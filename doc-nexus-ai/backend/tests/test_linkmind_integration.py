from __future__ import annotations


import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core import database
from app.core.config import settings
from app.main import app
from app.models.system_config import SystemConfig
from app.services import linkmind


class FakeLinkMindClient:
    def __init__(self) -> None:
        self.keys: dict[int, linkmind.LinkMindKey] = {}
        self.calls: list[tuple[str, object]] = []
        self._next_id = 1
        self.initialization_error: linkmind.LinkMindError | None = None

    def create_key(self, user_id: str, *, remark: str) -> linkmind.LinkMindKey:
        with sqlite3.connect(settings.tokenlink_database_path) as connection:
            assert connection.execute(
                "SELECT 1 FROM users WHERE id = ?", (user_id,)
            ).fetchone() == (1,)
        key_id = self._next_id
        self._next_id += 1
        key = linkmind.LinkMindKey(
            id=key_id,
            user_id=user_id,
            key_hint=f"lmk_test_{key_id}...{key_id:04d}",
            remark=remark,
            status="active",
            created_at=1_700_000_000_000,
            last_used_at=None,
            revoked_at=None,
            token=f"lmk_test_secret_{key_id}",
        )
        self.keys[key_id] = key
        self.calls.append(("create", key))
        return key

    def initialize_box(self, actor_id: str) -> linkmind.LinkMindInitializationResult:
        assert any(
            key.user_id == actor_id and key.status == "active"
            for key in self.keys.values()
        )
        self.calls.append(("initialize", actor_id))
        if self.initialization_error is not None:
            error = self.initialization_error
            self.initialization_error = None
            raise error
        return linkmind.LinkMindInitializationResult(
            deleted_token_statistics=125,
            deleted_login_sessions=3,
            initialized_at=1_788_250_000_000,
        )

    def list_keys(self, user_id: str) -> list[linkmind.LinkMindKey]:
        self.calls.append(("list", user_id))
        return [key for key in self.keys.values() if key.user_id == user_id]

    def update_key(
        self,
        user_id: str,
        key_id: int,
        *,
        remark: str,
        existing: linkmind.LinkMindKey | None = None,
    ) -> linkmind.LinkMindKey:
        key = self.keys[key_id]
        assert key.user_id == user_id
        updated = linkmind.LinkMindKey(
            id=key.id,
            user_id=key.user_id,
            key_hint=key.key_hint,
            remark=remark,
            status=key.status,
            created_at=key.created_at,
            last_used_at=key.last_used_at,
            revoked_at=key.revoked_at,
        )
        self.keys[key_id] = updated
        self.calls.append(("update", updated))
        return updated

    def revoke_key(
        self,
        user_id: str,
        key_id: int,
        *,
        existing: linkmind.LinkMindKey | None = None,
    ) -> linkmind.LinkMindKey:
        key = self.keys[key_id]
        assert key.user_id == user_id
        updated = linkmind.LinkMindKey(
            id=key.id,
            user_id=key.user_id,
            key_hint=key.key_hint,
            remark=key.remark,
            status="revoked",
            created_at=key.created_at,
            last_used_at=key.last_used_at,
            revoked_at=1_700_000_100_000,
        )
        self.keys[key_id] = updated
        self.calls.append(("revoke", key_id))
        return updated

    def token_usage(self, user_id: str, endpoint: str, **params: object) -> dict:
        self.calls.append((endpoint, {"user_id": user_id, **params}))
        return {"user_id": user_id, "endpoint": endpoint}


def _configure_linkmind(tmp_path: Path) -> dict[str, object]:
    original = {
        "db_path": settings.db_path,
        "database_url": settings.database_url,
        "linkmind_enabled": settings.linkmind_enabled,
        "tokenlink_database_path": settings.tokenlink_database_path,
        "linkmind_base_url": settings.linkmind_base_url,
        "linkmind_public_url": settings.linkmind_public_url,
        "linkmind_key_encryption_key": settings.linkmind_key_encryption_key,
        "__model_fields_set__": settings.model_fields_set.copy(),
    }
    settings.db_path = tmp_path / "cuihua.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    settings.linkmind_enabled = True
    settings.tokenlink_database_path = str(tmp_path / "tokenlink" / "tokenlink.db")
    settings.linkmind_base_url = "http://linkmind.test"
    settings.linkmind_public_url = "http://localhost:8080"
    settings.linkmind_key_encryption_key = Fernet.generate_key().decode("ascii")
    (tmp_path / "tokenlink").mkdir()
    return original


def _restore_settings(original: dict[str, object], db_path: Path) -> None:
    database.reset_database(db_path)
    original_fields_set = original.pop("__model_fields_set__")
    for name, value in original.items():
        setattr(settings, name, value)
    settings.__pydantic_fields_set__ = original_fields_set


def _shared_user(db_path: Path, user_id: str) -> tuple[str, str, str]:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT role, user_login_status, display_name FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    assert row is not None
    return row[0], row[1], row[2]


def test_linkmind_provisioning_syncs_lifecycle_and_protects_token(
    tmp_path: Path, monkeypatch
):
    original = _configure_linkmind(tmp_path)
    fake_client = FakeLinkMindClient()
    monkeypatch.setattr(linkmind, "get_client", lambda: fake_client)
    try:
        with TestClient(app) as client:
            status = client.get("/auth/setup-status")
            assert status.status_code == 200
            assert status.json()["required"] is True
            assert status.json()["linkmind_enabled"] is True

            admin = client.post(
                "/auth/setup",
                json={
                    "new_password": "LinkMind-admin-password-123",
                    "password_confirmation": "LinkMind-admin-password-123",
                },
            )
            assert admin.status_code == 200
            admin_body = admin.json()
            assert admin_body["user"]["linkmind_enabled"] is True
            assert admin_body["user"]["first_login"] is False
            admin_user_id = admin_body["user"]["id"]
            assert admin_body["user"]["linkmind_sso_url"] == (
                f"http://localhost:8080/sso/login?userId={admin_user_id}"
            )
            headers = {"Authorization": f"Bearer {admin_body['access_token']}"}
            assert _shared_user(
                Path(settings.tokenlink_database_path), admin_user_id
            ) == ("system_admin", "logged_in", "系统管理员")
            assert fake_client.keys == {}

            ensured = client.post(
                f"/admin/users/{admin_user_id}/linkmind-api-keys:ensure",
                headers=headers,
            )
            assert ensured.status_code == 204
            assert fake_client.keys[1].remark == "admin-default"
            assert (
                sum(
                    item.user_id == admin_user_id
                    for kind, item in fake_client.calls
                    if kind == "create"
                )
                == 1
            )
            assert ("initialize", admin_user_id) in fake_client.calls
            ensured_again = client.post(
                f"/admin/users/{admin_user_id}/linkmind-api-keys:ensure",
                headers=headers,
            )
            assert ensured_again.status_code == 204
            assert sum(kind == "initialize" for kind, _ in fake_client.calls) == 1
            completed_onboarding = client.post(
                "/admin/onboarding/complete",
                headers=headers,
                json={"company_name": "缔智元", "persona_name": "翠花"},
            )
            assert completed_onboarding.status_code == 200
            assert sum(kind == "initialize" for kind, _ in fake_client.calls) == 1

            created = client.post(
                "/admin/users",
                headers=headers,
                json={
                    "username": "linkmind-user",
                    "email": "linkmind-user@example.com",
                    "display_name": "LinkMind 用户",
                },
            )
            assert created.status_code == 201
            created_body = created.json()
            user_id = created_body["user_id"]
            assert created_body["linkmind_key"]["remark"] == "linkmind-user-default"
            assert created_body["linkmind_key"]["token"] == "lmk_test_secret_2"
            created_calls = [
                item for kind, item in fake_client.calls if kind == "create"
            ]
            assert sum(item.user_id == user_id for item in created_calls) == 1
            assert _shared_user(Path(settings.tokenlink_database_path), user_id) == (
                "user",
                "logged_out",
                "LinkMind 用户",
            )

            accepted = client.post(
                f"/auth/invitations/{created_body['invite_token']}/accept",
                json={
                    "password": "LinkMind-user-password-123",
                    "password_confirmation": "LinkMind-user-password-123",
                },
            )
            assert accepted.status_code == 200
            member_login = client.post(
                "/auth/login",
                json={
                    "username": "linkmind-user",
                    "password": "LinkMind-user-password-123",
                },
            )
            assert member_login.status_code == 200
            assert member_login.json()["user"]["linkmind_enabled"] is True
            member_headers = {
                "Authorization": f"Bearer {member_login.json()['access_token']}"
            }
            assert _shared_user(Path(settings.tokenlink_database_path), user_id) == (
                "user",
                "logged_in",
                "LinkMind 用户",
            )

            usage = client.get(
                f"/admin/users/{user_id}/linkmind-token-usage/overview?range=week",
                headers=headers,
            )
            assert usage.status_code == 200
            assert usage.json() == {
                "user_id": user_id,
                "endpoint": "overview",
            }

            listed = client.get("/auth/linkmind-api-keys", headers=member_headers)
            assert listed.status_code == 200
            assert listed.json()["items"] == [
                {
                    "id": 2,
                    "user_id": user_id,
                    "key_hint": "lmk_test_2...0002",
                    "remark": "linkmind-user-default",
                    "status": "active",
                    "created_at": 1_700_000_000_000,
                    "last_used_at": None,
                    "revoked_at": None,
                    "can_reveal": True,
                }
            ]
            assert '"token":' not in listed.text
            logout = client.post("/auth/logout", headers=member_headers)
            assert logout.status_code == 204
            assert fake_client.keys[2].status == "active"
            assert _shared_user(Path(settings.tokenlink_database_path), user_id) == (
                "user",
                "logged_out",
                "LinkMind 用户",
            )

            updated = client.patch(
                f"/admin/users/{user_id}/linkmind-api-keys/2",
                headers=headers,
                json={"remark": "员工电脑-张三"},
            )
            assert updated.status_code == 200
            assert updated.json()["remark"] == "员工电脑-张三"

            revealed = client.post(
                "/auth/linkmind-api-keys/2:reveal", headers=member_headers
            )
            assert revealed.status_code == 200
            assert revealed.json() == {"token": "lmk_test_secret_2"}

            revoked = client.post(
                f"/admin/users/{user_id}/linkmind-api-keys/2:revoke",
                headers=headers,
            )
            assert revoked.status_code == 200
            assert revoked.json()["status"] == "revoked"
            assert fake_client.keys[2].status == "revoked"
    finally:
        _restore_settings(original, tmp_path / "cuihua.sqlite3")


def test_linkmind_client_uses_local_user_key_contract_without_admin_auth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path.endswith("/user-api-keys"):
            return httpx.Response(
                201,
                json={
                    "status": "success",
                    "data": {
                        "id": 7,
                        "userId": "usr_7",
                        "apiKey": "lmk_secret",
                        "keyHint": "lmk_abcd...wxyz",
                        "remark": "cuihua-default",
                        "status": "active",
                        "createdAt": 1_700_000_000_000,
                    },
                },
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "page": 1,
                        "pageSize": 100,
                        "total": 1,
                        "items": [
                            {
                                "id": 7,
                                "userId": "usr_7",
                                "keyHint": "lmk_abcd...wxyz",
                                "remark": "cuihua-default",
                                "status": "active",
                                "createdAt": 1_700_000_000_000,
                                "lastUsedAt": 1_700_000_010_000,
                                "revokedAt": None,
                            }
                        ],
                    },
                },
            )
        if request.method == "PATCH":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "id": 7,
                        "userId": "usr_7",
                        "keyHint": "lmk_abcd...wxyz",
                        "remark": "员工电脑-张三",
                        "status": "active",
                        "createdAt": 1_700_000_000_000,
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "id": 7,
                    "userId": "usr_7",
                    "status": "revoked",
                    "revokedAt": 1_700_000_020_000,
                },
            },
        )

    client = linkmind.LinkMindClient(
        base_url="http://linkmind.test",
        transport=httpx.MockTransport(handler),
    )
    created = client.create_key("usr_7", remark="cuihua-default")
    listed = client.list_keys("usr_7")[0]
    updated = client.update_key(
        "usr_7", created.id, remark="员工电脑-张三", existing=created
    )
    revoked = client.revoke_key("usr_7", created.id, existing=updated)

    assert created.token == "lmk_secret"
    assert created.key_hint == "lmk_abcd...wxyz"
    assert listed.status == "active"
    assert listed.last_used_at == 1_700_000_010_000
    assert updated.remark == "员工电脑-张三"
    assert revoked.status == "revoked"
    assert [request.method for request in requests] == ["POST", "GET", "PATCH", "POST"]
    assert requests[0].url.path == "/v1/integration/users/usr_7/user-api-keys"
    assert requests[1].url.params["status"] == "all"
    assert requests[1].url.params["pageSize"] == "100"
    assert requests[2].url.path.endswith("/user-api-keys/7")
    assert requests[3].url.path.endswith("/user-api-keys/7/revoke")
    assert all("authorization" not in request.headers for request in requests)


def test_reused_linkmind_setup_preserves_external_state_without_faking_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _configure_linkmind(tmp_path)
    fake_client = FakeLinkMindClient()
    monkeypatch.setattr(linkmind, "get_client", lambda: fake_client)
    monkeypatch.setattr(settings, "linkmind_initialize_box_on_setup", False)
    try:
        with TestClient(app) as client:
            initialized = client.post(
                "/auth/setup",
                json={"new_password": "LinkMind-admin-password-123",
                      "password_confirmation": "LinkMind-admin-password-123"},
            )
            assert initialized.status_code == 200
            session = initialized.json()
            headers = {"Authorization": f"Bearer {session['access_token']}"}
            ensured = client.post(
                f"/admin/users/{session['user']['id']}/linkmind-api-keys:ensure",
                headers=headers,
            )
            assert ensured.status_code == 204
            completion = client.post(
                "/admin/onboarding/complete", headers=headers,
                json={"company_name": "基线验证企业", "persona_name": "小助理"},
            )
            assert completion.status_code == 200
            assert client.get("/auth/setup-status").json()["required"] is False
        assert any(key.status == "active" for key in fake_client.keys.values())
        assert not any(kind == "initialize" for kind, _ in fake_client.calls)
        with database.transaction() as db:
            assert db.get(SystemConfig, linkmind.LINKMIND_BOX_INITIALIZED_CONFIG_KEY) is None
    finally:
        _restore_settings(original, tmp_path / "cuihua.sqlite3")


def test_unknown_initialize_outcome_is_not_retried_during_onboarding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _configure_linkmind(tmp_path)
    fake_client = FakeLinkMindClient()
    fake_client.initialization_error = linkmind.LinkMindError(
        "LinkMind 服务请求超时",
        timed_out=True,
        outcome_unknown=True,
    )
    monkeypatch.setattr(linkmind, "get_client", lambda: fake_client)
    try:
        with TestClient(app) as client:
            initialized = client.post(
                "/auth/setup",
                json={
                    "new_password": "LinkMind-admin-password-123",
                    "password_confirmation": "LinkMind-admin-password-123",
                },
            )
            assert initialized.status_code == 200
            body = initialized.json()
            admin_user_id = body["user"]["id"]
            headers = {"Authorization": f"Bearer {body['access_token']}"}

            first_attempt = client.post(
                f"/admin/users/{admin_user_id}/linkmind-api-keys:ensure",
                headers=headers,
            )
            assert first_attempt.status_code == 503

            completion = client.post(
                "/admin/onboarding/complete",
                headers=headers,
                json={"company_name": "缔智元", "persona_name": "翠花"},
            )
            assert completion.status_code == 503
            assert "结果未知" in completion.json()["detail"]
            assert sum(kind == "initialize" for kind, _ in fake_client.calls) == 1

        with database.transaction() as db:
            initialization_state = db.get(
                SystemConfig, linkmind.LINKMIND_BOX_INITIALIZED_CONFIG_KEY
            )
            onboarding_state = db.get(SystemConfig, "onboarding_completed")
        assert initialization_state is not None
        assert (
            initialization_state.value == linkmind.LINKMIND_BOX_INITIALIZATION_STARTED
        )
        assert onboarding_state is None
    finally:
        _restore_settings(original, tmp_path / "cuihua.sqlite3")


def test_linkmind_client_initializes_box_with_actor_header() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "deletedTokenStatistics": 125,
                    "deletedLoginSessions": 3,
                    "initializedAt": 1_788_250_000_000,
                },
            },
        )

    client = linkmind.LinkMindClient(
        base_url="http://linkmind.test",
        transport=httpx.MockTransport(handler),
    )
    result = client.initialize_box("usr_system_admin")

    assert result == linkmind.LinkMindInitializationResult(
        deleted_token_statistics=125,
        deleted_login_sessions=3,
        initialized_at=1_788_250_000_000,
    )
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/integration/initialize"
    assert request.headers["X-Cuihua-Actor-Id"] == "usr_system_admin"
    assert json.loads(request.content) == {"confirmation": "RESET_USAGE_AND_SESSIONS"}


def test_linkmind_client_paginates_user_keys() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        item_start = 1 if page == 1 else 101
        count = 100 if page == 1 else 1
        items = [
            {
                "id": item_start + index,
                "userId": "usr_many",
                "keyHint": f"lmk_{item_start + index}",
                "remark": "cuihua-default",
                "status": "active",
                "createdAt": 1_700_000_000_000 + index,
            }
            for index in range(count)
        ]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "page": page,
                    "pageSize": 100,
                    "total": 101,
                    "items": items,
                },
            },
        )

    client = linkmind.LinkMindClient(
        base_url="http://linkmind.test", transport=httpx.MockTransport(handler)
    )
    keys = client.list_keys("usr_many")
    assert len(keys) == 101
    assert keys[-1].id == 101


def test_linkmind_client_maps_new_contract_errors() -> None:
    client = linkmind.LinkMindClient(
        base_url="http://linkmind.test",
        transport=httpx.MockTransport(lambda _: httpx.Response(409)),
    )
    with pytest.raises(linkmind.LinkMindError) as error:
        client.revoke_key("usr_7", 7)
    assert error.value.status_code == 409

    removed = linkmind.LinkMindClient(
        base_url="http://linkmind.test",
        transport=httpx.MockTransport(lambda _: httpx.Response(410)),
    )
    with pytest.raises(linkmind.LinkMindError) as error:
        removed.create_key("usr_7", remark="cuihua-default")
    assert error.value.status_code == 410


def test_linkmind_client_timeout_is_not_retried_as_duplicate_creation() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    client = linkmind.LinkMindClient(
        base_url="http://linkmind.test", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(linkmind.LinkMindError) as error:
        client.create_key("usr_7", remark="cuihua-default")
    assert error.value.timed_out is True
    assert error.value.outcome_unknown is True
    assert len(requests) == 1
