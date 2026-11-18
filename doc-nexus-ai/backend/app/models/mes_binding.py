"""doc-nexus 用户与 MES 账号的绑定。

一个员工两个系统共用一个账号：username 一致，密码由 MES 掌握。
这里只存「谁对应哪个 MES 账号」以及给技能进程用的凭据副本，
方便技能以该用户身份访问 MES（权限随 MES 角色走）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class MesUserBinding(Base):
    """本地用户到 MES 账号的映射。"""

    __tablename__ = "mes_user_bindings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    mes_username: Mapped[str] = mapped_column(String(128), index=True)
    # 技能进程需要以该用户身份登录 MES，因此保存口令；仅内部接口可读，
    # 不通过任何公开 API 暴露，也不写日志。
    mes_password: Mapped[str] = mapped_column(Text, default="")
    mes_role_code: Mapped[str] = mapped_column(String(64), default="")
    mes_is_admin: Mapped[bool] = mapped_column(default=False)
    synced_at: Mapped[datetime] = mapped_column(server_default=func.now())
