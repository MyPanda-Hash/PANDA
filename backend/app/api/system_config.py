from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import admin_user
from app.core.assistant_identity import (
    ASSISTANT_NAME,
    DEFAULT_COMPANY_NAME,
    build_default_pi_system_prompt,
)
from app.core.config import settings
from app.core.database import transaction
from app.models.auth import User
from app.repositories import system_config as repo
from app.schemas.system_config import (
    Importance,
    SystemConfigGroup,
    SystemConfigItem,
    SystemConfigOption,
    SystemConfigResponse,
    SystemConfigUpdateRequest,
)
from app.services.chat_agent_config import default_chat_welcome_suggestions_text
from app.services.model_routing import (
    MODEL_ROUTE_KEYS,
    list_default_model_options,
    list_route_options,
    list_vision_model_options,
)

router = APIRouter(prefix="/admin/system-config", tags=["admin-system-config"])


@dataclass(frozen=True)
class ConfigMeta:
    key: str
    label: str
    description: str
    default_value: str = ""
    group: str = "general"
    group_label: str = "通用"
    importance: Importance = "optional"
    secret: bool = False
    deprecated: bool = False
    multiline: bool = False
    options: tuple[tuple[str, str], ...] = ()


_GROUP_ORDER = (
    ("chat", "Chat / Pi", "后端直接托管的 Pi 对话、人设和模型路由。"),
    ("writing", "智能写作", "后端 Pi 写作任务的模型路由和发布策略。"),
    ("runtime", "处理运行参数", "文档处理轮询与超时等运行参数，一般保持默认即可。"),
    ("integration", "外部系统联调", "共享用户与 API Key 数据库联调配置。"),
    ("compat", "兼容旧项", "历史兼容字段，优先使用新键。"),
)


_CONFIG_META: tuple[ConfigMeta, ...] = (
    ConfigMeta(
        key="app_name",
        label="公司名称",
        description="用于默认 Pi 人设提示词中的公司归属；保留 app_name 键兼容历史配置。",
        default_value=DEFAULT_COMPANY_NAME,
        group="chat",
        group_label="Chat / Pi",
        importance="optional",
    ),
    ConfigMeta(
        key="pi_persona_name",
        label="Pi 数字员工名称",
        description="Chat 和写作 Pi 的对外人设名称。",
        default_value=settings.pi_persona_name,
        group="chat",
        group_label="Chat / Pi",
        importance="recommended",
    ),
    ConfigMeta(
        key="pi_persona_title",
        label="Pi 数字员工职称",
        description="注入 Pi system prompt 的人设职称。",
        default_value=settings.pi_persona_title,
        group="chat",
        group_label="Chat / Pi",
        importance="optional",
    ),
    ConfigMeta(
        key="pi_persona_system_prompt",
        label="Pi 人设系统提示词",
        description="留空使用随公司名称和数字员工名称变化的后端默认人设；仅填写稳定的行为边界，不要放 API Key 或用户资料。",
        group="chat",
        group_label="Chat / Pi",
        importance="optional",
        multiline=True,
    ),
    ConfigMeta(
        key="pi_chat_model",
        label="Pi Chat 模型端点",
        description="对话与知识问答共用此端点；provider 由端点记录决定，避免同名模型串错供应商。",
        group="chat",
        group_label="Chat / Pi",
        importance="required",
    ),
    ConfigMeta(
        key="pi_vision_model",
        label="Pi 图片识别模型",
        description="主聊天模型不支持图片时使用；留空则按可用端点顺序自动选择。",
        group="chat",
        group_label="Chat / Pi",
        importance="optional",
    ),
    ConfigMeta(
        key="chat_welcome_suggestions",
        label="Chat 欢迎页推荐问题",
        description="空白会话欢迎页上的快捷提问，每行一条；留空则不显示推荐问题。",
        default_value=default_chat_welcome_suggestions_text(),
        group="chat",
        group_label="Chat / Pi",
        importance="recommended",
        multiline=True,
    ),
    ConfigMeta(
        key="pi_writing_model",
        label="Pi 写作模型端点",
        description="提纲、正文、改写和校验任务使用的模型端点；留空回退 Pi Chat 端点。",
        group="writing",
        group_label="智能写作",
        importance="recommended",
    ),
    ConfigMeta(
        key="pi_writing_default_model",
        label="Pi 写作默认模型",
        description="从已选择的写作模型端点中挑选默认模型；留空则使用该端点的主模型。",
        group="writing",
        group_label="智能写作",
        importance="recommended",
    ),
    ConfigMeta(
        key="writing_pi_auto_publish",
        label="Pi 写作自动发布",
        description="开启后通过现有证据校验的候选会自动发布；生产环境建议关闭。",
        default_value="0",
        group="writing",
        group_label="智能写作",
        importance="optional",
    ),
    ConfigMeta(
        key="embedding_model",
        label="Embedding 模型端点",
        description="文档向量化使用的 embedding 端点；必须选择 capability=embedding 的端点。",
        group="retrieval",
        group_label="检索与模型",
        importance="recommended",
    ),
    ConfigMeta(
        key="rerank_model",
        label="Rerank 模型端点",
        description="可选的 rerank 端点；留空表示不启用独立 rerank。",
        group="retrieval",
        group_label="检索与模型",
        importance="optional",
    ),
)

_META_BY_KEY = {item.key: item for item in _CONFIG_META}
_ROUTE_KEY_TO_SLOT = {key: slot for slot, key in MODEL_ROUTE_KEYS.items()}


def _default_value(meta: ConfigMeta, stored: dict[str, str]) -> str:
    if meta.key != "pi_persona_system_prompt":
        return meta.default_value
    name = (
        stored.get("pi_persona_name") or settings.pi_persona_name or ASSISTANT_NAME
    ).strip()
    company_name = (stored.get("app_name") or DEFAULT_COMPANY_NAME).strip()
    return build_default_pi_system_prompt(
        persona_name=name or ASSISTANT_NAME,
        company_name=company_name or DEFAULT_COMPANY_NAME,
    )


def _to_item(
    meta: ConfigMeta,
    stored: dict[str, str],
    route_options: dict[str, list[tuple[str, str]]] | None = None,
) -> SystemConfigItem:
    options = (
        route_options.get(meta.key, [])
        if route_options is not None and meta.key in route_options
        else list(meta.options)
    )
    return SystemConfigItem(
        key=meta.key,
        value=stored.get(meta.key, ""),
        label=meta.label,
        description=meta.description,
        default_value=_default_value(meta, stored),
        group=meta.group,
        group_label=meta.group_label,
        importance=meta.importance,
        secret=meta.secret,
        deprecated=meta.deprecated,
        multiline=meta.multiline,
        options=[
            SystemConfigOption(value=value, label=label) for value, label in options
        ],
    )


def _build_response(
    stored: dict[str, str],
    route_options: dict[str, list[tuple[str, str]]] | None = None,
) -> SystemConfigResponse:
    items = [_to_item(meta, stored, route_options) for meta in _CONFIG_META]
    groups: list[SystemConfigGroup] = []
    for group_id, group_label, group_desc in _GROUP_ORDER:
        group_items = [item for item in items if item.group == group_id]
        if not group_items:
            continue
        groups.append(
            SystemConfigGroup(
                id=group_id,
                label=group_label,
                description=group_desc,
                items=group_items,
            )
        )
    return SystemConfigResponse(items=items, groups=groups)


def _route_options(
    db, stored: dict[str, str] | None = None
) -> dict[str, list[tuple[str, str]]]:
    options = {
        key: list_route_options(db, slot) for slot, key in MODEL_ROUTE_KEYS.items()
    }
    options["pi_writing_default_model"] = list_default_model_options(
        db, "writing", stored
    )
    options["pi_vision_model"] = list_vision_model_options(db)
    return options


def _validate_route_value(db, key: str, value: str) -> None:
    if key == "pi_vision_model":
        allowed = {
            option_value
            for option_value, _label in list_vision_model_options(db)
            if option_value
        }
        if value.strip() and value.strip() not in allowed:
            raise HTTPException(
                status_code=422, detail="图片识别模型未配置、未启用或不支持图片输入"
            )
        return
    if key not in _ROUTE_KEY_TO_SLOT or not value.strip():
        return
    options = dict(list_route_options(db, _ROUTE_KEY_TO_SLOT[key]))
    if value.strip() not in options:
        raise HTTPException(
            status_code=422, detail="模型端点未配置、未启用或能力不匹配"
        )


def _validate_writing_default_model(db, stored: dict[str, str], value: str) -> None:
    allowed = {
        option_value
        for option_value, _label in list_default_model_options(db, "writing", stored)
        if option_value
    }
    trimmed = value.strip()
    if not trimmed:
        return
    if not allowed:
        raise HTTPException(status_code=422, detail="请先选择有效的 Pi 写作模型端点")
    if trimmed not in allowed:
        raise HTTPException(status_code=422, detail="默认模型不属于所选写作端点")


def _sync_writing_default_model(db, stored: dict[str, str]) -> dict[str, str]:
    names = [
        option_value
        for option_value, _label in list_default_model_options(db, "writing", stored)
        if option_value
    ]
    current = (stored.get("pi_writing_default_model") or "").strip()
    if not names or not current or current in names:
        return stored
    meta = _META_BY_KEY["pi_writing_default_model"]
    repo.upsert(db, meta.key, names[0], meta.description)
    stored = repo.get_all(db)
    return stored


@router.get("", response_model=SystemConfigResponse)
def get_config(_: User = Depends(admin_user)) -> SystemConfigResponse:
    with transaction() as db:
        stored = repo.get_all(db)
        return _build_response(stored, _route_options(db, stored))


@router.patch("", response_model=SystemConfigResponse)
def update_config(
    payload: SystemConfigUpdateRequest,
    admin: User = Depends(admin_user),
) -> SystemConfigResponse:
    del admin
    with transaction() as db:
        stored = repo.get_all(db)
        pending = {item.key: item.value for item in payload.items}
        upcoming = {**stored, **pending}
        for item in payload.items:
            meta = _META_BY_KEY.get(item.key)
            if meta is None:
                raise HTTPException(
                    status_code=422, detail=f"不支持的系统配置项：{item.key}"
                )
            description = meta.description
            # Secret fields: empty means keep existing value
            if meta.secret and item.value.strip() == "":
                continue
            if item.key == "pi_writing_default_model":
                _validate_writing_default_model(db, upcoming, item.value)
            elif meta.options:
                allowed = {value for value, _label in meta.options}
                if item.value.strip() and item.value.strip() not in allowed:
                    raise HTTPException(
                        status_code=422,
                        detail=f"{meta.label}取值无效",
                    )
            _validate_route_value(db, item.key, item.value)
            repo.upsert(db, item.key, item.value, description)
        stored = _sync_writing_default_model(db, repo.get_all(db))
        return _build_response(stored, _route_options(db, stored))
