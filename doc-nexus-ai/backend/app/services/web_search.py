"""联网检索：给对话补上「查外部资料」的能力。

两个通道，各有分工：

- **百炼搜索增强**（主）：阿里云 qwen + enable_search，直接返回带来源的结论。
  实测能给出具体数字、机构名、时间，还有交叉验证，信息密度最高。
- **360 / 百度抓取**（兜底）：拿标题/摘要/链接列表。360 稳定，百度限流极紧。

为什么不用 DeepSeek 的 key：它的 API 没有搜索能力——没有内置搜索工具
（tools 里给 type=web_search 直接 422），enable_search 参数会被静默忽略，
联网只在官方 App/网页版里有（2026-11-17 实测）。

已排除的源：DuckDuckGo 直接 202；Bing 国际站走代理能返回但内容是反爬降级后的
无关结果（搜「装修」返回「ANSYS」）；SearXNG 公共实例超时。
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 12.0
MAX_RESULTS = 6
COOLDOWN_SECONDS = 180.0
CACHE_SECONDS = 300.0
# 检索超时：AnySearch 正常 3s、慢时 11s；超过 20s 就该换源，而不是让用户等。
# 百炼走模型生成，天然更慢，给 45s。
ANYSEARCH_TIMEOUT_SECONDS = 20.0
BAILIAN_TIMEOUT_SECONDS = 45.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

_CLIENT: httpx.Client | None = None
_COOLDOWN: dict[str, float] = {}
_CACHE: dict[str, tuple[float, Any]] = {}


class WebSearchError(RuntimeError):
    """检索失败（网络、反爬或全部源不可用）。"""


def _client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
            # 这几个源在本机直连可达；走系统代理反而被拦
            trust_env=False,
        )
    return _CLIENT


def _clean(value: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", value or "", flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _search_360(keyword: str, limit: int) -> list[dict[str, Any]]:
    response = _client().get("https://www.so.com/s", params={"q": keyword})
    response.raise_for_status()
    body = response.text
    if len(body) < 20000:
        raise WebSearchError("360 返回异常（疑似被拦截）")

    # 每条结果是一个 <li class="res-list">
    blocks = re.findall(r'<li class="res-list.*?</li>', body, re.S)
    results: list[dict[str, Any]] = []
    for block in blocks:
        title_match = re.search(r'<h3[^>]*>(.*?)</h3>', block, re.S)
        if not title_match:
            continue
        link_match = re.search(r'<h3[^>]*>.*?<a[^>]*href="([^"]+)"', block, re.S)
        title = _clean(title_match.group(1))
        if not title or title in ("其他人还搜了", "相关搜索"):
            continue
        tail = block[title_match.end():]
        summary = _clean(tail)[:220]
        results.append({
            "title": title[:120],
            "url": link_match.group(1) if link_match else "",
            "summary": summary,
        })
        if len(results) >= min(limit, MAX_RESULTS):
            break
    return results


def _search_baidu(keyword: str, limit: int) -> list[dict[str, Any]]:
    """备用源。限流很紧，命中缓存或 360 失败时才走这里。"""
    client = _client()
    try:
        client.get("https://www.baidu.com/")
    except httpx.HTTPError:
        pass
    response = client.get("https://www.baidu.com/s", params={"wd": keyword, "rn": 10})
    response.raise_for_status()
    body = response.text
    if len(body) < 5000:
        raise WebSearchError("百度被限流")

    results: list[dict[str, Any]] = []
    for match in re.finditer(
        r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.S
    ):
        title = _clean(match.group(2))
        if not title:
            continue
        tail = body[match.end(): match.end() + 3000]
        summary_match = re.search(
            r'class="[^"]*(?:c-abstract|content-right_|summary-text|abstract)[^"]*"[^>]*>(.*?)</(?:div|span)>',
            tail,
            re.S,
        )
        results.append({
            "title": title[:120],
            "url": match.group(1),
            "summary": _clean(summary_match.group(1))[:220] if summary_match else _clean(tail)[:220],
        })
        if len(results) >= min(limit, MAX_RESULTS):
            break
    return results




def _anysearch_rpc(method: str, params: dict[str, Any] | None = None, *, call_id: int = 1) -> dict[str, Any]:
    """AnySearch 走 MCP JSON-RPC（不是 REST），返回解析后的 result。"""
    key = (settings.anysearch_api_key or "").strip()
    if not key:
        raise WebSearchError("没有配置 AnySearch key（ANYSEARCH_API_KEY）")

    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": call_id, "method": method}
    if params is not None:
        payload["params"] = params

    response = httpx.post(
        settings.anysearch_api_url,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "X-Anysearch-Client": "dsh/1.0",
        },
        json=payload,
        timeout=ANYSEARCH_TIMEOUT_SECONDS,
        follow_redirects=True,
    )
    if response.status_code >= 400:
        raise WebSearchError("AnySearch 返回 " + str(response.status_code) + "：" + response.text[:140])

    text = response.text.strip()
    # 服务端可能走 SSE：最后一条 data: 才是结果
    if "data:" in text[:400]:
        parsed = None
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    parsed = json.loads(line[5:].strip())
                except ValueError:
                    continue
        if parsed is None:
            raise WebSearchError("AnySearch 返回了无法解析的 SSE")
        body = parsed
    else:
        try:
            body = json.loads(text)
        except ValueError as exc:
            raise WebSearchError("AnySearch 返回了非 JSON 内容") from exc

    if body.get("error"):
        raise WebSearchError("AnySearch 报错：" + str(body["error"])[:140])
    return body.get("result") or {}


def _search_anysearch(keyword: str, limit: int) -> list[dict[str, Any]]:
    """AnySearch 搜索，返回结构化结果。"""
    # 每次请求前先握手：MCP 是有状态的，直接 tools/call 会被拒
    _anysearch_rpc(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "dsh", "version": "1.0"},
        },
    )
    result = _anysearch_rpc(
        "tools/call",
        {"name": "search", "arguments": {"query": keyword, "max_results": min(limit, MAX_RESULTS)}},
        call_id=2,
    )

    blocks = result.get("content") or []
    text = "".join(str(b.get("text") or "") for b in blocks if isinstance(b, dict))
    if not text.strip():
        raise WebSearchError("AnySearch 没有返回内容")

    # 返回是 Markdown：「### 1. 标题 / - **URL**: … / - 摘要… / date: …」
    rows: list[dict[str, Any]] = []
    for chunk in re.split(r"^###\s+", text, flags=re.M)[1:]:
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        title = re.sub(r"^\d+[.、]\s*", "", lines[0]).strip()
        url = ""
        summary_parts: list[str] = []
        for line in lines[1:]:
            cleaned = line.lstrip("- ").strip()
            if cleaned.lower().startswith("**url**") or cleaned.lower().startswith("url:"):
                url = cleaned.split(":", 1)[-1].strip().strip("*").strip()
            elif cleaned.lower().startswith("date:"):
                summary_parts.append("（" + cleaned[5:].strip() + "）")
            else:
                summary_parts.append(cleaned.replace("**", ""))
        rows.append({
            "title": title[:140],
            "url": url,
            "summary": " ".join(summary_parts)[:260],
        })
    if not rows:
        # 结构不认识也把原文给模型，总比丢掉强
        rows = [{"title": "AnySearch 检索结果", "url": "", "summary": text[:600]}]
    return rows

def _search_bailian(keyword: str) -> str:
    """阿里云百炼搜索增强：直接返回带来源的结论。"""
    key = (settings.search_api_key or "").strip()
    if not key:
        raise WebSearchError("没有配置搜索 key（SEARCH_API_KEY 或 LOCAL_VLM_API_KEY）")

    payload = {
        "model": settings.search_api_model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "联网检索并回答：" + keyword + chr(10) + chr(10)
                        + "要求：给具体数字、机构名和时间；每条数据后面标来源；"
                        + "找不到就直说没有，不要编。"
                    ),
                }
            ]
        },
        "parameters": {
            "max_tokens": 900,
            "enable_search": True,
            "search_options": {"forced_search": True, "search_strategy": "pro"},
        },
    }
    response = httpx.post(
        settings.search_api_url,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        json=payload,
        timeout=BAILIAN_TIMEOUT_SECONDS,
        trust_env=False,
    )
    if response.status_code >= 400:
        raise WebSearchError("百炼检索返回 " + str(response.status_code) + "：" + response.text[:120])
    try:
        body = response.json()
    except ValueError as exc:
        raise WebSearchError("百炼检索返回了非 JSON 内容") from exc

    text = str((body.get("output") or {}).get("text") or "").strip()
    if not text:
        raise WebSearchError("百炼检索没有返回内容")
    return text


def answer(query: str) -> str:
    """检索并回答。优先走百炼（信息密度最高），失败再退回抓取。"""
    keyword = (query or "").strip()
    if not keyword:
        return ""

    now = time.monotonic()
    cache_key = "answer::" + keyword
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]

    # 先用 AnySearch：结构化、带 URL 和日期，信息最完整
    try:
        rows = _search_anysearch(keyword, 6)
        text = format_results(keyword, rows)
        _CACHE[cache_key] = (now, text)
        return text
    except Exception as exc:  # noqa: BLE001
        logger.warning("anysearch failed: %s", exc)

    # 再退到百炼的搜索增强（模型总结 + 来源）
    try:
        text = _search_bailian(keyword)
        _CACHE[cache_key] = (now, text)
        return text
    except Exception as exc:  # noqa: BLE001
        logger.warning("bailian search failed: %s", exc)

    # 兜底：抓取列表拼成文本
    rows = search(keyword, limit=5)
    return format_results(keyword, rows)

# 源顺序 = 优先级。AnySearch 最快最准（带 URL + 日期），其余是兜底。
SOURCES = (
    ("anysearch", _search_anysearch),
    ("360", _search_360),
    ("baidu", _search_baidu),
)

def search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """检索公开网页，返回 [{title, url, summary}]。按源依次尝试。"""
    keyword = (query or "").strip()
    if not keyword:
        return []

    now = time.monotonic()
    cached = _CACHE.get(keyword)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]

    errors: list[str] = []
    for name, runner in SOURCES:
        if now < _COOLDOWN.get(name, 0.0):
            continue
        try:
            rows = runner(keyword, limit)
        except Exception as exc:  # noqa: BLE001
            _COOLDOWN[name] = now + COOLDOWN_SECONDS
            errors.append(name + "：" + str(exc)[:60])
            logger.warning("web_search source failed: %s %s", name, exc)
            continue
        if rows:
            _CACHE[keyword] = (now, rows)
            return rows
        errors.append(name + "：没有结果")

    raise WebSearchError("联网检索暂时不可用（" + "；".join(errors) + "）")


def format_results(query: str, results: list[dict[str, Any]]) -> str:
    """给模型看的纯文本。"""
    if not results:
        return "没有检索到「" + query + "」的相关网页。"
    lines = ["联网检索「" + query + "」，前 " + str(len(results)) + " 条："]
    for index, row in enumerate(results, start=1):
        lines.append(str(index) + ". " + row["title"])
        if row.get("summary"):
            lines.append("   " + row["summary"])
        if row.get("url"):
            lines.append("   " + row["url"])
    lines.append("（以上为网页检索摘要，引用时请说明来源）")
    return chr(10).join(lines)