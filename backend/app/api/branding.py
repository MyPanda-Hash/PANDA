"""品牌与命名：系统名、AI 数字员工名、语音助手名，由管理员改。

为什么单独开一个模块而不是塞进 /admin/system-config：
那份配置是「模型路由 / 运行参数」的运维面，键名带 Pi 概念、分组是 Chat/Writing，
而这三个名字是**全员可见的产品外观**，前端外壳每次启动都要读，
不该让普通用户去读一份管理员配置，也不该把命名混进模型设置里。

存储直接复用 system_config 表（不新建表），键前缀 brand_。
读取接口对**所有登录用户**开放：外壳要拿它渲染标题、导航品牌、助手名。
写入仅管理员。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import admin_user, current_user_from_bearer_or_cookie  # noqa: F401
from app.core.database import transaction
from app.models.auth import User
from app.repositories import system_config as config_repo

router = APIRouter(prefix="/branding", tags=["branding"])

# 默认值：产品还没改名时的初始外观
DEFAULTS = {
    "brand_system_name": "MES",
    "brand_assistant_name": "小智",
    "brand_voice_name": "小智",
    "brand_company_name": "",
}

LABELS = {
    "brand_system_name": "系统名称",
    "brand_assistant_name": "数字员工名称",
    "brand_voice_name": "语音助手名称",
    "brand_company_name": "公司名称",
}


class BrandingResponse(BaseModel):
    system_name: str = ""
    assistant_name: str = ""
    voice_name: str = ""
    company_name: str = ""


class BrandingUpdate(BaseModel):
    system_name: str | None = Field(default=None, max_length=32)
    assistant_name: str | None = Field(default=None, max_length=16)
    voice_name: str | None = Field(default=None, max_length=16)
    company_name: str | None = Field(default=None, max_length=48)


def _read(db) -> BrandingResponse:
    stored = config_repo.get_all(db)
    return BrandingResponse(
        system_name=(stored.get("brand_system_name") or DEFAULTS["brand_system_name"]).strip(),
        assistant_name=(stored.get("brand_assistant_name") or DEFAULTS["brand_assistant_name"]).strip(),
        voice_name=(stored.get("brand_voice_name") or DEFAULTS["brand_voice_name"]).strip(),
        company_name=(stored.get("brand_company_name") or DEFAULTS["brand_company_name"]).strip(),
    )


@router.get("", response_model=BrandingResponse)
def get_branding(
    _: User = Depends(current_user_from_bearer_or_cookie),
) -> BrandingResponse:
    """全员可读：外壳靠它渲染品牌与助手名。

    用 bearer_or_cookie 而不是 current_user：浏览器端是 Cookie 会话，
    只认 Authorization 头的依赖会让外壳永远读不到品牌（实测 401）。
    """
    with transaction() as db:
        return _read(db)


@router.patch("", response_model=BrandingResponse)
def update_branding(
    payload: BrandingUpdate,
    user: User = Depends(admin_user),
) -> BrandingResponse:
    """仅管理员：改名。空字符串表示「用默认值」。"""
    mapping = {
        "system_name": "brand_system_name",
        "assistant_name": "brand_assistant_name",
        "voice_name": "brand_voice_name",
        "company_name": "brand_company_name",
    }
    with transaction() as db:
        for field_name, key in mapping.items():
            value = getattr(payload, field_name)
            if value is None:
                continue
            cleaned = str(value).strip()
            # 存默认值等于没改，写空串让读取端回落到 DEFAULTS，避免以后改默认值时不生效
            if not cleaned or cleaned == DEFAULTS.get(key, ""):
                config_repo.upsert(db, key, "", LABELS[key])
            else:
                config_repo.upsert(db, key, cleaned, LABELS[key])
        return _read(db)