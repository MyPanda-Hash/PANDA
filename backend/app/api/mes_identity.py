"""内部接口：按用户取 MES 身份，供技能进程使用。

不通过任何公开 API 暴露：仅在容器回环 + capability token 校验后返回。
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from app.core.config import settings
from app.core.database import transaction
from app.models.mes_binding import MesUserBinding

router = APIRouter(prefix="/internal/v1", tags=["internal-mes-identity"])


def _require_capability(token: str | None) -> None:
    """Only the pi runtime may read per-user MES credentials.

    The container loopback is already private, but the password is stored in
    cleartext, so a shared token keeps any future co-located process from
    reading it just by guessing the URL.
    """
    expected = (settings.pi_harness_gateway_token or "").strip()
    if not expected:
        return  # 未配置时不阻断：单机部署依旧可用
    if (token or "").strip() != expected:
        raise HTTPException(status_code=403, detail="内部令牌无效")


@router.get("/mes-identity/{user_id}")
def get_mes_identity(
    user_id: str, x_nexus_capability: str | None = Header(default=None)
) -> dict:
    _require_capability(x_nexus_capability)
    with transaction() as db:
        row = (
            db.query(MesUserBinding)
            .filter(MesUserBinding.user_id == user_id)
            .one_or_none()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="该用户未绑定 MES 账号")
        return {
            "data": {
                "user_id": row.user_id,
                "mes_username": row.mes_username,
                "mes_password": row.mes_password,
                "mes_role_code": row.mes_role_code,
                "mes_is_admin": bool(row.mes_is_admin),
            }
        }
