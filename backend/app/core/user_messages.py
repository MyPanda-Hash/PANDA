"""User-facing error text. Stack traces and errno details belong in logs only."""

from __future__ import annotations

import json
import re

_UNSAFE_MARKERS = (
    "traceback",
    "errno",
    "[errno",
    "object-key",
    "/users/",
    "minio",
    "connection refused",
    'file "',
    "site-packages",
    "signaturedoesnotmatch",
    "exception",
    "httpx",
    "sqlalchemy",
    "psycopg",
    "operationalerror",
    "stack",
    "/app/backend",
    "chunks failed",
    "failed after",
)

_EXCEPTION_NAME = re.compile(r"^[A-Za-z]+(?:Error|Exception|Warning)$")
_FILE_LINE = re.compile(r'File ".+", line \d+')
_REDACTED = "[REDACTED]"

_PEM_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^\r\n-]*PRIVATE KEY-----.*?-----END [^\r\n-]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_PEM_PRIVATE_KEY_START_RE = re.compile(
    r"-----BEGIN [^\r\n-]*PRIVATE KEY-----.*\Z",
    re.IGNORECASE | re.DOTALL,
)
_URL_USERINFO_RE = re.compile(
    r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)[^\s/?#@]+@",
    re.IGNORECASE,
)
_QUERY_PARAMETER_RE = re.compile(
    r"""(?ix)
    (?P<prefix>[?&;]\s*)
    (?P<key>[a-z][a-z0-9_.-]*)
    (?P<separator>\s*=\s*)
    (?P<value>\[REDACTED\]|"[^"]*"|'[^']*'|[^\s&;,]+)
    """
)
_AUTH_HEADER_RE = re.compile(
    r"(?im)(?<![a-z0-9_.-])"
    r"(?P<key>authorization|proxy-authorization|cookie|set-cookie)"
    r"(?P<separator>\s*:\s*)[^\r\n]*"
)
_ASSIGNMENT_KEY_RE = re.compile(
    r"""(?ix)
    (?<![a-z0-9_.])
    (?P<key>[a-z_][a-z0-9_.-]*)
    (?P<key_quote>["']?)
    (?P<separator>\s*[:=]\s*)
    """
)
_CLI_FLAG_RE = re.compile(
    r"""(?ix)
    (?<![a-z0-9_.-])
    --(?P<key>[a-z_][a-z0-9_.-]*)
    (?P<separator>[ \t]*[=:][ \t]*|[ \t]+)
    (?P<value>"[^"]*"|'[^']*'|[^ \t\r\n]+)
    """
)
_BEARER_SECRET_RE = re.compile(r"(?i)\bBearer\s+[^\s,;&]+")

_NON_SECRET_KEY_NAMES = frozenset(
    {
        "session_id",
        "sessionid",
        "token_usage",
        "token_count",
        "token_counts",
        "token_type",
        "token_kind",
        "token_name",
        "prompt_tokens",
        "completion_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "max_tokens",
        "token_budget",
        "token_limit",
        "tokens_used",
        "tokens_remaining",
        "usage_tokens",
        "token_stats",
        "token_metrics",
        "token_statistics",
    }
)
_SECRET_KEY_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "passwd",
        "secret",
        "token",
        "cookie",
        "set_cookie",
        "credential",
        "credentials",
        "access_key",
        "access_key_id",
        "aws_access_key_id",
        "aws_secret_access_key",
        "private_key",
        "client_secret",
        "refresh_token",
        "access_token",
        "session_token",
        "id_token",
        "bearer_token",
        "csrf_token",
        "signature",
        "sig",
        "x_amz_signature",
        "x_amz_credential",
        "x_amz_security_token",
        "oauth_signature",
        "x_goog_signature",
        "x_oss_signature",
    }
)
_SECRET_COMPACT_KEY_NAMES = frozenset(
    {
        "setcookie",
        "accesskey",
        "accesskeyid",
        "awsaccesskeyid",
        "awssecretaccesskey",
        "privatekey",
        "clientsecret",
        "refreshtoken",
        "accesstoken",
        "sessiontoken",
        "idtoken",
        "bearertoken",
        "csrftoken",
        "xamzsignature",
        "xamzcredential",
        "xamzsecuritytoken",
        "oauthsignature",
        "xgoogsignature",
        "xosssignature",
    }
)


def _normalize_secret_key(key: object) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key).strip())
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def is_sensitive_key(key: object) -> bool:
    """Return whether a structured field is likely to contain a credential."""
    normalized = _normalize_secret_key(key)
    if not normalized or normalized in _NON_SECRET_KEY_NAMES:
        return False
    compact = normalized.replace("_", "")
    if normalized in _SECRET_KEY_NAMES or compact in _SECRET_COMPACT_KEY_NAMES:
        return True
    if any(
        marker in normalized
        for marker in ("api_key", "access_key", "private_key")
    ) or "apikey" in compact:
        return True
    if any(
        marker in normalized
        for marker in (
            "password",
            "passwd",
            "secret",
            "credential",
            "cookie",
            "authorization",
        )
    ):
        return True
    if normalized == "signature" or normalized.endswith("_signature"):
        return True
    if normalized.endswith("_id"):
        return False
    if normalized == "session" or normalized.startswith("session_"):
        return normalized.endswith("_token")
    if normalized == "token" or normalized.endswith("_token"):
        return True
    if normalized.startswith("token_"):
        return True
    return False


_ASSIGNMENT_VALUE_BOUNDARIES = frozenset(" \t\r\n,;&")
_ASSIGNMENT_DELIMITERS = {"[": "]", "{": "}", "(": ")"}


def _is_assignment_value_boundary(value: str, index: int) -> bool:
    if value[index] in _ASSIGNMENT_VALUE_BOUNDARIES:
        return True
    return value[index] == "\\" and value[index + 1 : index + 2].lower() in {
        "n",
        "r",
        "t",
    }


def _scan_quoted_value(value: str, start: int, quote: str) -> int:
    index = start + 1
    line_end = value.find("\n", index)
    limit = len(value) if line_end < 0 else line_end
    while index < limit:
        if value[index] == "\\":
            index += 2
            continue
        if value[index] == quote:
            return index + 1
        index += 1
    return limit


def _scan_structured_value(value: str, start: int) -> int:
    closing = _ASSIGNMENT_DELIMITERS.get(value[start])
    if closing is None:
        return start
    stack = [closing]
    quote: str | None = None
    index = start + 1
    line_end = value.find("\n", index)
    limit = len(value) if line_end < 0 else line_end
    while index < limit:
        char = value[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
        elif char in _ASSIGNMENT_DELIMITERS:
            stack.append(_ASSIGNMENT_DELIMITERS[char])
        elif char in _ASSIGNMENT_DELIMITERS.values():
            if stack and char == stack[-1]:
                stack.pop()
                if not stack:
                    return index + 1
        index += 1
    return limit


def _assignment_value_end(value: str, start: int) -> int:
    if start >= len(value):
        return start
    quoted = value[start] in {'"', "'"}
    if quoted:
        end = _scan_quoted_value(value, start, value[start])
    elif value[start] in _ASSIGNMENT_DELIMITERS:
        end = _scan_structured_value(value, start)
    else:
        end = start
        while end < len(value) and not _is_assignment_value_boundary(value, end):
            end += 1
        if value[start : start + 6].lower() == "bearer" and (
            end == start + 6 or value[start + 6].isspace()
        ):
            token_start = start + 6
            while token_start < len(value) and value[token_start].isspace():
                token_start += 1
            token_end = token_start
            while (
                token_end < len(value)
                and not _is_assignment_value_boundary(value, token_end)
            ):
                token_end += 1
            end = token_end
    if not quoted:
        while end < len(value) and not _is_assignment_value_boundary(value, end):
            end += 1
    return end


def _replace_assignments(value: str) -> str:
    pieces: list[str] = []
    cursor = 0
    for match in _ASSIGNMENT_KEY_RE.finditer(value):
        if match.start() < cursor or not is_sensitive_key(match.group("key")):
            continue
        end = _assignment_value_end(value, match.end())
        raw_value = value[match.end() : end]
        if raw_value.startswith(('"', "'")) and raw_value.endswith(raw_value[0]):
            replacement = f"{raw_value[0]}{_REDACTED}{raw_value[0]}"
        else:
            replacement = _REDACTED
        pieces.append(value[cursor : match.start()])
        pieces.append(
            f"{match.group('key')}{match.group('key_quote')}"
            f"{match.group('separator')}{replacement}"
        )
        cursor = end
    if not pieces:
        return value
    pieces.append(value[cursor:])
    return "".join(pieces)


def _replace_cli_flags(value: str) -> str:
    pieces: list[str] = []
    cursor = 0
    for match in _CLI_FLAG_RE.finditer(value):
        if match.start() < cursor or not is_sensitive_key(match.group("key")):
            continue
        raw_value = match.group("value")
        if not raw_value or (
            raw_value.startswith("--")
            and not raw_value.startswith(('"', "'"))
            and match.group("separator").strip() == ""
        ):
            continue
        if raw_value.startswith(('"', "'")) and raw_value.endswith(raw_value[0]):
            replacement = f"{raw_value[0]}{_REDACTED}{raw_value[0]}"
        else:
            replacement = _REDACTED
        pieces.append(value[cursor : match.start()])
        pieces.append(
            f"--{match.group('key')}{match.group('separator')}{replacement}"
        )
        cursor = match.end()
    if not pieces:
        return value
    pieces.append(value[cursor:])
    return "".join(pieces)


def _replace_query_parameter(match: re.Match[str]) -> str:
    key = match.group("key")
    if not is_sensitive_key(key):
        return match.group(0)
    raw_value = match.group("value")
    quote = (
        raw_value[0]
        if len(raw_value) >= 2
        and raw_value[0] in {'"', "'"}
        and raw_value[-1] == raw_value[0]
        else ""
    )
    return (
        f"{match.group('prefix')}{key}"
        f"{match.group('separator')}{quote}{_REDACTED}{quote}"
    )


def redact_inline_secrets(value: str) -> str:
    """Redact credentials in human-readable runtime and error text."""
    redacted = _PEM_PRIVATE_KEY_RE.sub(_REDACTED, value)
    redacted = _PEM_PRIVATE_KEY_START_RE.sub(_REDACTED, redacted)
    redacted = _AUTH_HEADER_RE.sub(
        lambda match: f"{match.group('key')}{match.group('separator')}{_REDACTED}",
        redacted,
    )
    redacted = _URL_USERINFO_RE.sub(
        lambda match: f"{match.group('scheme')}{_REDACTED}@", redacted
    )
    redacted = _QUERY_PARAMETER_RE.sub(_replace_query_parameter, redacted)
    redacted = _replace_cli_flags(redacted)
    redacted = _replace_assignments(redacted)
    return _BEARER_SECRET_RE.sub("Bearer [REDACTED]", redacted)


def redact_error_text(
    value: object,
    *,
    fallback: str = "",
    limit: int = 4_000,
) -> str:
    """Return bounded credential-safe text for persisted or streamed errors."""
    redacted = redact_inline_secrets(str(value or ""))
    if len(redacted) > limit:
        redacted = f"{redacted[:limit]}\n…(已截断)"
    return redacted or fallback


# 客户可见错误只允许来自此处的稳定文案；异常文本与供应商响应只进内部日志。
_TRUSTED_CHAT_ERROR_MESSAGES = {
    "quota_exhausted": "当前模型账户的可用额度不足，暂时无法继续。",
    "rate_limited": "模型平台当前请求较多，本轮已暂停。",
    "request_too_large": "本轮资料或上下文超出当前模型可处理范围。",
    "provider_unavailable": "模型服务暂时不可用，本轮已暂停。",
    "auth_invalid": "当前模型配置需要管理员处理，暂时无法继续。",
    "content_filtered": (
        "模型服务因内容安全策略拒绝了本次请求或输出，生成已停止。"
        "请调整请求内容后重新发送。"
    ),
    "model_failed": "模型服务未能完成本轮处理。",
    "model_unavailable": "本次选择的模型暂不可用，请更换模型后重试，或联系管理员检查模型配置。",
    "chat_request_conflict": "本次请求与已提交的记录不一致或仍在处理中，请先核对会话记录，不要重复提交。",
    "network_unavailable": "连接暂时不可用，请检查网络后重试。",
    "stream_interrupted": "连接已中断，回复尚未完成。请查看已有内容后再继续。",
    "session_expired": "登录已过期，请重新登录后继续。",
    "chat_endpoint_unavailable": "当前未配置可用的对话模型端点，请先完成模型配置。",
    "model_output_limit": (
        "模型在完成回答前达到输出长度限制，本轮已停止；"
        "已生成的内容和工具结果仍保留。"
    ),
    "chat_token_budget_exceeded": (
        "本轮累计 Token 用量已达到本轮上限，已停止继续调用模型；"
        "已生成的内容和文件仍保留。"
    ),
    "chat_request_budget_exceeded": (
        "本轮模型调用次数已达到上限，已停止继续调用模型；"
        "已生成的内容和文件仍保留。"
    ),
    "chat_token_usage_unavailable": (
        "模型未返回完整用量，无法确认本轮剩余 Token 预算，"
        "已停止继续调用；已生成的内容和文件仍保留。"
    ),
    "vision_endpoint_unavailable": (
        "图片暂时无法解析，请移除图片后重试，或切换支持识图的模型。"
    ),
    "vision_call_failed": "图片暂时无法解析，请稍后重试。",
    "vision_timeout": "图片识别超时，本轮未能读取图片内容。",
    "vision_budget_exceeded": "图片处理未能完成；请查看失败原因后重试。",
    "vision_output_invalid": "图片解析结果不可用，请稍后重试。",
    "vision_request_invalid": (
        "视觉请求参数或格式与模型不兼容，请联系管理员检查端点配置。"
    ),
    "vision_usage_invalid": "图片处理用量无法核验，本轮已停止。",
    "vision_unsupported_image": "当前视觉端点不支持所上传的图片。",
    "ocr_disabled": "图片暂时无法解析，请切换支持识图的模型。",
    "ocr_engine_unavailable": "本地 OCR 暂不可用，请切换支持识图的模型。",
    "ocr_page_timeout": "图片 OCR 超时，请稍后重试或切换支持识图的模型。",
    "ocr_no_text": "图片中未识别到可用文字，请切换支持识图的模型。",
    "ocr_image_invalid": "图片格式或内容不可解析，请重新上传。",
    "ocr_pixel_limit_exceeded": "图片尺寸超过 OCR 安全上限。",
    "ocr_output_limit_exceeded": "图片 OCR 结果超过上下文限制。",
    "runtime_unavailable": "聊天运行服务暂时不可用，请稍后重试。",
    "timeout": "本轮处理超时；已生成的内容和文件仍保留，请核对进度后重试。",
    "protocol_error": "聊天运行服务返回异常，请稍后重试。",
    "cancelled": "本轮处理已取消。",
    "permission_denied": "当前操作未获授权，请调整请求后重试。",
    "model_invalid": "当前模型配置不可用，请联系管理员处理。",
    "upstream_error": "本轮处理未能完成；已生成的内容和文件仍保留，请稍后重试。",
    "control_dispatch_failed": "操作未能提交，请稍后重试。",
    "pi_execution_queue_full": "当前任务较多，执行队列已满，请稍后重试。",
    "pi_execution_queue_timeout": "等待执行资源超时，请稍后重试。",
    "pi_execution_admission_unavailable": "执行资源暂不可用，请稍后重试。",
    "pi_execution_shutting_down": "服务正在关闭，当前任务未开始执行。",
    "pi_execution_admission_lost": "执行资源租约已失效，任务已安全停止。",
}
_CHAT_ERROR_KIND_FALLBACKS = {
    "model": "模型服务未能完成本轮处理。",
    "extension": "扩展执行失败，详细信息已记录。",
    "tool": "工具执行失败，详细信息已记录。",
    "runtime": "本轮处理未能完成，请稍后重试。",
}
_STABLE_CHAT_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_TRUSTED_CHAT_ERROR_REASON_CODES = frozenset(
    {
        "answer_endpoint_unavailable",
        "capability_unsupported",
        "concurrency_invalid",
        "data_level_blocked",
        "data_level_invalid",
        "endpoint_inactive",
        "image_support_disabled",
        "model_inactive",
        "model_missing",
        "secret_missing",
        "timeout_invalid",
        "image_too_large",
        "image_format_unsupported",
        "image_invalid",
        "image_count_exceeded",
        "vision_concurrency_exhausted",
        "vision_budget_exhausted",
        "vision_budget_unavailable",
        "vision_settle_failed",
        "vision_response_over_limit",
    }
)


def stable_chat_error_code(value: object) -> str:
    """只接纳可安全回传并用于恢复判断的稳定错误码。"""
    code = str(value or "").strip()
    return code if _STABLE_CHAT_ERROR_CODE_RE.fullmatch(code) else ""


def canonical_chat_error_metadata(
    value: object,
    *,
    error_code: object = "",
) -> tuple[str, str | None]:
    """只保留恢复所需的稳定错误码、HTTP 状态和机器原因码。"""
    parsed: object = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
    metadata = parsed if isinstance(parsed, dict) else {}
    code = stable_chat_error_code(error_code) or stable_chat_error_code(
        metadata.get("error_code") or metadata.get("code")
    )
    safe: dict[str, object] = {}
    if code:
        safe["error_code"] = code
    http_status = metadata.get("http_status")
    if (
        isinstance(http_status, int)
        and not isinstance(http_status, bool)
        and 400 <= http_status <= 599
    ):
        safe["http_status"] = http_status
    reasons = metadata.get("reasons")
    if isinstance(reasons, list):
        stable_reasons = [
            reason
            for raw_reason in reasons
            if (
                (reason := stable_chat_error_code(raw_reason))
                in _TRUSTED_CHAT_ERROR_REASON_CODES
            )
        ][:12]
        if stable_reasons:
            safe["reasons"] = stable_reasons
    if not safe:
        return code, None
    return code, json.dumps(safe, ensure_ascii=False, separators=(",", ":"))


def trusted_chat_error_message(
    error_code: object,
    *,
    error_kind: str = "runtime",
) -> str:
    """从固定目录生成客户文案，绝不回显异常或供应商 payload。"""
    code = stable_chat_error_code(error_code)
    if code in _TRUSTED_CHAT_ERROR_MESSAGES:
        return _TRUSTED_CHAT_ERROR_MESSAGES[code]
    return _CHAT_ERROR_KIND_FALLBACKS.get(
        error_kind,
        _CHAT_ERROR_KIND_FALLBACKS["runtime"],
    )


def looks_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def is_user_safe_message(text: str) -> bool:
    """Return True when text is short operator/user guidance, not a dump."""
    stripped = text.strip()
    if not stripped or len(stripped) > 300:
        return False
    lowered = stripped.lower()
    if any(marker in lowered for marker in _UNSAFE_MARKERS):
        return False
    if _FILE_LINE.search(stripped) or _EXCEPTION_NAME.fullmatch(stripped):
        return False
    if "\n" in stripped and ("Error" in stripped or "Exception" in stripped):
        return False
    return True


def public_error_message(text: str | None, *, fallback: str) -> str:
    stored = redact_inline_secrets((text or "").strip())
    if stored and is_user_safe_message(stored):
        return stored[:300]
    return redact_inline_secrets(fallback)
