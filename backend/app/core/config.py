import secrets
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOC_NEXUS_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )
    database_url: str = (
        f"sqlite:///{Path(__file__).resolve().parents[2] / 'doc_nexus.sqlite3'}"
    )
    db_path: Path = Path(__file__).resolve().parents[2] / "doc_nexus.sqlite3"
    jwt_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    access_ttl_minutes: int = 15
    refresh_ttl_days: int = 14
    invite_ttl_hours: int = 72
    # TokenLink shared directory and LinkMind local User Key integration.
    linkmind_enabled: bool = False
    # 复用既有 LinkMind 的客户基线关闭此项；不重置外部统计/登录会话，也不伪造完成标记。
    linkmind_initialize_box_on_setup: bool = True
    tokenlink_database_path: str = ""
    linkmind_base_url: str = ""
    # Browser-reachable URL for the optional LinkMind SSO redirect.
    linkmind_public_url: str = ""
    # URL-safe base64 Fernet key used to encrypt the one-time-created
    # LinkMind User Key when Doc Nexus stores it server-side.
    linkmind_key_encryption_key: str = ""
    # 独立于 JWT/LinkMind 的通知 Fernet 密钥环，JSON 如 {"1":"<Fernet key>"}。
    notification_fernet_keys: str = ""
    notification_fernet_current_version: str = "1"
    # Automatic media routing is opt-in; explicit UI/slash paths are unaffected.
    media_intent_mode: Literal["off", "shadow", "confirm", "auto"] = "off"
    # Auto execution remains empty until production data calibrates this exact
    # classifier version and an operator explicitly allowlists reason codes.
    media_intent_auto_calibration_version: str = ""
    media_intent_auto_reason_codes: str = ""
    media_dispatch_lease_seconds: int = Field(default=300, ge=30, le=86_400)

    linkmind_timeout_seconds: float = 10.0
    frontend_origin: str = "http://localhost:5173"
    bootstrap_username: str = "admin"
    # Internal placeholder used only until the unauthenticated setup flow sets a password.
    bootstrap_password: str = secrets.token_urlsafe(32)
    upload_dir: Path = Path(__file__).resolve().parents[2] / "uploads"
    max_upload_mb: int = 100
    # Chat document attachments are marked temporary by the chat upload flow.
    # The cleanup runner is opt-in; a zero interval disables its scheduled loop.
    temporary_attachment_ttl_seconds: int = 24 * 60 * 60
    temporary_attachment_cleanup_interval_seconds: int = 60 * 60
    # Knowledge-document multipart uploads are independent from 100 MiB chat
    # attachments.  The reverse proxy accepts one 16 MiB staging PUT.
    document_uploads_enabled: bool = True
    document_upload_max_mb: int = Field(default=512, ge=1, le=4096)
    document_upload_part_size_mb: int = Field(default=16, ge=5, le=16)
    document_upload_ttl_seconds: int = Field(
        default=24 * 60 * 60, ge=300, le=7 * 24 * 60 * 60
    )
    document_upload_part_url_ttl_seconds: int = Field(
        default=15 * 60, ge=60, le=7 * 24 * 60 * 60
    )
    document_upload_lease_seconds: int = Field(default=300, ge=30, le=86_400)
    document_upload_cleanup_interval_seconds: int = Field(default=300, ge=30, le=86_400)
    document_upload_cleanup_batch_size: int = Field(default=100, ge=1, le=1000)
    document_upload_max_sessions_per_user: int = Field(default=8, ge=1, le=100)
    # Leave empty for same-origin /storage-upload proxy URLs.  When set, the
    # MinIO SDK signs directly against this operator-configured HTTP(S) host.
    document_upload_public_url: str = ""
    qa_retrieval_top_k: int = 8
    qa_retrieval_pool_limit: int = 200
    # Hybrid fusion: final = (1 - graph_weight) * norm(lex) + graph_weight * norm(graph).
    qa_graph_weight: float = 0.45
    # Adaptive retrieval is opt-in per caller through retrieve_for_qa(mode="auto").
    # Existing QA/chat callers keep the legacy lexical+graph behavior by default.
    qa_vector_enabled: bool = True
    qa_vector_top_k: int = 30
    qa_vector_min_lexical_score: float = 0.8
    qa_vector_min_lexical_margin: float = 0.15
    # Local, bounded PDF OCR. The executable is intentionally fixed to
    # ``tesseract``; callers cannot supply arbitrary process arguments.
    ocr_enabled: bool = True
    ocr_languages: str = "chi_sim+eng"
    ocr_dpi: int = 300
    ocr_max_pages: int = 200
    ocr_page_timeout_seconds: int = 30
    ocr_max_total_pixels: int = 400_000_000
    # Opt-in document/PDF visual recovery. Chat image assistance follows model
    # capabilities automatically and does not depend on this switch.
    vision_fallback_enabled: bool = False
    vision_max_images_per_message: int = 4
    vision_max_image_bytes: int = 4_000_000
    vision_max_output_chars: int = 12_000
    vision_timeout_seconds: float = 60.0
    vision_max_concurrency: int = 2
    vision_max_total_tokens: int = 16_000
    vision_max_cost_cny: Decimal = Decimal("1.00")
    chat_image_ocr_fallback_enabled: bool = False
    # MinIO (object storage)
    minio_enabled: str = "1"
    minio_url: str = ""
    minio_show_url: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "dgmeta"
    minio_temp_bucket: str = "dgmeta-temp"
    minio_presign_minutes: int = 120
    dingtalk_webhook: str = ""
    dingtalk_secret: str = ""
    # 自由对话：外部 Agent 平台（翠花）
    agent_chat_base_url: str = "http://192.168.88.244:8991"
    agent_chat_api_key: str = ""
    agent_chat_agent_id: str = "75fcf0fa-9556-452b-9115-3130cf7f04e9"
    agent_chat_model: str = ""
    agent_chat_session_type: str = "debug_trial"
    # Nexus capability grant used by Pi read-only tools.
    agent_capability_secret: str = ""
    agent_capability_ttl_seconds: int = 600
    writing_provider: str = "pi_chat"
    pi_agent_url: str = ""
    pi_agent_api_key: str = ""
    pi_agent_id: str = ""
    pi_agent_require_published: bool = True
    # Default non-writing agent budget. Chat disables its wall-clock cap and
    # instead uses a per-turn token budget plus idle timeout.
    pi_agent_timeout_seconds: float = 900.0
    # Formal writing gets a separate budget because it may render several
    # chapters/pages in one durable operation.
    pi_writing_timeout_seconds: float = 1200.0
    # Per-tool execution ceiling, independent of the chat token budget.
    pi_tool_timeout_seconds: int = 600
    pi_max_stderr_chars: int = 16_384
    # Idle timeout per readline — Pi has output, timer resets; no output, timeout fires.
    pi_idle_timeout_seconds: int = 600
    writing_event_stream_timeout_seconds: int = 3600
    pi_nexus_capability_base_url: str = "http://127.0.0.1:8000"
    # Wall-clock fallback for bounded (non-chat) runtime executions.
    pi_default_timeout_seconds: int = 900
    # 共享 Pi 运行资源按总量及用户限额准入，等待队列有界。
    pi_max_concurrency: int = Field(default=4, ge=1, le=64)
    pi_max_concurrency_per_user: int = Field(default=2, ge=1, le=64)
    pi_queue_max_waiters: int = Field(default=32, ge=0, le=1024)
    pi_queue_timeout_seconds: float = Field(default=120.0, ge=1, le=3600)
    chat_preflight_max_workers: int = Field(default=8, ge=1, le=32)
    worker_role: Literal["all", "api", "documents", "background"] = "all"
    document_worker_threads: int = Field(default=2, ge=1, le=32)
    background_worker_threads: int = Field(default=2, ge=1, le=32)
    worker_shutdown_grace_seconds: float = Field(default=15.0, ge=1, le=300)
    schedule_scan_interval_seconds: float = Field(default=1.0, ge=0.1, le=60)
    schedule_lease_seconds: int = Field(default=90, ge=30, le=900)
    schedule_worker_concurrency: int = Field(default=2, ge=1, le=16)
    schedule_worker_concurrency_per_user: int = Field(default=1, ge=1, le=8)
    # 单用户非归档计划数上限；预算硬停之外的总量防线。
    schedule_max_per_user: int = Field(default=50, ge=1, le=500)
    # 单次无人值守运行在预算预检中的最坏成本估算上限（CNY）。
    schedule_max_cost_cny: Decimal = Field(default=Decimal("2.00"), ge=0)
    # 终态运行（含尝试、通知与工作区）保留天数；0 天语义不支持，最小 1 天。
    schedule_run_retention_days: int = Field(default=90, ge=1, le=3650)
    # 定时任务保留清理间隔；0 表示关闭清理循环。
    schedule_cleanup_interval_seconds: float = Field(default=3600.0, ge=0.0, le=86400.0)
    pi_executable: str = "pi"
    pi_runtime_dir: Path | None = None
    # Shared enterprise agents execute in disposable sibling containers.
    pi_execution_mode: str = "sandbox"
    pi_sandbox_image: str = "doc-nexus-ai-backend"
    pi_sandbox_network: str = ""
    pi_sandbox_host_runtime_dir: Path | None = None
    pi_sandbox_memory: str = "1g"
    pi_sandbox_cpus: str = "2"
    pi_sandbox_pids_limit: int = 256
    pi_sandbox_workspace_max_bytes: int = 1024 * 1024 * 1024
    pi_sandbox_workspace_max_files: int = 20_000
    pi_root: Path = Path(__file__).resolve().parents[2] / "data" / "pi-runtime"
    pi_nexus_extension_path: Path = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "pi_runtime"
        / "extensions"
        / "nexus_capability.ts"
    )
    pi_tool_policy_extension_path: Path = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "pi_runtime"
        / "extensions"
        / "tool_policy.ts"
    )
    pi_tool_gateway_extension_path: Path = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "pi_runtime"
        / "extensions"
        / "tool_gateway_proxy.ts"
    )
    pi_harness_gateway_url: str = ""
    pi_harness_gateway_token: str = ""
    # Comma-separated host allowlist for non-read business API requests from
    # local chat sessions. Public webpage reads are limited to GET/HEAD.
    pi_chat_network_allowlist: str = (
        "www.nmc.cn,nmc.cn,typhoon.nmc.cn,www.weather.com.cn,"
        "toy1.weather.com.cn,weather.cma.cn,www.thehear.org,thehear.org"
    )
    # Trusted built-in Skills admitted idempotently during initialization.
    pi_builtin_skill_allowlist: str = (
        "writing-bid,writing-proposal,writing-quote,writing-report,find-skills,the-news,"
        "minio-upload,dashi-ppt,pptx,open-meteo-api"
    )
    # Official Skill catalog used by search_skill_catalog (server-side only).
    pi_skill_catalog_base_url: str = "https://skills.sh"
    # Approved Skill production isolation. Empty keeps the local test adapter.
    pi_harness_skill_container_image: str = ""
    pi_harness_skill_container_network: str = "none"
    pi_persona_name: str = "翠花"
    pi_persona_title: str = "数字员工翠花"
    pi_persona_system_prompt: str = ""
    pi_chat_model: str = ""
    pi_writing_model: str = ""
    pi_writing_default_model: str = ""
    embedding_model: str = ""
    rerank_model: str = ""
    # Embedding API (overrides model endpoint resolution when set)
    embedding_api_url: str = ""
    embedding_api_key: str = ""
    embedding_api_model: str = "text-embedding-v2"
    embedding_dimensions: int = 1536
    # ---- MES 联合认证 ----
    # 开启后，本地不存在的账号会向 MES 验证密码；验证通过则自动镜像成
    # 本地用户。这样 MES 建账号即可登录本系统，权限随 MES 角色走。
    mes_auth_enabled: bool = False
    mes_auth_base_url: str = "http://host.docker.internal:8080"
    # ---- MES 单点登录（免密进场）----
    # light-mes 用同一密钥签发 60 秒票据，本服务验签后直接建立会话。
    # 留空 = 关闭，GET /api/auth/mes-sso 返回 401。
    # MES 与 AI 合并成一套账号体系后，AI 直接验 MES 签发的 JWT（HS256，同一密钥）。
    # 注意别名：本类统一加 DOC_NEXUS_ 前缀，而这个密钥是 MES 与 AI 共用的既有变量，
    # 名字必须还是 MES_JWT_SECRET，否则两边对不上。
    # 留空时回落到原来的 ticket/自建会话路径，保证老部署还能起来。
    mes_jwt_secret: str = Field(default="", validation_alias="MES_JWT_SECRET")
    mes_sso_secret: str = ""
    mes_sso_redirect: str = "/chat"

    # 图片能否出网（初始化解析组织架构图用）：.env 里的值不会进 os.environ，
    # 所以 data_guard 读不到，这里显式声明一份供其回退查询。
    data_mask_media_policy: str = Field(
        default="",
        validation_alias=AliasChoices("DOC_NEXUS_DATA_MASK_MEDIA_POLICY"),
    )

    # ---- 系统初始化向导（MES 调本服务解析组织架构图） ----
    internal_token: str = Field(
        default="",
        validation_alias=AliasChoices("DOC_NEXUS_INTERNAL_TOKEN", "INTERNAL_TOKEN"),
    )

    # ---- MES 主动监控（AI 助手轮询 MES，主动派任务） ----
    mes_watch_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("MES_WATCH_ENABLED", "DOC_NEXUS_MES_WATCH_ENABLED"),
    )
    mes_watch_base_url: str = Field(
        default="http://127.0.0.1:8080",
        validation_alias=AliasChoices("MES_WATCH_BASE_URL", "DOC_NEXUS_MES_WATCH_BASE_URL"),
    )
    mes_watch_username: str = Field(
        default="admin",
        validation_alias=AliasChoices("MES_WATCH_USERNAME", "DOC_NEXUS_MES_WATCH_USERNAME"),
    )
    mes_watch_password: str = Field(
        default="",
        validation_alias=AliasChoices("MES_WATCH_PASSWORD", "DOC_NEXUS_MES_WATCH_PASSWORD"),
    )

    # ---- 联网检索 ----------------------------------------------------------
    # 为什么不用 DeepSeek 的 key 做搜索：它的 API 没有搜索能力（没有内置搜索工具，
    # enable_search 参数会被静默忽略），联网只在官方 App/网页版里有。
    # 这里用阿里云百炼的搜索增强（qwen + enable_search），实测能返回带来源的结论。
    search_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("SEARCH_API_KEY", "LOCAL_VLM_API_KEY"),
    )
    search_api_url: str = Field(
        default="https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
        validation_alias=AliasChoices("SEARCH_API_URL"),
    )
    # AnySearch：给 AI 用的搜索基础设施，MCP 协议。实测 3 秒返回带 URL + 日期的结构化结果，
    # 比百炼的「模型总结」和 360 的「标题列表」都更适合当检索源。
    anysearch_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("ANYSEARCH_API_KEY"),
    )
    # 联网检索总开关：关掉后模型不会调 web_search，只回本系统数据
    search_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("SEARCH_ENABLED"),
    )
    anysearch_api_url: str = Field(
        default="https://api.anysearch.com/mcp",
        validation_alias=AliasChoices("ANYSEARCH_API_URL"),
    )
    search_api_model: str = Field(
        default="qwen-plus",
        validation_alias=AliasChoices("SEARCH_API_MODEL"),
    )
    # ---- 检测阈值 ----------------------------------------------------------
    # 规则的门槛集中在这里，见 app/services/rule_thresholds.py。
    # 不同工厂节奏差别很大，写死的数字最后一定会被绕过。
    mes_rule_stall_days: int = Field(
        default=1, validation_alias=AliasChoices("MES_RULE_STALL_DAYS")
    )
    mes_rule_quality_rate: float = Field(
        default=0.05, validation_alias=AliasChoices("MES_RULE_QUALITY_RATE")
    )
    mes_rule_quality_critical_rate: float = Field(
        default=0.15, validation_alias=AliasChoices("MES_RULE_QUALITY_CRITICAL_RATE")
    )
    mes_rule_quality_min_sample: int = Field(
        default=5, validation_alias=AliasChoices("MES_RULE_QUALITY_MIN_SAMPLE")
    )
    mes_rule_trend_rate: float = Field(
        default=0.08, validation_alias=AliasChoices("MES_RULE_TREND_RATE")
    )
    mes_rule_trend_min_orders: int = Field(
        default=2, validation_alias=AliasChoices("MES_RULE_TREND_MIN_ORDERS")
    )
    mes_rule_trend_min_sample: int = Field(
        default=10, validation_alias=AliasChoices("MES_RULE_TREND_MIN_SAMPLE")
    )
    mes_rule_supplier_min_events: int = Field(
        default=2, validation_alias=AliasChoices("MES_RULE_SUPPLIER_MIN_EVENTS")
    )
    mes_rule_customer_min_docs: int = Field(
        default=2, validation_alias=AliasChoices("MES_RULE_CUSTOMER_MIN_DOCS")
    )
    mes_rule_approver_min_events: int = Field(
        default=3, validation_alias=AliasChoices("MES_RULE_APPROVER_MIN_EVENTS")
    )
    mes_rule_due_soon_days: int = Field(
        default=2, validation_alias=AliasChoices("MES_RULE_DUE_SOON_DAYS")
    )
    mes_rule_stale_hours: int = Field(
        default=24, validation_alias=AliasChoices("MES_RULE_STALE_HOURS")
    )
    mes_rule_repeat_kind_times: int = Field(
        default=3, validation_alias=AliasChoices("MES_RULE_REPEAT_KIND_TIMES")
    )
    mes_rule_repeat_doc_times: int = Field(
        default=2, validation_alias=AliasChoices("MES_RULE_REPEAT_DOC_TIMES")
    )
    # 早间主动推送：每天第一轮扫描时，把各自的今日计划推到站内消息 + 调度台群
    mes_morning_plan_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("MES_MORNING_PLAN_ENABLED", "DOC_NEXUS_MES_MORNING_PLAN_ENABLED"),
    )
    # 每日简报推送时间（本地时区的整点）。0 表示关闭推送。
    # 选择「几点」而不是 cron 表达式：目前只有一个日报任务，表达式徒增理解成本。
    brief_push_hour: int = Field(
        default=8,
        ge=0,
        le=23,
        description="每日简报推送小时（本地时间），0 表示关闭",
    )

    mes_watch_interval_seconds: float = Field(
        default=120.0,
        validation_alias=AliasChoices("MES_WATCH_INTERVAL_SECONDS", "DOC_NEXUS_MES_WATCH_INTERVAL_SECONDS"),
    )
    # MES 开放审批接口（只读，独立令牌，按审批人查询）
    mes_open_api_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("MES_OPEN_API_ENABLED", "DOC_NEXUS_MES_OPEN_API_ENABLED"),
    )
    mes_open_api_base_url: str = Field(
        default="http://127.0.0.1:8080",
        validation_alias=AliasChoices("MES_OPEN_API_BASE_URL", "DOC_NEXUS_MES_OPEN_API_BASE_URL"),
    )
    mes_open_api_token: str = Field(
        default="",
        validation_alias=AliasChoices("MES_OPEN_API_TOKEN", "DOC_NEXUS_MES_OPEN_API_TOKEN"),
    )
    # 低库存阈值：MES 没有"安全库存"字段，所以阈值放配置里；0 表示不启用。
    mes_watch_low_stock_threshold: float = Field(
        default=0.0,
        validation_alias=AliasChoices("MES_WATCH_LOW_STOCK_THRESHOLD", "DOC_NEXUS_MES_WATCH_LOW_STOCK_THRESHOLD"),
    )
    # 按物料覆盖阈值，格式 "CL005:50,CP004:100"
    mes_watch_low_stock_items: str = Field(
        default="",
        validation_alias=AliasChoices("MES_WATCH_LOW_STOCK_ITEMS", "DOC_NEXUS_MES_WATCH_LOW_STOCK_ITEMS"),
    )
    mes_watch_purchase_lead_days: int = Field(
        default=3,
        validation_alias=AliasChoices("MES_WATCH_PURCHASE_LEAD_DAYS", "DOC_NEXUS_MES_WATCH_PURCHASE_LEAD_DAYS"),
    )
    mes_watch_inspect_overdue_days: int = Field(
        default=2,
        validation_alias=AliasChoices("MES_WATCH_INSPECT_OVERDUE_DAYS", "DOC_NEXUS_MES_WATCH_INSPECT_OVERDUE_DAYS"),
    )
    mes_watch_no_report_days: int = Field(
        default=3,
        validation_alias=AliasChoices("MES_WATCH_NO_REPORT_DAYS", "DOC_NEXUS_MES_WATCH_NO_REPORT_DAYS"),
    )
    # 反复提醒：几小时后重发、最多几次
    mes_watch_remind_hours: float = Field(
        default=4.0,
        validation_alias=AliasChoices("MES_WATCH_REMIND_HOURS", "DOC_NEXUS_MES_WATCH_REMIND_HOURS"),
    )
    mes_watch_max_reminders: int = Field(
        default=3,
        validation_alias=AliasChoices("MES_WATCH_MAX_REMINDERS", "DOC_NEXUS_MES_WATCH_MAX_REMINDERS"),
    )
    # 拖多久没处理就升级为 critical
    mes_watch_escalate_hours: float = Field(
        default=24.0,
        validation_alias=AliasChoices("MES_WATCH_ESCALATE_HOURS", "DOC_NEXUS_MES_WATCH_ESCALATE_HOURS"),
    )
    # 只在这些时段通知（24 小时制），空=全天。例：8-18
    mes_watch_quiet_hours: str = Field(
        default="",
        validation_alias=AliasChoices("MES_WATCH_QUIET_HOURS", "DOC_NEXUS_MES_WATCH_QUIET_HOURS"),
    )
    # 销售订单下单后多久还没出库就算逾期
    mes_watch_so_ship_days: int = Field(
        default=7,
        validation_alias=AliasChoices("MES_WATCH_SO_SHIP_DAYS", "DOC_NEXUS_MES_WATCH_SO_SHIP_DAYS"),
    )
    # 给用户点的 MES 前端地址（不是 API 地址）；留空则用 base_url
    mes_watch_ui_url: str = Field(
        default="",
        validation_alias=AliasChoices("MES_WATCH_UI_URL", "DOC_NEXUS_MES_WATCH_UI_URL"),
    )
    # 归档：resolved/handled 超过 N 天的事件挪进归档，不再参与统计
    mes_watch_archive_days: int = Field(
        default=30,
        validation_alias=AliasChoices("MES_WATCH_ARCHIVE_DAYS", "DOC_NEXUS_MES_WATCH_ARCHIVE_DAYS"),
    )
    # 报表：每天几点发日报（本地时间，0-23），周一发周报，每月 1 号发月报
    assistant_report_hour: int = Field(
        default=9,
        validation_alias=AliasChoices("ASSISTANT_REPORT_HOUR", "DOC_NEXUS_ASSISTANT_REPORT_HOUR"),
    )
    assistant_report_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("ASSISTANT_REPORT_ENABLED", "DOC_NEXUS_ASSISTANT_REPORT_ENABLED"),
    )
    mes_watch_group_name: str = Field(
        default="AI 调度台",
        validation_alias=AliasChoices("MES_WATCH_GROUP_NAME", "DOC_NEXUS_MES_WATCH_GROUP_NAME"),
    )
    # 查询向量请求独立限流，避免慢供应商占满聊天预处理线程。
    embedding_query_max_concurrency: int = Field(default=4, ge=1, le=32)
    embedding_query_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    dashscope_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "DASHSCOPE_API_KEY", "DOC_NEXUS_DASHSCOPE_API_KEY"
        ),
    )
    dashscope_asr_url: str = Field(
        default=(
            "https://llm-o2dmtl9qx5a7ssmk.cn-beijing.maas.aliyuncs.com/"
            "api/v1/services/aigc/multimodal-generation/generation"
        ),
        validation_alias=AliasChoices(
            "DASHSCOPE_ASR_URL", "DOC_NEXUS_DASHSCOPE_ASR_URL"
        ),
    )
    dashscope_asr_model: str = Field(
        default="qwen-audio-3.0-asr-flash",
        validation_alias=AliasChoices(
            "DASHSCOPE_ASR_MODEL", "DOC_NEXUS_DASHSCOPE_ASR_MODEL"
        ),
    )
    asr_max_upload_bytes: int = 10 * 1024 * 1024
    asr_timeout_seconds: float = 60.0
    # MOSS-Live 全双工实时语音（悬浮窗）。密钥留空时只有语音入口不可用，
    # 其余功能不受影响；浏览器永远拿不到密钥，由后端 ws 中继持有。
    moss_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("MOSS_API_KEY", "DOC_NEXUS_MOSS_API_KEY"),
    )
    moss_realtime_url: str = Field(
        default="wss://api.mosi.cn/v1/realtime",
        validation_alias=AliasChoices(
            "MOSS_REALTIME_URL", "DOC_NEXUS_MOSS_REALTIME_URL"
        ),
    )
    moss_realtime_model: str = Field(
        default="moss-live-1.0",
        validation_alias=AliasChoices(
            "MOSS_REALTIME_MODEL", "DOC_NEXUS_MOSS_REALTIME_MODEL"
        ),
    )
    moss_voice_id: str = Field(
        default="6c3152b1-1d4f-4e49-ade3-09f447712fec",
        validation_alias=AliasChoices("MOSS_VOICE_ID", "DOC_NEXUS_MOSS_VOICE_ID"),
    )
    moss_voice_persona: str = Field(
        default="小缔",
        validation_alias=AliasChoices(
            "MOSS_VOICE_PERSONA", "DOC_NEXUS_MOSS_VOICE_PERSONA"
        ),
    )
    # 单次通话上限：按连接时长计费，超时必须断开，不做常驻麦克风。
    moss_voice_max_seconds: int = Field(
        default=900,
        ge=30,
        le=3600,
        validation_alias=AliasChoices(
            "MOSS_VOICE_MAX_SECONDS", "DOC_NEXUS_MOSS_VOICE_MAX_SECONDS"
        ),
    )
    moss_voice_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "MOSS_VOICE_ENABLED", "DOC_NEXUS_MOSS_VOICE_ENABLED"
        ),
    )
    # 语音能不能派活（建待办 / 发通知）。关掉后只剩查询。
    moss_voice_briefing_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "MOSS_VOICE_BRIEFING_ENABLED", "DOC_NEXUS_MOSS_VOICE_BRIEFING_ENABLED"
        ),
    )
    # 开场播报最多念多少字的概况：太长会把第一轮对话拖成朗读
    moss_voice_briefing_chars: int = Field(
        default=220,
        validation_alias=AliasChoices(
            "MOSS_VOICE_BRIEFING_CHARS", "DOC_NEXUS_MOSS_VOICE_BRIEFING_CHARS"
        ),
    )
    moss_voice_write_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "MOSS_VOICE_WRITE_ENABLED", "DOC_NEXUS_MOSS_VOICE_WRITE_ENABLED"
        ),
    )
    # 同一件事的多个细节并成一条待办的时间窗（天内）
    todo_merge_window_days: float = Field(
        default=7.0,
        ge=0.5,
        le=30.0,
        validation_alias=AliasChoices(
            "TODO_MERGE_WINDOW_DAYS", "DOC_NEXUS_TODO_MERGE_WINDOW_DAYS"
        ),
    )
    # 待办状态回流：多久没动算「卡住」、多久催一次、催几次升级。
    todo_followup_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "TODO_FOLLOWUP_ENABLED", "DOC_NEXUS_TODO_FOLLOWUP_ENABLED"
        ),
    )
    todo_idle_nudge_hours: float = Field(
        default=24.0,
        ge=1.0,
        le=720.0,
        validation_alias=AliasChoices(
            "TODO_IDLE_NUDGE_HOURS", "DOC_NEXUS_TODO_IDLE_NUDGE_HOURS"
        ),
    )
    todo_stuck_hours: float = Field(
        default=48.0,
        ge=1.0,
        le=720.0,
        validation_alias=AliasChoices(
            "TODO_STUCK_HOURS", "DOC_NEXUS_TODO_STUCK_HOURS"
        ),
    )
    todo_nudge_cooldown_hours: float = Field(
        default=12.0,
        ge=0.5,
        le=168.0,
        validation_alias=AliasChoices(
            "TODO_NUDGE_COOLDOWN_HOURS", "DOC_NEXUS_TODO_NUDGE_COOLDOWN_HOURS"
        ),
    )
    todo_max_nudges: int = Field(
        default=3,
        ge=1,
        le=20,
        validation_alias=AliasChoices(
            "TODO_MAX_NUDGES", "DOC_NEXUS_TODO_MAX_NUDGES"
        ),
    )
    # 一次通话里最多派几件，防止一句话被模型拆成十条待办。
    moss_voice_write_limit: int = Field(
        default=5,
        ge=0,
        le=50,
        validation_alias=AliasChoices(
            "MOSS_VOICE_WRITE_LIMIT", "DOC_NEXUS_MOSS_VOICE_WRITE_LIMIT"
        ),
    )
    tts_service_url: str = Field(
        default="https://wxapp.digimeta.com.cn/tts/synthesize",
        validation_alias=AliasChoices("TTS_SERVICE_URL", "DOC_NEXUS_TTS_SERVICE_URL"),
    )
    tts_voice: str = Field(
        default="x4_yezi",
        validation_alias=AliasChoices("TTS_VOICE", "DOC_NEXUS_TTS_VOICE"),
    )
    tts_speed: int = 50
    tts_pitch: int = 50
    tts_volume: int = 50
    tts_sample_rate: int = 16000
    tts_timeout_seconds: float = 15.0
    tts_max_response_bytes: int = 6 * 1024 * 1024
    tts_max_audio_bytes: int = 4 * 1024 * 1024
    dashscope_video_workspace_id: str = Field(
        default="",
        validation_alias=AliasChoices(
            "DASHSCOPE_VIDEO_WORKSPACE_ID", "DOC_NEXUS_DASHSCOPE_VIDEO_WORKSPACE_ID"
        ),
    )
    dashscope_video_model: str = Field(
        default="kling/kling-v3-video-generation",
        validation_alias=AliasChoices(
            "DASHSCOPE_VIDEO_MODEL", "DOC_NEXUS_DASHSCOPE_VIDEO_MODEL"
        ),
    )
    dashscope_video_image_model: str = Field(
        default="kling/kling-v3-omni-video-generation",
        validation_alias=AliasChoices(
            "DASHSCOPE_VIDEO_IMAGE_MODEL",
            "DOC_NEXUS_DASHSCOPE_VIDEO_IMAGE_MODEL",
        ),
    )
    video_file_adapter: str = Field(
        default="bailian",
        validation_alias=AliasChoices(
            "DOC_NEXUS_VIDEO_FILE_ADAPTER",
            "DASHSCOPE_VIDEO_FILE_ADAPTER",
        ),
    )
    dashscope_video_timeout_seconds: float = 60.0
    dashscope_video_poll_seconds: float = 15.0
    dashscope_image_workspace_id: str = Field(
        default="",
        validation_alias=AliasChoices(
            "DASHSCOPE_IMAGE_WORKSPACE_ID",
            "DOC_NEXUS_DASHSCOPE_IMAGE_WORKSPACE_ID",
        ),
    )
    dashscope_image_model: str = Field(
        default="kling/kling-v3-image-generation",
        validation_alias=AliasChoices(
            "DASHSCOPE_IMAGE_MODEL",
            "DOC_NEXUS_DASHSCOPE_IMAGE_MODEL",
        ),
    )
    dashscope_image_edit_model: str = Field(
        default="kling/kling-v3-image-generation",
        validation_alias=AliasChoices(
            "DASHSCOPE_IMAGE_EDIT_MODEL",
            "DOC_NEXUS_DASHSCOPE_IMAGE_EDIT_MODEL",
        ),
    )
    dashscope_image_timeout_seconds: float = 60.0
    dashscope_image_poll_seconds: float = 15.0
    writing_pi_auto_publish: bool = False
    writing_dispatch_lease_seconds: int = 60
    writing_dispatch_max_attempts: int = 5
    writing_dispatch_poll_seconds: float = 1.0
    writing_scope_secret: str = ""
    # Durable personal memory. Off by default: shipping the code must not turn
    # learning on for an existing deployment, and deployment .env files are not
    # rewritten to enable it. Learning additionally stays per-employee opt-in.
    personal_memory_enabled: bool = False
    personal_memory_snapshot_max_entries: int = Field(default=20, ge=1, le=100)
    personal_memory_snapshot_max_chars: int = Field(default=3_000, ge=200, le=20_000)
    personal_memory_source_text_max_chars: int = Field(
        default=12_000, ge=500, le=12_000
    )
    personal_memory_source_retention_days: int = Field(default=7, ge=1, le=90)
    personal_memory_extractor_version: str = "v1"
    personal_memory_lease_seconds: int = Field(default=120, ge=120, le=3_600)
    personal_memory_max_attempts: int = Field(default=3, ge=1, le=3)
    personal_memory_extract_timeout_seconds: float = Field(
        default=30.0, ge=5.0, le=120.0
    )
    personal_memory_poll_seconds: float = Field(default=2.0, ge=0.1, le=60.0)
    personal_memory_shutdown_grace_seconds: float = Field(
        default=35.0, ge=1.0, le=180.0
    )
    personal_memory_max_output_tokens: int = Field(default=4_096, ge=1_024, le=8_192)
    personal_memory_unpriced_reservation_cny: str = "1"
    personal_memory_cleanup_batch_size: int = Field(default=100, ge=1, le=500)
    personal_memory_cleanup_interval_seconds: float = Field(
        default=60.0, ge=10.0, le=3_600.0
    )
    personal_memory_candidate_retention_days: int = Field(default=30, ge=1, le=90)
    personal_memory_terminal_retention_days: int = Field(default=30, ge=1, le=90)
    writing_scope_ttl_seconds: int = 900
    writing_evidence_provider: str = "hybrid"
    # Enterprise WeChat self-built application callback
    wecom_corp_id: str = ""
    wecom_agent_id: int = 0
    wecom_secret: str = ""
    wecom_token: str = ""
    wecom_encoding_aes_key: str = ""
    wecom_timeout_seconds: float = 30.0
    # Empty exposes every non-deleted knowledge space; values narrow the scope.
    wecom_knowledge_space_ids: str = ""
    # Enterprise WeChat intelligent bot callback (@翠花 in group chats)
    wecom_aibot_id: str = ""
    wecom_aibot_token: str = ""
    wecom_aibot_encoding_aes_key: str = ""
    wecom_aibot_timeout_seconds: float = 30.0
    environment: str = "development"

    @field_validator("ocr_dpi")
    @classmethod
    def _ocr_dpi_bounds(cls, value: int) -> int:
        if not 150 <= value <= 400:
            raise ValueError("ocr_dpi must be between 150 and 400")
        return value

    @field_validator("ocr_max_pages")
    @classmethod
    def _ocr_pages_bounds(cls, value: int) -> int:
        if not 1 <= value <= 1000:
            raise ValueError("ocr_max_pages must be between 1 and 1000")
        return value

    @field_validator("ocr_page_timeout_seconds")
    @classmethod
    def _ocr_timeout_bounds(cls, value: int) -> int:
        if not 5 <= value <= 120:
            raise ValueError("ocr_page_timeout_seconds must be between 5 and 120")
        return value

    @field_validator("ocr_max_total_pixels")
    @classmethod
    def _ocr_pixels_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("ocr_max_total_pixels must be positive")
        return value

    @field_validator("ocr_languages")
    @classmethod
    def _ocr_languages_safe(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"[A-Za-z0-9_.+\-]+", value):
            raise ValueError("ocr_languages contains unsupported characters")
        return value

    @field_validator("vision_max_images_per_message")
    @classmethod
    def _vision_images_bounds(cls, value: int) -> int:
        if not 1 <= value <= 4:
            raise ValueError("vision_max_images_per_message must be between 1 and 4")
        return value

    @field_validator("vision_max_image_bytes")
    @classmethod
    def _vision_image_bytes_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("vision_max_image_bytes must be positive")
        return value

    @field_validator("vision_max_output_chars")
    @classmethod
    def _vision_output_chars_bounds(cls, value: int) -> int:
        if not 256 <= value <= 100_000:
            raise ValueError("vision_max_output_chars must be between 256 and 100000")
        return value

    @field_validator("vision_timeout_seconds")
    @classmethod
    def _vision_timeout_bounds(cls, value: float) -> float:
        if not 1 <= value <= 300:
            raise ValueError("vision_timeout_seconds must be between 1 and 300")
        return value

    @field_validator("vision_max_concurrency")
    @classmethod
    def _vision_concurrency_bounds(cls, value: int) -> int:
        if not 1 <= value <= 16:
            raise ValueError("vision_max_concurrency must be between 1 and 16")
        return value

    @field_validator("vision_max_total_tokens")
    @classmethod
    def _vision_tokens_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("vision_max_total_tokens must be positive")
        return value

    @field_validator("vision_max_cost_cny")
    @classmethod
    def _vision_cost_nonnegative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("vision_max_cost_cny must be non-negative")
        return value


settings = Settings()


_INSECURE_SECRET_VALUES = frozenset(
    {
        "development-only-change-me",
        "replace-with-at-least-32-random-bytes",
        "replace-with-a-random-install-password",
        "changeme",
    }
)


def validate_runtime_security(
    configured: Settings,
    *,
    require_bootstrap_password: bool = False,
) -> None:
    """Fail closed for production-only secrets without burdening local development."""
    if configured.environment.strip().lower() not in {"production", "prod"}:
        return

    jwt_secret = configured.jwt_secret.strip()
    if (
        "jwt_secret" not in configured.model_fields_set
        or len(jwt_secret) < 32
        or jwt_secret.lower() in _INSECURE_SECRET_VALUES
    ):
        raise RuntimeError(
            "production requires DOC_NEXUS_JWT_SECRET with at least 32 random characters"
        )

    if not require_bootstrap_password:
        return
    if "bootstrap_password" not in configured.model_fields_set:
        raise RuntimeError(
            "production requires DOC_NEXUS_BOOTSTRAP_PASSWORD for first-run setup"
        )
    bootstrap_password = configured.bootstrap_password.strip()
    if (
        len(bootstrap_password) < 12
        or bootstrap_password.lower() in _INSECURE_SECRET_VALUES
    ):
        raise RuntimeError(
            "configured DOC_NEXUS_BOOTSTRAP_PASSWORD must have at least 12 characters"
        )
