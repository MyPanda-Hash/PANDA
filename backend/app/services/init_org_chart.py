"""组织架构图 → 部门树草案（系统初始化向导用）。

只做一件事：把一张组织架构图交给**视觉模型**，换回结构化的部门行。
不落库、不写 MES——MES 拿到草案后由人核对、再走它自己的初始化落库，
与「AI 不直连数据库」的边界一致。

数据边界：图片无法脱敏，云端端点默认会被 `media_egress_allowed` 拦截；
没配本机视觉模型时明确报错，而不是偷偷把客户的组织架构图发出去。
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.services.data_guard import media_egress_allowed
from app.services.model_routing import ResolvedModelTarget, resolve_vision_endpoint
from app.services.plain_llm import LlmUnavailable, _resolve_secret, chat_completions_url

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 12 * 1024 * 1024
ALLOWED_MEDIA = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/webp": "webp",
}
TIMEOUT_SECONDS = 120.0
MAX_DEPTS = 200

SYSTEM_PROMPT = """你是制造业 MES 实施顾问，负责把组织架构图读成部门树。

只输出一个 JSON 对象，不要任何解释文字、不要 Markdown 代码块：
{
  "depts": [
    {"code": "D01", "name": "生产部", "parentCode": "", "sort": 10, "positions": ["车间主任", "班组长"]}
  ],
  "warnings": ["无法辨认的内容：..."],
  "notes": "整体判断依据，一句话"
}

规则：
1. 只写图上真实存在的部门/岗位，看不清就写进 warnings，不要编造。
   人员/职务节点（如「总经理」「厂长」「张三」）不是部门：不要放进 depts，
   它们下面挂的部门直接作为顶层（parentCode 留空）。
2. parentCode 填**上级部门的 code**；顶层部门留空字符串。不能出现环。
3. code 用 D01、D02 递增；name 用图上的原始中文名称，不要改写、不要加简称。
4. sort 按图上从左到右、从上到下的顺序，10、20、30 递增。
5. 虚线汇报、兼任关系只在 warnings 里说明，不要拆成额外部门。
6. 一个部门下有多个框时按并列关系处理（同一 parentCode）。
"""


class OrgChartError(RuntimeError):
    """组织架构图解析失败（可把 code 直接回给调用方）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def build_messages(image_bytes: bytes, media_type: str, extra_hint: str = "", *, hint: str = "") -> list[dict[str, Any]]:
    """构造视觉模型请求：系统提示 + 一张图（data URL）。"""
    mime = (media_type or "").split(";")[0].strip().lower()
    if mime not in ALLOWED_MEDIA:
        raise OrgChartError("unsupported_media", "只支持 PNG / JPEG / WebP 图片")
    if not image_bytes:
        raise OrgChartError("empty_image", "图片内容为空")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise OrgChartError("image_too_large", "图片超过 12MB，请压缩后再上传")
    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    note = (extra_hint or hint).strip()
    suffix = f"\n补充说明（来自用户）：{note}" if note else ""
    return [
        {"role": "system", "content": SYSTEM_PROMPT + suffix},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请按约定 JSON 输出这张组织架构图的部门树。"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]


def parse_reply(text: str) -> dict[str, Any]:
    """把模型回复解析成部门行；容忍代码块、前后废话与重复 code。"""
    raw = (text or "").strip()
    if not raw:
        raise OrgChartError("empty_reply", "模型没有返回内容")
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.MULTILINE).strip()
    if not raw.startswith("{"):
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise OrgChartError("bad_reply", "模型没有返回 JSON 对象")
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OrgChartError("bad_reply", f"模型返回的 JSON 无法解析：{exc.msg}") from exc

    depts_in = data.get("depts")
    if not isinstance(depts_in, list) or not depts_in:
        snippet = _snippet(text)
        if any(word in snippet for word in ("无法", "不能", "看不到", "没有图片", "未提供图片", "cannot", "can't", "unable")):
            raise OrgChartError(
                "vision_blind",
                f"当前模型看不到图片（它回复：{snippet}），请换一个真正的视觉模型端点",
            )
        raise OrgChartError(
            "no_depts",
            f"图里没有识别出部门（模型回复：{snippet}）。请换一张更清晰的图，或改用 Excel 模板",
        )

    depts: list[dict[str, Any]] = []
    warnings: list[str] = [str(w) for w in (data.get("warnings") or []) if str(w).strip()]
    seen: set[str] = set()
    for index, item in enumerate(depts_in[:MAX_DEPTS]):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            warnings.append(f"第 {index + 1} 个部门没有名称，已跳过")
            continue
        code = str(item.get("code") or "").strip() or f"D{index + 1:02d}"
        if code in seen:
            code = f"{code}-{index + 1}"
            warnings.append(f"部门编码重复，已改为 {code}")
        seen.add(code)
        try:
            sort = int(float(item.get("sort") or (index + 1) * 10))
        except (TypeError, ValueError):
            sort = (index + 1) * 10
        depts.append(
            {
                "code": code,
                "name": name,
                "parentCode": str(item.get("parentCode") or "").strip(),
                "sort": sort,
                "positions": [str(p).strip() for p in (item.get("positions") or []) if str(p).strip()],
            }
        )

    # 父编码必须存在，否则挂根（并提示），避免 MES 落库时出现孤儿子部门
    codes = {d["code"] for d in depts}
    for dept in depts:
        parent = dept["parentCode"]
        if parent and parent not in codes:
            warnings.append(f"「{dept['name']}」的上级编码 {parent} 不存在，已按顶层处理")
            dept["parentCode"] = ""
    warnings.extend(_cycle_warnings(depts))

    return {
        "depts": depts,
        "warnings": warnings,
        "notes": str(data.get("notes") or "").strip(),
    }


def _snippet(text: str, limit: int = 80) -> str:
    """把模型原话压成一行短句，便于把「为什么失败」直接回给操作员。"""
    flat = " ".join((text or "").split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def _cycle_warnings(depts: list[dict[str, Any]]) -> list[str]:
    """父子关系成环会让部门树不可用，这里提前挑出来并从根断开。"""
    parent_of = {d["code"]: d["parentCode"] for d in depts}
    by_code = {d["code"]: d for d in depts}
    warnings: list[str] = []
    for code in list(parent_of):
        seen: list[str] = []
        cursor = code
        while cursor:
            if cursor in seen:
                by_code[code]["parentCode"] = ""
                warnings.append(f"「{by_code[code]['name']}」的上级关系成环，已按顶层处理")
                break
            seen.append(cursor)
            cursor = parent_of.get(cursor, "")
    return warnings


def _payload(messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 3000,
        "stream": False,
        "response_format": {"type": "json_object"},
    }


async def parse_org_chart(
    db: Session,
    image_bytes: bytes,
    media_type: str,
    *,
    extra_hint: str = "",
    profile_id: str | None = None,
) -> dict[str, Any]:
    """调用视觉模型解析组织架构图，返回 {depts, warnings, notes, model}。"""
    target: ResolvedModelTarget | None = resolve_vision_endpoint(
        db, data_level="public", configured_profile_id=profile_id
    )
    if target is None:
        raise OrgChartError(
            "no_vision_model",
            "没有可用的视觉模型：请在数字员工的「模型端点」里配一个支持图片输入的模型",
        )
    if not media_egress_allowed(target.endpoint_url):
        raise OrgChartError(
            "vision_cloud_blocked",
            "图片要发往云端模型，按数据策略已拦截：可配本机视觉模型，"
            "或显式设置 DOC_NEXUS_DATA_MASK_MEDIA_POLICY=allow 放行",
        )

    messages = build_messages(image_bytes, media_type, extra_hint)
    url = chat_completions_url(target.endpoint_url)
    headers = {"Content-Type": "application/json"}
    secret = _resolve_secret(getattr(target.endpoint, "secret_ref", None))
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=_payload(messages, target.model), headers=headers)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "init_org_chart.http_error",
            extra={"status": exc.response.status_code, "url": url.split("?")[0]},
        )
        raise OrgChartError("model_http_error", f"模型端点返回 {exc.response.status_code}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise OrgChartError("model_unreachable", f"模型调用失败：{exc}") from exc

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OrgChartError("bad_reply", "模型返回结构不符合 OpenAI 兼容格式") from exc
    if not isinstance(text, str) or not text.strip():
        # 端点登记成 supports_vision=1 不等于模型真能看：实测有的对话模型收到图片仍返回空，
        # 这时报「配置的模型看不到图片」比报 JSON 解析失败有用得多。
        raise OrgChartError(
            "vision_blind",
            f"模型 {target.model} 没有返回内容，可能看不到图片：请换一个真正的视觉模型端点",
        )

    parsed = parse_reply(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False))
    parsed["model"] = target.model
    parsed["endpoint"] = target.endpoint.provider
    return parsed