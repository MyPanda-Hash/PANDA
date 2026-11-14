"""Backend-owned Pi Agent facade used by Chat and Writing."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import shutil
from collections.abc import Callable
from datetime import datetime
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import urlsplit

from app.core.config import settings
from app.core.database import transaction
from app.repositories import harness as harness_repo
from app.services import linkmind
from app.services.harness.task import (
    AgentTask,
    ProjectContext,
    TaskLimits,
    TaskPermissions,
    TaskVerification,
)
from app.services.model_endpoint_config import (
    ADAPTER_AZURE_OPENAI,
    PROTOCOL_CHAT_COMPLETIONS,
    resolve_endpoint_configuration,
)
from app.services.model_routing import (
    configured_default_model,
    resolve_model_endpoint,
)
from app.services.pi_agent_client import (
    PiAgentCancelledError,
    PiAgentError,
    PiAgentResult,
    parse_json_reply,
)
from app.services.pi_runtime.config import (
    PI_RUNTIME_AGENT_ID,
    PiRuntimeSettings,
    resolve_pi_model,
)
from app.services.pi_runtime.events import pi_event_to_sse_item
from app.services.pi_runtime.persona import PiPersona, load_pi_persona
from app.services.pi_runtime.runner import (
    PI_OUTPUT_LIMIT_ERROR_CODE,
    PI_OUTPUT_LIMIT_PUBLIC_MESSAGE,
    PiRpcCancelledError,
    PiRpcError,
    PiRpcRunner,
    PiRpcTimeoutError,
    PiRunResult,
)
from app.services.pi_runtime.skills import workspace_dir_for_agent

logger = logging.getLogger(__name__)
_PI_ADMISSION_PUBLIC_MESSAGES = {
    "pi_execution_queue_full": "当前任务较多，执行队列已满，请稍后重试。",
    "pi_execution_queue_timeout": "等待执行资源超时，请稍后重试。",
    "pi_execution_admission_unavailable": "执行资源暂不可用，请稍后重试。",
    "pi_execution_shutting_down": "服务正在关闭，当前任务未开始执行。",
    "pi_execution_admission_lost": "执行资源租约已失效，任务已安全停止。",
    PI_OUTPUT_LIMIT_ERROR_CODE: PI_OUTPUT_LIMIT_PUBLIC_MESSAGE,
}
CHAT_TURN_WARNING_BASE_TOKENS = 500_000
# 提醒阈值随上下文缩放：每次模型调用都会重发整段上下文，因此固定阈值会
# 过早提示正常的工具迭代。阈值只用于展示提醒，不传给 model proxy 作为硬上限。
CHAT_TURN_EXPECTED_CALLS = 7
CHAT_TURN_WARNING_CEILING_TOKENS = 1_500_000
GENERATION_CHAT_TURN_WARNING_BASE_TOKENS = 800_000
GENERATION_CHAT_TURN_EXPECTED_CALLS = 12
GENERATION_CHAT_TURN_WARNING_CEILING_TOKENS = 3_000_000
GENERATION_CHAT_SKILLS = frozenset(
    {
        "dashi-ppt",
        "docx",
        "markdown-exporter",
        "pdf",
        "powerpoint-ppt",
        "pptx",
        "writing-bid",
        "writing-proposal",
        "writing-quote",
        "writing-report",
        "xlsx",
    }
)


def chat_turn_token_warning_threshold(
    context_tokens: int = 0,
    *,
    selected_skill: str | None = None,
) -> int:
    """Scale one turn's warning threshold without inferring generation intent."""
    generation = (selected_skill or "") in GENERATION_CHAT_SKILLS
    expected_calls = (
        GENERATION_CHAT_TURN_EXPECTED_CALLS if generation else CHAT_TURN_EXPECTED_CALLS
    )
    baseline = (
        GENERATION_CHAT_TURN_WARNING_BASE_TOKENS
        if generation
        else CHAT_TURN_WARNING_BASE_TOKENS
    )
    ceiling = (
        GENERATION_CHAT_TURN_WARNING_CEILING_TOKENS
        if generation
        else CHAT_TURN_WARNING_CEILING_TOKENS
    )
    scaled = max(0, int(context_tokens or 0)) * expected_calls
    return max(baseline, min(scaled, ceiling))


def _safe_int(value: object) -> int:
    if isinstance(value, (int, float, str)):
        try:
            return int(cast(str | int | float, value))
        except (ValueError, OverflowError):
            return 0
    return 0


_HARNESS_OPERATIONAL_RULES = """

Skill 运行方式：
- 会话中发布的是管理员已批准、摘要校验通过的完整标准 Skill；发现匹配任务的 Skill 后，先读取并严格遵循其 `SKILL.md`。
- Skill 的 scripts、references、assets 与其他随附文件均可按 Skill 自身说明在当前 sandbox 和会话工作区内使用，不需要也不存在额外执行包装器。
- 产出文件放在当前会话工作区；只有工具结果明确成功后，才能声称操作完成。
- 面向用户交付的文稿类文档直接写成 Markdown（`.md`）即可；系统会在交付时自动
  转成 `.docx` 供下载，不要在 sandbox 内自行安装 pandoc/npm 生成 Word 文件。
- 生成日期表、日历或排期时先确定年份：优先沿用用户明确指定的年份；仅给月份且没有其他年份依据时，采用下方当前日期所属年份并在交付说明中明确，不沿用模型记忆中的旧年份。日期和星期必须用 Python datetime/calendar 等日历计算，不手写推算。
- 自然周按周一至周日分组，目标日期必须完整覆盖且不重复；跨月补齐的日期应明确标为相邻月份，月度统计只计算该月日期，不能把完整周天数直接当作当月天数。
- Excel 使用预装 openpyxl 生成；宣称“自动统计”的字段必须写入真实公式，不能用下划线或静态数字冒充。保存后重新打开实际文件，核对日期覆盖、星期、统计范围、公式及下拉选项；用全未打卡、全已打卡和跨月样例核验统计。openpyxl 不计算公式缓存，未经表格计算引擎验证不得声称已验证 Excel 重算结果。
- 文件交付成功后，平台会根据实际生成文件自动显示包含文件名、下载和查看入口的文件卡片；不要自行拼接或重复输出下载链接，也不要输出未求值的模板字符串、URL 编码表达式或内部路径。
- 最终答复只保留对用户有用的内容说明、必要限制或待确认事项；不要复述“文件已上传”、Skill 执行过程或“系统会自动转成 .docx”等内部交付细节，也不要解释平台将显示卡片或下载入口。没有额外说明时只回复“已完成。”，文件入口由平台提供。
- 工具失败时只向用户说明未完成事项和可恢复的下一步，不复述接口原文、内部地址、凭据或堆栈；执行结果未知时先核验，不能假称成功。
- 联网查询以实际取得的相关网页为依据，答复列出可核对的来源链接和资料时间；网页报错、验证码、无关内容或仅访问首页都不算查到答案。没有查到时明确说明，不能把模型记忆写成已联网核实的结果。
- 排行、赛事或其他时效性信息先依据当前日期核对届次和统计时间；不能因旧届数据熟悉就当作最新。用户要求的条数未取得完整证据时，只列已核实的部分并明确缺项，不宣称已完成全部。
- 缺少可执行依赖或遇到权限拒绝后，停止同一路径；可用的替代方案必须确实具备该能力并向用户说明。不要反复猜域名、文章 ID 或路径；从已返回的相关页面链接继续，仍无依据时直接报告无法核实。
- 排查命令保留真实退出状态，不用 `| head`、`|| true` 等管道或兜底把失败伪装成成功；需要截取输出时先确认原命令成功。
- 查找尚未安装的 Skill 时使用 `search_skill_catalog`；用户明确要求安装时使用
  `request_skill_install` 提交隔离区审核申请，不得声称已安装或已启用。
"""


def _sandbox_operational_rules() -> str:
    current_time = datetime.now().astimezone()
    operational_rules = (
        f"当前日期：{current_time.date().isoformat()}；时区：{current_time:%Z %z}。\n"
        f"{_HARNESS_OPERATIONAL_RULES}"
    )
    if (str(settings.pi_execution_mode or "").strip().lower()) != "sandbox":
        return operational_rules
    hosts = tuple(
        dict.fromkeys(
            host.strip().lower()
            for host in (settings.pi_chat_network_allowlist or "").split(",")
            if host.strip()
        )
    )
    allowlist = "、".join(hosts) or "未配置"
    return f"""{operational_rules.rstrip()}

Sandbox 能力边界（必须先遵守，避免无效重试）：
- 容器根文件系统只读；只有 `/workspace`、`/session` 和 `/tmp` 可写，`/skills` 与 `/pi-config` 只读。
- 镜像预装 `sh`、`python`、`node`、`curl`、`git`、`zip`、`unzip`、`fd`、Excel 库 `openpyxl` 和 PPT 运行依赖；PPT 技能按说明直接调用 Node/Python 脚本。工具策略仍禁止直接调用包管理器或在线安装；`belt` 不可用。
- `web-search` 若要求 `belt`/`npx`，当前会话不可执行；不要反复运行 `which`、`npm`、`npx`、`belt`，也不要在 sandbox 内临时安装。
- `http_request` 可读取任意公网网页，但仅限 `GET`/`HEAD`；有请求体或写操作仍只允许当前白名单主机：{allowlist}。
- 遇到“command not found”、包管理器缺失、私网地址或非读取请求被拒绝时，停止同类重试并说明限制。
"""


@dataclass(frozen=True, slots=True)
class LocalPiEndpoint:
    endpoint_id: str
    provider: str
    base_url: str
    model: str
    api_key: str
    api_version: str = ""
    adapter: str = "openai_compatible"
    api_protocol: str = PROTOCOL_CHAT_COMPLETIONS
    allow_anonymous_api_key: bool = False
    supports_image_input: bool = False
    max_context: int = 128000


@dataclass(slots=True)
class LocalPiAgent:
    id: str
    name: str
    tenant_id: str
    system_message: str = ""
    model_ref: Any = field(
        default_factory=lambda: SimpleNamespace(credential_id="local", model="")
    )
    pi_config: Any = field(
        default_factory=lambda: SimpleNamespace(
            thinking_level="medium", tools=(), timeout_sec=300, enable_skills=True
        )
    )


def _generated_files_reply(generated_files: list[dict[str, Any]]) -> str:
    lines = []
    for item in generated_files:
        name = str(item.get("name") or item.get("path") or "文件")
        url = str(item.get("url") or "").strip()
        lines.append(f"- [{name}]({url})" if url else f"- {name}")
    if not lines:
        return "文件已生成，但下载链接暂不可用。"
    return "已生成文件：\n" + "\n".join(lines)


def _secret(secret_ref: str | None) -> str:
    raw = (secret_ref or "").strip()
    if raw.startswith("env://"):
        return os.environ.get(raw[6:], "").strip()
    return raw


def _base_url(endpoint_url: str) -> str:
    value = endpoint_url.split("?")[0].rstrip("/")
    for suffix in (
        "/chat/completions",
        "/completions",
        "/responses",
        "/embeddings",
        "/rerank",
        "/openai/v1",
        "/openai",
    ):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    return value.rstrip("/")


_ANONYMOUS_LOCAL_HOST_ALIASES = frozenset(
    {
        "docker.for.linux.host.internal",
        "docker.for.linux.localhost",
        "docker.for.mac.host.internal",
        "docker.for.mac.localhost",
        "docker.for.win.host.internal",
        "docker.for.win.localhost",
        "gateway.docker.internal",
        "host.containers.internal",
        "host.docker.internal",
        "localhost",
    }
)
_ANONYMOUS_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "10.0.0.0/8",
        "127.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
    )
)
_ANONYMOUS_IPV6_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "::1/128",
        "fc00::/7",
    )
)


def _endpoint_hostname(endpoint_url: object) -> str | None:
    """Extract a safe HTTP(S) hostname without resolving or following redirects."""
    raw = str(endpoint_url or "").strip()
    if not raw or any(character.isspace() for character in raw):
        return None
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or username is not None
        or password is not None
        or (port is not None and not 1 <= port <= 65535)
        or parsed.fragment
    ):
        return None
    return hostname.rstrip(".").casefold()


def _is_controlled_anonymous_host(hostname: str) -> bool:
    if hostname in _ANONYMOUS_LOCAL_HOST_ALIASES:
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    if address.version == 4:
        return any(address in network for network in _ANONYMOUS_IPV4_NETWORKS)
    mapped = address.ipv4_mapped
    if mapped is not None:
        return any(mapped in network for network in _ANONYMOUS_IPV4_NETWORKS)
    return any(address in network for network in _ANONYMOUS_IPV6_NETWORKS)


def _allows_anonymous_api_key(endpoint: Any, *, capability: str = "chat") -> bool:
    """Allow keyless calls only to explicitly controlled local gateways."""
    hostname = _endpoint_hostname(getattr(endpoint, "endpoint_url", ""))
    if hostname is None:
        return False

    secret_ref = getattr(endpoint, "secret_ref", None)
    normalized_capability = str(capability or "").strip().lower()
    if secret_ref is None:
        return _is_controlled_anonymous_host(hostname)

    if (
        normalized_capability == "embedding"
        and secret_ref == linkmind.LINKMIND_ENDPOINT_SECRET_REF
    ):
        # Existing local LinkMind embedding routes remain keyless; a
        # non-local route needs an explicit configured LinkMind host match.
        if _is_controlled_anonymous_host(hostname):
            return True
        configured_hostname = _endpoint_hostname(settings.linkmind_base_url)
        return bool(configured_hostname) and hostname == configured_hostname

    # Empty or unresolved secret references must fail closed as well.
    return False


def resolve_local_pi_endpoint(
    model: str | None = None,
    *,
    capability: str = "chat",
    endpoint_id: str | None = None,
    user_id: str = "",
) -> LocalPiEndpoint:
    slot = "writing" if capability == "writing" else capability
    requested = (model or "").strip() or (
        configured_default_model("writing") if slot == "writing" else ""
    )
    with transaction() as db:
        endpoint = resolve_model_endpoint(
            db,
            slot,
            requested_model=requested or None,
            configured_id=endpoint_id,
        )
    if slot == "writing" and (model or "").strip() and (
        endpoint is None or endpoint.model != requested
    ):
        raise PiAgentError(
            "所选生成模型已不可用，请重新选择模型。",
            code="writing_model_unavailable",
            max_attempts=1,
        )
    if endpoint is None:
        raise PiAgentError(
            f"未配置可用的本地 Pi {capability} 模型端点，请在管理页面新增 active {capability} 端点"
        )
    api_key = _secret(endpoint.secret_ref)
    if endpoint.secret_ref == linkmind.LINKMIND_ENDPOINT_SECRET_REF:
        if capability == "embedding":
            # Embedding uses its own provider credential path; do not send a
            # per-user LinkMind User Key for this capability.
            api_key = ""
        else:
            logger.info(
                "Resolving LinkMind model endpoint endpoint_id=%s user_id=%s",
                endpoint.id,
                user_id,
            )
            if not user_id:
                raise PiAgentError("LinkMind 模型端点需要当前用户身份")
            try:
                api_key = linkmind.ensure_user_key(user_id)
            except linkmind.LinkMindError as exc:
                logger.warning(
                    "LinkMind user key unavailable endpoint_id=%s user_id=%s error=%s",
                    endpoint.id,
                    user_id,
                    exc.detail,
                )
                raise PiAgentError("当前用户的 LinkMind Key 不可用") from exc
            logger.info(
                "LinkMind model endpoint resolved endpoint_id=%s user_id=%s api_key_present=%s",
                endpoint.id,
                user_id,
                bool(api_key),
            )
    allow_anonymous_api_key = _allows_anonymous_api_key(
        endpoint, capability=capability
    )
    if not api_key and not allow_anonymous_api_key:
        raise PiAgentError(
            f"模型端点 {endpoint.provider}/{endpoint.model} 未配置 API Key"
        )
    try:
        config = resolve_endpoint_configuration(
            endpoint.provider,
            getattr(endpoint, "adapter", ""),
            getattr(endpoint, "api_protocol", ""),
        )
    except ValueError as exc:
        raise PiAgentError(str(exc)) from exc
    chosen = endpoint.model
    return LocalPiEndpoint(
        endpoint_id=endpoint.id,
        provider=config.provider,
        base_url=_base_url(endpoint.endpoint_url),
        model=chosen,
        api_key=api_key,
        api_version=getattr(endpoint, "api_version", "") or "",
        adapter=config.adapter,
        api_protocol=config.api_protocol,
        allow_anonymous_api_key=allow_anonymous_api_key,
        supports_image_input=endpoint.supports_image_input,
        max_context=endpoint.max_context,
    )


def _agent(
    *,
    agent_id: str,
    user_id: str,
    model: str,
    system_message: str = "",
    timeout_seconds: float | None = None,
) -> LocalPiAgent:
    persona = load_pi_persona()
    prompt = system_message.strip() or persona.system_prompt
    if not system_message.strip() and isinstance(persona, PiPersona):
        prompt = f"{prompt.rstrip()}\n{_sandbox_operational_rules()}"
    try:
        timeout_sec = max(
            1,
            int(
                timeout_seconds
                if timeout_seconds is not None
                else settings.pi_agent_timeout_seconds
            ),
        )
    except (TypeError, ValueError):
        timeout_sec = 900
    try:
        idle_timeout_sec = max(30, int(settings.pi_idle_timeout_seconds))
    except (TypeError, ValueError):
        idle_timeout_sec = 600
    return LocalPiAgent(
        # Backend-owned Pi uses one stable capability identity. The former
        # External agent identifiers must not leak into the child process.
        id=PI_RUNTIME_AGENT_ID,
        name="doc-nexus-assistant",
        tenant_id="_system",
        system_message=prompt,
        model_ref=SimpleNamespace(credential_id="local", model=model),
        pi_config=SimpleNamespace(
            thinking_level="medium",
            tools=(
                "read",
                "write",
                "edit",
                "bash",
                "grep",
                "find",
                "search_skill_catalog",
                "request_skill_install",
                "http_request",
            ),
            timeout_sec=timeout_sec,
            idle_timeout_sec=idle_timeout_sec,
            enable_skills=True,
        ),
    )


# 交接过来的工作区最多复制这么多，避免把一个巨大的会话目录整个搬走。
CHAT_HANDOFF_WORKSPACE_MAX_BYTES = 200 * 1024 * 1024
# 播种成功后写在目标工作区里的标记。和它描述的文件同生共死：工作区被清掉，
# 标记一起没，下次重新播种。据此可以区分「已经播过种」与「播种失败后 Pi 自己
# 写了文件」，两者都表现为目标非空，但只有前者算源文件可用。
HANDOFF_SEED_MARKER = ".doc-nexus-handoff"


def _ignore_seeded_entries(directory: str, names: list[str]) -> set[str]:
    """Skip symlinks and the seed marker itself.

    Symlinks: the size guard walks without following directory links, so
    following them while copying could move far more than the cap allows.
    Marker: carrying it into a chained handoff would make the next target
    believe it was already seeded.
    """
    ignored = {HANDOFF_SEED_MARKER}
    ignored.update(
        name for name in names if os.path.islink(os.path.join(directory, name))
    )
    return ignored


def seed_handoff_workspace(
    *,
    user_id: str,
    source_session_id: str,
    target_session_id: str,
) -> bool:
    """Report whether the source session's files are available to the target.

    The answer is about the workspace's *state*, not about whether this call
    copied anything: a retry of a handoff turn finds the files already there
    and must still say yes, or the carried-over context would claim the files
    are missing when they are not.

    A false answer in either direction is worse than no files, because the
    injected handoff context tells the model what it may assume about the
    workspace.
    """
    if not source_session_id or not target_session_id:
        return False
    if source_session_id == target_session_id:
        return False
    try:
        runtime = PiRuntimeSettings.from_env()
        agent, _resolved = LocalPiAgentClient(
            user_id=user_id, capability="chat"
        )._resolve()
    except Exception:  # noqa: BLE001 - 端点解析失败不该打挂整个交接回合
        logger.warning(
            "Chat handoff workspace seed could not resolve the agent user=%s",
            user_id,
            exc_info=True,
        )
        return False

    try:
        workspace = workspace_dir_for_agent(
            runtime, agent, target_session_id
        ).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        if (workspace / HANDOFF_SEED_MARKER).exists():
            return True
        source = workspace_dir_for_agent(runtime, agent, source_session_id).resolve()
        if not source.is_dir() or source == workspace:
            return False
        if any(workspace.iterdir()):
            # 没有标记却已经有文件：上一次播种没成功，Pi 自己写过东西。
            # 不覆盖，也不能对外声称源文件可用。
            logger.warning(
                "Chat handoff workspace already has unseeded files source=%s",
                source_session_id,
            )
            return False
        total = 0
        for root, _dirs, files in os.walk(source):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
                if total > CHAT_HANDOFF_WORKSPACE_MAX_BYTES:
                    logger.warning(
                        "Chat handoff workspace too large to seed source=%s bytes>%s",
                        source_session_id,
                        CHAT_HANDOFF_WORKSPACE_MAX_BYTES,
                    )
                    return False
        shutil.copytree(
            source,
            workspace,
            dirs_exist_ok=True,
            ignore=_ignore_seeded_entries,
        )
        (workspace / HANDOFF_SEED_MARKER).write_text(
            source_session_id, encoding="utf-8"
        )
        return True
    except (OSError, shutil.Error):
        logger.warning(
            "Chat handoff workspace seed failed source=%s target=%s",
            source_session_id,
            target_session_id,
            exc_info=True,
        )
        return False


class LocalPiAgentClient:
    """Synchronous facade for the writing dispatcher and async facade for Chat."""

    def __init__(
        self,
        *,
        agent_id: str | None = None,
        user_id: str = "",
        model: str | None = None,
        system_message: str = "",
        capability: str = "chat",
    ) -> None:
        self.agent_id = PI_RUNTIME_AGENT_ID
        self.user_id = user_id
        self.model = (model or "").strip()
        self.system_message = system_message
        self.capability = (capability or "chat").strip().lower()
        self.effective_model = ""
        self.source = "local_pi"

    def _resolve(self) -> tuple[LocalPiAgent, Any]:
        endpoint = resolve_local_pi_endpoint(
            self.model,
            capability=self.capability,
            user_id=self.user_id,
        )
        self.effective_model = endpoint.model
        agent = _agent(
            agent_id=self.agent_id,
            user_id=self.user_id,
            model=endpoint.model,
            system_message=self.system_message,
            timeout_seconds=(
                settings.pi_writing_timeout_seconds
                if self.capability == "writing"
                else settings.pi_agent_timeout_seconds
            ),
        )
        provider = endpoint.adapter
        credential = SimpleNamespace(
            provider=provider,
            adapter=endpoint.adapter,
            api_protocol=endpoint.api_protocol,
            api_key=endpoint.api_key,
            allow_anonymous_api_key=endpoint.allow_anonymous_api_key,
            base_url=endpoint.base_url,
            default_model=endpoint.model,
            azure_endpoint=(
                endpoint.base_url if endpoint.adapter == ADAPTER_AZURE_OPENAI else ""
            ),
            azure_deployment=(
                endpoint.model if endpoint.adapter == ADAPTER_AZURE_OPENAI else ""
            ),
            azure_api_version=endpoint.api_version,
            supports_image_input=endpoint.supports_image_input,
            max_context=endpoint.max_context,
        )
        store = SimpleNamespace(
            get_credential=lambda credential_id: (
                credential if credential_id == "local" else None
            )
        )
        return agent, resolve_pi_model(agent, cast(Any, store))

    async def chat_async(
        self,
        message: str,
        *,
        session_id: str | None = None,
        images: list[dict[str, Any]] | None = None,
        capability_token: str | None = None,
        resume_context: str | None = None,
        history_context_key: str | None = None,
        on_event: Callable[[dict[str, Any]], Any] | None = None,
        cancel_check: Callable[[], Any] | None = None,
        streamed_turn: bool = False,
        context_tokens: int = 0,
        token_warning_threshold: int | None = None,
        evidence_source_resolver: Callable[[list[str]], list[dict[str, Any]]] | None = None,
        admission_user_id: str | None = None,
        strict_browser: bool = False,
    ) -> tuple[PiRunResult, LocalPiAgent]:
        """执行一个 Pi 回合。

        ``streamed_turn`` 仅对用户可控的聊天流关闭固定墙钟上限，idle、
        请求次数和取消保护继续生效。设置 ``token_warning_threshold`` 时，
        model proxy 会严格核验用量以生成超量提醒，但不会把该值当作
        Token 硬上限。
        """
        streamed_turn = bool(streamed_turn) and self.capability == "chat"
        del context_tokens
        track_model_usage = streamed_turn or token_warning_threshold is not None
        runtime = PiRuntimeSettings.from_env()
        agent, resolved = self._resolve()
        # 只优化网页交互聊天；写作及其他调用方保留原工具曝光策略。
        if streamed_turn and capability_token and not strict_browser:
            agent.pi_config.tool_loading_mode = "lazy"
        current_session = (
            session_id or ""
        ).strip() or f"chat-{self.user_id or 'anonymous'}"
        self._ensure_harness_task(runtime, agent, current_session)
        try:
            result = await PiRpcRunner(runtime).run(
                agent=agent,
                resolved_model=resolved,
                session_id=current_session,
                prompt=message,
                resume_context=resume_context,
                history_context_key=history_context_key,
                images=images,
                on_event=on_event,
                cancel_check=cancel_check,
                nexus_capability_token=capability_token,
                task_id=current_session,
                gateway_tenant_id=str(getattr(agent, "tenant_id", "_system")),
                gateway_user_id=PI_RUNTIME_AGENT_ID,
                admission_user_id=admission_user_id or self.user_id,
                collect_generated_files=not strict_browser,
                strict_browser=strict_browser,
                evidence_source_resolver=evidence_source_resolver,
                enforce_wall_clock_timeout=not streamed_turn,
                track_model_usage=track_model_usage,
                max_tokens=None,
                initial_tokens=0,
            )
        except PiRpcCancelledError as exc:
            cancelled = PiAgentCancelledError(str(exc))
            for attribute in ("token_usage", "code", "context_usage"):
                value = getattr(exc, attribute, None)
                if value:
                    cancelled.__dict__[attribute] = value
            raise cancelled from exc
        except PiRpcTimeoutError as exc:
            if exc.generated_files:
                return (
                    PiRunResult(
                        reply=_generated_files_reply(exc.generated_files),
                        stats={"stop_reason": "timeout_with_generated_files"},
                        state={},
                        events=[],
                        token_usage=exc.token_usage,
                        generated_files=exc.generated_files,
                    ),
                    agent,
                )
            raise
        except (PiAgentError, RuntimeError) as exc:
            if isinstance(exc, PiAgentError):
                wrapped = exc
            else:
                error_code = str(getattr(exc, "code", "") or "")
                wrapped = PiAgentError(
                    str(exc),
                    code=error_code,
                    public_message=_PI_ADMISSION_PUBLIC_MESSAGES.get(error_code, ""),
                    max_attempts=getattr(exc, "max_attempts", None),
                )
            for attribute in (
                "generated_files",
                "token_usage",
                "partial_reply",
                "code",
                "context_usage",
                "retry_after_seconds",
                "max_attempts",
            ):
                value = getattr(exc, attribute, None)
                if value:
                    wrapped.__dict__[attribute] = value
            if wrapped is exc:
                raise
            raise wrapped from exc
        return result, agent

    @staticmethod
    def _ensure_harness_task(
        runtime: PiRuntimeSettings, agent: LocalPiAgent, session_id: str
    ) -> None:
        """Persist the chat session as a Harness task with a canonical workspace."""
        workspace = workspace_dir_for_agent(runtime, agent, session_id).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        allowlist = tuple(
            host.strip()
            for host in (settings.pi_chat_network_allowlist or "").split(",")
            if host.strip()
        )
        task = AgentTask(
            task_id=session_id,
            tenant_id=str(agent.tenant_id),
            user_id=PI_RUNTIME_AGENT_ID,
            project=ProjectContext(project_id="chat", workspace=str(workspace)),
            objective="Pi chat session",
            permissions=TaskPermissions(
                workspace_file_upload=True,
                skill_install_request=True,
                network=True,
                network_allowlist=allowlist,
                public_web_read=True,
                install_packages=runtime.execution_mode == "sandbox",
                sandbox=runtime.execution_mode == "sandbox",
            ),
            limits=TaskLimits(),
            verification=TaskVerification(),
        )
        try:
            with transaction() as db:
                existing = harness_repo.get_task(db, session_id)
                if existing is None:
                    harness_repo.create_task(db, task, workspace_path=str(workspace))
                elif (
                    existing.tenant_id == task.tenant_id
                    and existing.user_id == task.user_id
                    and existing.project_id == task.project.project_id
                ):
                    if existing.workspace_path != str(workspace):
                        # Runtime roots can change when switching host/sandbox
                        # mode or recreating the container. Keep the persisted
                        # Harness identity bound to the workspace Pi will use.
                        harness_repo.update_task_workspace(
                            db,
                            session_id,
                            str(workspace),
                        )
                    harness_repo.ensure_task_permission(
                        db,
                        session_id,
                        "skill_install_request",
                    )
                    harness_repo.ensure_task_permission(
                        db,
                        session_id,
                        "public_web_read",
                    )
        except Exception:
            logger.warning(
                "Failed to persist Harness task for chat session %s; "
                "approved Skill execution will be unavailable",
                session_id,
                exc_info=True,
            )

    async def steer_async(
        self,
        message: str,
        *,
        session_id: str,
        images: list[dict[str, Any]] | None = None,
        client_message_id: str | None = None,
    ) -> None:
        await self._control_async(
            "steer",
            message=message,
            session_id=session_id,
            images=images,
            client_message_id=client_message_id,
        )

    async def follow_up_async(
        self,
        message: str,
        *,
        session_id: str,
        images: list[dict[str, Any]] | None = None,
        client_message_id: str | None = None,
    ) -> None:
        await self._control_async(
            "follow_up",
            message=message,
            session_id=session_id,
            images=images,
            client_message_id=client_message_id,
        )

    async def respond_to_extension_ui(
        self,
        *,
        session_id: str,
        request_id: str,
        confirmed: bool,
    ) -> None:
        runtime = PiRuntimeSettings.from_env()
        agent = _agent(
            agent_id=self.agent_id,
            user_id=self.user_id,
            model=self.model,
            system_message=self.system_message,
        )
        try:
            await PiRpcRunner(runtime).respond_to_extension_ui(
                agent=agent,
                session_id=session_id,
                request_id=request_id,
                confirmed=confirmed,
            )
        except (PiRpcError, RuntimeError) as exc:
            raise PiAgentError(str(exc)) from exc

    async def _control_async(
        self,
        command: str,
        *,
        message: str,
        session_id: str,
        images: list[dict[str, Any]] | None,
        client_message_id: str | None,
    ) -> None:
        runtime = PiRuntimeSettings.from_env()
        agent, _resolved = self._resolve()
        try:
            if command == "steer":
                await PiRpcRunner(runtime).steer(
                    agent=agent,
                    session_id=session_id,
                    message=message,
                    images=images,
                    client_message_id=client_message_id,
                )
            else:
                await PiRpcRunner(runtime).follow_up(
                    agent=agent,
                    session_id=session_id,
                    message=message,
                    images=images,
                    client_message_id=client_message_id,
                )
        except (PiAgentError, RuntimeError) as exc:
            raise PiAgentError(str(exc)) from exc


    def chat(
        self,
        message: str,
        *,
        session_id: str | None = None,
        idempotency_key: str | None = None,
        capability_token: str | None = None,
        on_event: Callable[[dict[str, Any]], Any] | None = None,
        cancel_check: Callable[[], Any] | None = None,
        token_warning_threshold: int | None = None,
        admission_user_id: str | None = None,
    ) -> PiAgentResult:
        del idempotency_key

        async def run() -> PiAgentResult:
            result, _ = await self.chat_async(
                message,
                session_id=session_id,
                capability_token=capability_token,
                on_event=on_event,
                cancel_check=cancel_check,
                token_warning_threshold=token_warning_threshold,
                admission_user_id=admission_user_id,
            )
            try:
                payload = parse_json_reply(result.reply)
            except Exception as exc:
                wrapped = PiAgentError(str(exc))
                if result.token_usage:
                    wrapped.__dict__["token_usage"] = result.token_usage
                if result.generated_files:
                    wrapped.__dict__["generated_files"] = result.generated_files
                raise wrapped from exc
            raw_stats = result.stats if isinstance(result.stats, dict) else {}
            token_stats = raw_stats.get("tokens")
            stats = dict(token_stats) if isinstance(token_stats, dict) else {}
            return PiAgentResult(
                payload=payload,
                session_id=session_id or f"chat-{self.user_id or 'anonymous'}",
                model=self.effective_model or self.model,
                run_time_ms=0,
                token_usage={
                    "prompt_tokens": result.token_usage.get("prompt_tokens", 0),
                    "completion_tokens": result.token_usage.get("completion_tokens", 0),
                    "total_tokens": result.token_usage.get("total_tokens", 0),
                    "input_uncached_tokens": result.token_usage.get(
                        "input_uncached_tokens", _safe_int(stats.get("inputUncached"))
                    ),
                    "cache_read_tokens": result.token_usage.get(
                        "cache_read_tokens", _safe_int(stats.get("cacheRead"))
                    ),
                    "cache_write_tokens": result.token_usage.get(
                        "cache_write_tokens", _safe_int(stats.get("cacheWrite"))
                    ),
                },
                trace_steps=len(result.events),
            )

        try:
            return asyncio.run(run())
        except PiAgentError:
            raise
        except Exception as exc:
            error_code = str(getattr(exc, "code", "") or "")
            wrapped = PiAgentError(
                str(exc),
                code=error_code,
                public_message=_PI_ADMISSION_PUBLIC_MESSAGES.get(error_code, ""),
                max_attempts=getattr(exc, "max_attempts", None),
            )
            for attribute in (
                "generated_files",
                "token_usage",
                "partial_reply",
                "code",
                "context_usage",
                "retry_after_seconds",
                "max_attempts",
            ):
                value = getattr(exc, attribute, None)
                if value:
                    wrapped.__dict__[attribute] = value
            raise wrapped from exc


def runtime_event_to_chat_item(
    event: dict[str, Any], *, agent_name: str = "doc-nexus-assistant"
) -> dict[str, Any] | None:
    return pi_event_to_sse_item(event, agent_name=agent_name)
