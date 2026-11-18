"""读 MES 的只读客户端：拿令牌 + 查面板数据。

只做两件事：登录拿 token（带缓存），POST /api/px/queryFormDataList 拉列表。
不写 MES、不按钮、不审批——AI 助手的职责是「发现并通知」，动作仍然由人执行。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 20 * 60
REQUEST_TIMEOUT_SECONDS = 15.0


class MesWatchError(RuntimeError):
    """MES 不可达或返回异常。"""


@dataclass
class _CachedToken:
    value: str = ""
    expires_at: float = 0.0


class MesClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        access_token: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.mes_watch_base_url).rstrip("/")
        self.username = username or settings.mes_watch_username
        self.password = password if password is not None else settings.mes_watch_password
        # 用「调用者本人」的 MES token 时不再登录：查到的就是他能看的数据，
        # 越权由 MES 自己拒绝（2026-11-17 起 AI 侧工具优先走这条路）。
        if access_token:
            self._token = _CachedToken(value=access_token, expires_at=float("inf"))
        else:
            self._token = _CachedToken()

    # ---------- 认证 ----------

    def profile(self) -> dict[str, Any]:
        """登录并拿到这个账号的档案（含 visiblePanels / approvePanels / isAdmin）。

        权限判定要的正是这三样：MES 登录返回里就有，不必再调别的接口。
        """
        if not self.password:
            raise MesWatchError("未配置 MES 密码，无法判定权限")
        try:
            response = httpx.post(
                f"{self.base_url}/api/auth/login",
                json={"userName": self.username, "password": self.password},
                timeout=REQUEST_TIMEOUT_SECONDS,
                trust_env=False,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MesWatchError(f"MES 登录失败：{exc}") from exc
        data = payload.get("data") or {}
        token = str(data.get("token") or "")
        if token:
            self._token = _CachedToken(
                value=token, expires_at=time.monotonic() + TOKEN_TTL_SECONDS
            )
        profile = data.get("user") or {}
        return profile if isinstance(profile, dict) else {}

    def token(self) -> str:
        if self._token.value and time.monotonic() < self._token.expires_at:
            return self._token.value
        if not self.password:
            raise MesWatchError("未配置 MES_WATCH_PASSWORD，无法轮询 MES")
        try:
            response = httpx.post(
                f"{self.base_url}/api/auth/login",
                json={"userName": self.username, "password": self.password},
                timeout=REQUEST_TIMEOUT_SECONDS,
                trust_env=False,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MesWatchError(f"MES 登录失败：{exc}") from exc
        data = payload.get("data") or {}
        token = str(data.get("token") or "")
        if not token:
            raise MesWatchError(f"MES 登录未返回令牌：{payload.get('message') or ''}")
        self._token = _CachedToken(value=token, expires_at=time.monotonic() + TOKEN_TTL_SECONDS)
        return token

    # ---------- 查询 ----------

    def query_panel(
        self, panel_code: str, *, page_size: int = 200, condition: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        body = {
            "panelCode": panel_code,
            "pageNo": 1,
            "pageSize": page_size,
        }
        if condition:
            body["condition"] = condition
        try:
            response = httpx.post(
                f"{self.base_url}/api/px/queryFormDataList",
                json=body,
                headers={"Authorization": f"Bearer {self.token()}"},
                timeout=REQUEST_TIMEOUT_SECONDS,
                trust_env=False,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MesWatchError(f"查询 {panel_code} 失败：{exc}") from exc
        if str(payload.get("code")) not in ("200", "0"):
            raise MesWatchError(
                f"查询 {panel_code} 返回错误：{payload.get('message') or payload.get('code')}"
            )
        data = payload.get("data") or {}
        rows = data.get("rows") or data.get("list") or []
        return [row for row in rows if isinstance(row, dict)]

    def dashboard(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """调 MES 看板接口（用当前账号的令牌，权限与网页一致）。"""
        try:
            response = httpx.get(
                f"{self.base_url}/api/dashboard/{path.lstrip('/')}",
                params=params or {},
                headers={"Authorization": f"Bearer {self.token()}"},
                timeout=REQUEST_TIMEOUT_SECONDS,
                trust_env=False,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MesWatchError(f"调用 MES 看板 {path} 失败：{exc}") from exc
        if str(payload.get("code")) not in ("200", "0"):
            raise MesWatchError(f"MES 看板 {path} 返回错误：{payload.get('message') or payload.get('code')}")
        return payload.get("data")

    # ---------- 开放审批接口（只读，独立令牌） ----------

    def _open_get(self, prefix: str, path: str, params: dict[str, Any] | None = None) -> Any:
        if not settings.mes_open_api_enabled:
            raise MesWatchError("MES 开放接口未启用")
        token = (settings.mes_open_api_token or "").strip()
        if not token:
            raise MesWatchError("未配置 MES_OPEN_API_TOKEN")
        base = (settings.mes_open_api_base_url or "").rstrip("/")
        try:
            response = httpx.get(
                f"{base}/api/open/{prefix}/{path}",
                params=params or {},
                headers={"X-Open-Api-Token": token},
                timeout=REQUEST_TIMEOUT_SECONDS,
                trust_env=False,
            )
        except httpx.HTTPError as exc:
            raise MesWatchError(f"调用 MES 开放接口失败：{exc}") from exc
        if response.status_code >= 400:
            raise MesWatchError(
                f"MES 开放接口返回 {response.status_code}：{response.text[:120]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise MesWatchError("MES 开放接口返回的不是 JSON") from exc
        if str(body.get("code")) not in ("200", "0"):
            raise MesWatchError(f"MES 开放接口业务错误：{body.get('message') or body.get('code')}")
        return body.get("data")

    def _open_api_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._open_get("approvals", path, params)

    # ---------- 开放组织接口（只读：部门树 + 人员归属） ----------

    def org_map(self) -> dict[str, Any]:
        """组织快照：部门树 + 每个人的部门路径与岗位。

        群聊的「人力地图」靠它把成员挂到部门下；部门唯一来源是 MES，
        AI 侧只读不写，改部门仍在 MES 的组织架构页面。
        """
        data = self._open_get("org", "map")
        return data if isinstance(data, dict) else {}

    # ---------- 开放业务接口（只读：追踪 / 详情 / 库存） ----------

    def trace(self, doc_no: str) -> dict[str, Any]:
        """单据追踪：环节、卡点、下一步，和 MES 页面同一份结果。"""
        data = self._open_get("biz", "trace", {"docNo": doc_no})
        return data if isinstance(data, dict) else {}

    def form(self, panel_code: str, form_no: str) -> dict[str, Any]:
        """单据详情：表头 + 明细（字段已由 MES 按面板配置过滤）。"""
        data = self._open_get("biz", "form", {"panelCode": panel_code, "formNo": form_no})
        return data if isinstance(data, dict) else {}

    def stock(self, *, item_code: str = "", warehouse: str = "", limit: int = 50) -> dict[str, Any]:
        """库存结存：行 + 数量/金额合计。"""
        params: dict[str, Any] = {"limit": limit}
        if item_code:
            params["itemCode"] = item_code
        if warehouse:
            params["warehouse"] = warehouse
        data = self._open_get("biz", "stock", params)
        return data if isinstance(data, dict) else {}

    def approvers(self) -> list[dict[str, Any]]:
        data = self._open_api_get("approvers")
        return [row for row in (data or []) if isinstance(row, dict)]

    def pending_approvals(self, approver: str, *, limit: int = 50) -> list[dict[str, Any]]:
        data = self._open_api_get("pending", {"approver": approver, "limit": limit})
        items = (data or {}).get("items") if isinstance(data, dict) else None
        return [row for row in (items or []) if isinstance(row, dict)]

    def fetch_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """一次拉齐规则需要的面板；单个面板失败不影响其它面板。"""
        snapshot: dict[str, list[dict[str, Any]]] = {}
        for panel in (
            "SO_ORDER",
            "MANU_ORDER",
            "STOCK_STATUS",
            "PU_ORDER",
            "ARRIVAL_IN",
            "FINISH_INSPECT",
            "PROCESS_REPORT",
            "DISPATCH",       # 工单排产：判断「后面几道工序排不上」要用
            "MANU_ORDER_MATERIAL_DETAIL",  # 工单材料需求：没开领料单的工单也能判缺料
            "SALE_OUT",
            "MATERIAL_REQ",
            "MATERIAL_OUT",
        ):
            try:
                snapshot[panel] = self.query_panel(panel)
            except MesWatchError as exc:
                logger.warning("mes_watch.panel_failed", extra={"panel": panel, "error": str(exc)})
                snapshot[panel] = []
        return snapshot
