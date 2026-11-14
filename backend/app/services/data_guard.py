"""云端模型出口的数据脱敏守卫。

背景：``doc-nexus-ai`` 有 5 条会把用户内容送到模型端点的路径
（聊天主链路、会话标题、个人记忆抽取、向量化、图片理解）。当端点是公网云模型时，
这些内容会离开本机，可能带出客户名、手机号、身份证、金额等敏感信息。

本模块提供唯一的脱敏实现，供各出口调用：

* :func:`is_cloud_endpoint` —— 按地址判定「云端」还是「本机/内网」
* :func:`session_for_endpoint` —— 按策略给出本次请求的 :class:`MaskSession`
* ``MaskSession.mask_payload`` / ``mask_text`` / ``restore_text`` —— 脱敏与还原
* :func:`anonymize_text` —— 向量化专用：无序号、可重复（保证检索语义稳定）
* :func:`media_egress_allowed` —— 图片/音频这类无法脱敏的出口的放行判定

策略全部走环境变量，默认值即安全值：

=============== ============================================================
变量             作用
=============== ============================================================
``DOC_NEXUS_DATA_MASK_MODE``     ``mask``（默认）/ ``block`` / ``off``
``DOC_NEXUS_DATA_MASK_TERMS``    额外业务词表，逗号分隔，命中即整体替换
``DOC_NEXUS_DATA_MASK_TRUSTED_HOSTS``  内网域名白名单（公网域名托管的内网网关）
``DOC_NEXUS_DATA_MASK_MEDIA_POLICY``   ``block``（默认）/ ``allow``
=============== ============================================================

失败处理遵循 fail-closed：脱敏过程中抛异常时调用方必须不发请求，
而不是「脱敏失败就发原文」。
"""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

MODE_OFF = "off"
MODE_MASK = "mask"
MODE_BLOCK = "block"

MEDIA_BLOCK = "block"
MEDIA_ALLOW = "allow"

#: 占位符形态：〔KIND_1〕。用全角括号，避免和模型常见的 Markdown/代码符号冲突。
PLACEHOLDER_OPEN = "〔"
PLACEHOLDER_CLOSE = "〕"
_PLACEHOLDER_RE = re.compile(r"〔([A-Z_]+)_(\d+)〕")


class CloudEgressBlocked(RuntimeError):
    """策略为 block 时，检测到敏感内容而拒绝发往云端。"""


@dataclass(frozen=True, slots=True)
class _Rule:
    name: str
    pattern: re.Pattern[str]


# 顺序有意义：先长后短，避免银行卡被手机号规则切碎。
_RULES: tuple[_Rule, ...] = (
    _Rule(
        "ID_CARD",
        re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])"),
    ),
    _Rule(
        "BANK_CARD",
        re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
    ),
    _Rule(
        "PHONE",
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    ),
    _Rule(
        "EMAIL",
        re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    ),
    _Rule(
        # 业务金额/单价：必须带「元/万/块/%」或货币符号才算，
        # 否则订单号、数量、工时也会被当成金额误伤。
        "MONEY",
        re.compile(
            r"(?:[¥￥]\s?\d[\d,]*(?:\.\d+)?"
            r"|\d[\d,]*(?:\.\d+)?\s?(?:元|万元|万|块|%))"
        ),
    ),
)

#: 键名命中即整值脱敏。宁可多脱，也不要漏。
_SENSITIVE_KEY = re.compile(
    r"(客户|供应商|往来单位|联系人|收货人|负责人|经手人|业务员|"
    r"电话|手机|传真|邮箱|地址|开户行|行号|账号|税号|身份证|"
    r"单价|价格|金额|成本|工资|底薪|计件|底价|报价|毛利|利润|"
    r"应收|应付|余额|折扣|优惠)"
)

_MAX_TERMS = 200


def _env(name: str, default: str = "") -> str:
    value = (os.getenv(name) or "").strip()
    if value:
        return value
    # .env 由 pydantic 读入 settings，不会进 os.environ（本地脚本不 export），
    # 这里回退查一次，避免「明明在 .env 里配了却不生效」。
    if name == "DOC_NEXUS_DATA_MASK_MEDIA_POLICY":
        try:
            from app.core.config import settings

            configured = (getattr(settings, "data_mask_media_policy", "") or "").strip()
            if configured:
                return configured
        except Exception:  # 配置层异常不能影响脱敏判定，按默认拦截
            pass
    return default


def _mode() -> str:
    value = _env("DOC_NEXUS_DATA_MASK_MODE", MODE_MASK).lower()
    return value if value in {MODE_OFF, MODE_MASK, MODE_BLOCK} else MODE_MASK


def _trusted_hosts() -> set[str]:
    raw = _env("DOC_NEXUS_DATA_MASK_TRUSTED_HOSTS")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _extra_terms() -> tuple[str, ...]:
    raw = _env("DOC_NEXUS_DATA_MASK_TERMS")
    terms = [item.strip() for item in raw.split(",") if item.strip()]
    # 长的先替换，避免「华东铝业」被「华东」截断
    terms.sort(key=len, reverse=True)
    return tuple(terms[:_MAX_TERMS])


def host_of(base_url: object) -> str:
    raw = str(base_url or "").strip()
    if not raw:
        return ""
    try:
        return (urlsplit(raw).hostname or "").lower()
    except ValueError:
        return ""


def is_cloud_endpoint(base_url: object) -> bool:
    """端点是否在公网云端。

    判定顺序：本机/回环 → 私网 IP → 内网后缀 → 白名单 → 其余按公网处理。
    域名默认按公网处理（宁可多脱敏），内网域名靠白名单显式声明。
    """
    host = host_of(base_url)
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        return not (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        )
    if host.endswith((".local", ".internal", ".lan", ".localdomain")):
        return False
    if host in _trusted_hosts():
        return False
    return True


def media_egress_allowed(base_url: object) -> bool:
    """图片/音频无法脱敏：云端默认拦截，需显式 allow。"""
    if not is_cloud_endpoint(base_url):
        return True
    return _env("DOC_NEXUS_DATA_MASK_MEDIA_POLICY", MEDIA_BLOCK).lower() == MEDIA_ALLOW


@dataclass
class MaskSession:
    """一次请求内的脱敏会话：负责占位符编号与还原映射。"""

    endpoint: str
    mode: str = MODE_MASK
    enabled: bool = True
    hits: dict[str, int] = field(default_factory=dict)
    _value_to_token: dict[str, str] = field(default_factory=dict)
    _token_to_value: dict[str, str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    # ---------- 基础 ----------

    @property
    def masked(self) -> bool:
        return self.enabled and self.mode != MODE_OFF

    def _record(self, kind: str) -> None:
        self.hits[kind] = self.hits.get(kind, 0) + 1

    def _token_for(self, kind: str, value: str) -> str:
        existing = self._value_to_token.get(value)
        if existing:
            return existing
        index = self._counters.get(kind, 0) + 1
        self._counters[kind] = index
        token = f"{PLACEHOLDER_OPEN}{kind}_{index}{PLACEHOLDER_CLOSE}"
        self._value_to_token[value] = token
        self._token_to_value[token] = value
        return token

    # ---------- 脱敏 ----------

    def mask_text(self, text: str) -> str:
        """掩掉一段文本里的 PII 与业务词，同一原文得到同一占位符。"""
        if not isinstance(text, str) or not text or not self.masked:
            return text
        result = text
        for rule in _RULES:
            def _sub(match: re.Match[str], kind: str = rule.name) -> str:
                value = match.group(0)
                self._record(kind)
                return self._token_for(kind, value)

            result = rule.pattern.sub(_sub, result)
        for term in _extra_terms():
            if term and term in result:
                count = result.count(term)
                for _ in range(count):
                    self._record("TERM")
                result = result.replace(term, self._token_for("TERM", term))
        return result

    def mask_value(self, value: Any) -> Any:
        """键名敏感时整值替换；非字符串原样返回（数值也换，避免金额泄露）。"""
        if not self.masked:
            return value
        if isinstance(value, bool) or value is None:
            return value
        self._record("FIELD")
        return self._token_for("FIELD", str(value))

    def mask_payload(self, payload: Any) -> Any:
        """深拷贝式脱敏：返回新对象，不改动入参。"""
        if not self.masked:
            return payload
        masked = self._walk(payload)
        if self.mode == MODE_BLOCK and self.hits:
            raise CloudEgressBlocked(
                "检测到敏感数据，按策略禁止发送到云端模型（可切换到本机/内网模型）"
            )
        return masked

    def _walk(self, node: Any) -> Any:
        if isinstance(node, str):
            return self.mask_text(node)
        if isinstance(node, list):
            return [self._walk(item) for item in node]
        if isinstance(node, tuple):
            return tuple(self._walk(item) for item in node)
        if isinstance(node, dict):
            out: dict[Any, Any] = {}
            for key, value in node.items():
                if isinstance(key, str) and _SENSITIVE_KEY.search(key) and not isinstance(
                    value, (dict, list, tuple)
                ):
                    out[key] = self.mask_value(value)
                else:
                    out[key] = self._walk(value)
            return out
        return node

    # ---------- 还原 ----------

    def restore_text(self, text: str) -> str:
        """把模型回答里的占位符换回真实值。未知占位符原样保留。"""
        if not isinstance(text, str) or not text or not self._token_to_value:
            return text
        return _PLACEHOLDER_RE.sub(
            lambda m: self._token_to_value.get(m.group(0), m.group(0)),
            text,
        )


def session_for_endpoint(base_url: object, *, mode: str | None = None) -> MaskSession:
    """按端点与全局策略给出会话。本机/内网端点默认关闭（不改动行为）。"""
    endpoint = str(base_url or "")
    resolved_mode = (mode or _mode()).lower()
    if resolved_mode not in {MODE_OFF, MODE_MASK, MODE_BLOCK}:
        resolved_mode = MODE_MASK
    cloud = is_cloud_endpoint(endpoint)
    return MaskSession(
        endpoint=endpoint,
        mode=resolved_mode,
        enabled=cloud and resolved_mode != MODE_OFF,
    )


def anonymize_text(text: str) -> str:
    """向量化专用：无序号、可重复，保证同一段文本每次得到相同结果。

    带序号会让不同批次的同一段文本产生不同向量，检索会漂移。
    """
    if not isinstance(text, str) or not text:
        return text
    result = text
    for rule in _RULES:
        result = rule.pattern.sub(f"{PLACEHOLDER_OPEN}{rule.name}{PLACEHOLDER_CLOSE}", result)
    for term in _extra_terms():
        if term:
            result = result.replace(term, f"{PLACEHOLDER_OPEN}TERM{PLACEHOLDER_CLOSE}")
    return result
