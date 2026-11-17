"""按「调用者本人」的 MES token 判权限。

为什么单独一层：
- MES 是权限权威。`/api/auth/userinfo` 会返回这个账号真正能看的 `visiblePanels`
  （实测 admin 157 个、worker 10 个且不含销售订单）。
- AI 侧工具过去用系统级账号查数，等于任何人都能看到全部数据（2026-11-17 实测复现）。
- 这里统一用「用户自己的 token」问 MES 要权限，再决定工具能不能查；
  没有 token 就明确拒绝，绝不退回系统账号。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from app.core.config import settings

PROFILE_TTL_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 8.0


class MesUnauthorized(RuntimeError):
    """没有可用的 MES 身份，或该账号无权查看此数据。"""


@dataclass
class MesViewer:
    token: str
    is_admin: bool
    panels: set[str]
    real_name: str
    loaded_at: float

    def can_view(self, panel: str) -> bool:
        if self.is_admin:
            return True
        return panel.upper() in self.panels


_CACHE: dict[str, MesViewer] = {}


def viewer_of(token: str) -> MesViewer:
    """用调用者的 token 取他的可见面板（短缓存，避免每轮都请求 MES）。"""
    raw = (token or "").strip()
    if not raw:
        raise MesUnauthorized("缺少 MES 登录态，无法按账号权限查数据")
    cached = _CACHE.get(raw)
    if cached and time.monotonic() - cached.loaded_at < PROFILE_TTL_SECONDS:
        return cached

    base_url = settings.mes_watch_base_url.rstrip("/")
    try:
        response = httpx.get(
            base_url + "/api/auth/userinfo",
            headers={"Authorization": "Bearer " + raw},
            timeout=REQUEST_TIMEOUT_SECONDS,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        raise MesUnauthorized("无法连接 MES 校验权限：" + str(exc)) from exc
    if response.status_code == 401:
        raise MesUnauthorized("MES 登录态已失效，请重新登录")
    if response.status_code >= 400:
        raise MesUnauthorized("MES 权限校验失败：" + str(response.status_code))
    try:
        data = (response.json() or {}).get("data") or {}
    except ValueError as exc:
        raise MesUnauthorized("MES 权限校验返回了非 JSON 内容") from exc

    panels = {str(item).upper() for item in (data.get("visiblePanels") or [])}
    viewer = MesViewer(
        token=raw,
        is_admin=bool(data.get("isAdmin")),
        panels=panels,
        real_name=str(data.get("realName") or data.get("userName") or ""),
        loaded_at=time.monotonic(),
    )
    _CACHE[raw] = viewer
    return viewer


def require_panel(token: str, panel: str) -> MesViewer:
    """没有权限就抛错：让模型如实说「你没有权限」，而不是让 MES 返回空数据。"""
    viewer = viewer_of(token)
    if not viewer.can_view(panel):
        raise MesUnauthorized(
            "当前账号没有「" + panel + "」的查看权限；如需查看请联系管理员开通"
        )
    return viewer


def invalidate(token: str = "") -> None:
    if token:
        _CACHE.pop(token, None)
    else:
        _CACHE.clear()