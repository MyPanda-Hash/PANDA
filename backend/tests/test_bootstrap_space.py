"""Tests for first-run database bootstrap."""

from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core import database
from app.core.config import settings
from app.core.security import hash_password, now, verify_password
from app.main import app
from app.models.auth import User
from app.models.model_endpoints import ModelEndpoint
from app.models.system_config import SystemConfig
from app.repositories import users
from app.services import linkmind
from app.services.default_endpoints import (
    DEFAULT_CHAT_EXTRA_MODELS,
    DEFAULT_CHAT_MODEL,
    DEFAULT_EMBEDDING_MODEL,
    default_chat_url,
    default_embedding_url,
)


def test_public_bootstrap_setup_is_gated_by_first_login_once_and_creates_default_space(
    tmp_path: Path,
) -> None:
    original_path = settings.db_path
    original_url = settings.database_url
    original_environment = settings.environment
    path = tmp_path / "bootstrap-space.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    settings.environment = "production"
    try:
        with TestClient(app) as client:
            status = client.get("/auth/setup-status")
            assert status.status_code == 200
            assert status.json()["required"] is True
            assert status.json()["username"] == settings.bootstrap_username

            mismatched_passwords = client.post(
                "/auth/setup",
                json={
                    "new_password": "Admin-password-123",
                    "password_confirmation": "Another-password-123",
                },
            )
            assert mismatched_passwords.status_code == 422
            placeholder_password = client.post(
                "/auth/setup",
                json={
                    "new_password": settings.bootstrap_password,
                    "password_confirmation": settings.bootstrap_password,
                },
            )
            assert placeholder_password.status_code == 422

            initialized = client.post(
                "/auth/setup",
                json={
                    "new_password": "Admin-password-123",
                    "password_confirmation": "Admin-password-123",
                },
            )
            assert initialized.status_code == 200
            assert initialized.json()["user"]["first_login"] is False
            bootstrap_user_id = initialized.json()["user"]["id"]
            assert bootstrap_user_id.startswith("usr_")
            UUID(bootstrap_user_id.removeprefix("usr_"))
            headers = {"Authorization": f"Bearer {initialized.json()['access_token']}"}

            listed = client.get("/knowledge-spaces", headers=headers)
            assert listed.status_code == 200
            spaces = listed.json()
            assert len(spaces) == 1
            assert spaces[0]["name"] == "默认资料空间"
            assert spaces[0]["owner_user_id"] == initialized.json()["user"]["id"]
            space_id = spaces[0]["id"]

            categories = client.get(
                f"/knowledge-spaces/{space_id}/categories", headers=headers
            )
            assert categories.status_code == 200
            assert [item["name"] for item in categories.json()] == [
                "产品文档",
                "技术文档",
                "API文档",
                "设计文档",
                "其他",
            ]

        with TestClient(app) as client:
            status = client.get("/auth/setup-status")
            assert status.json()["required"] is False
            login = client.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": "Admin-password-123",
                },
            )
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            listed = client.get("/knowledge-spaces", headers=headers)
            assert [item["id"] for item in listed.json()] == [space_id]
            repeat_setup = client.post(
                "/auth/setup",
                json={
                    "new_password": "Another-password-123",
                    "password_confirmation": "Another-password-123",
                },
            )
            assert repeat_setup.status_code == 409
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        settings.environment = original_environment
        database.reset_database(path)


def test_production_bootstrap_without_install_secret_fails_closed(
    tmp_path: Path,
) -> None:
    original_path = settings.db_path
    original_url = settings.database_url
    original_environment = settings.environment
    original_fields_set = settings.model_fields_set.copy()
    path = tmp_path / "bootstrap-no-initial-password.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    settings.environment = "production"
    try:
        settings.__pydantic_fields_set__.discard("bootstrap_password")
        with pytest.raises(RuntimeError, match="DOC_NEXUS_BOOTSTRAP_PASSWORD"):
            with TestClient(app):
                pass
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        settings.environment = original_environment
        settings.__pydantic_fields_set__ = original_fields_set
        database.reset_database(path)


def test_initialized_admin_is_not_reset_without_setup_audit(tmp_path: Path) -> None:
    original_path = settings.db_path
    original_url = settings.database_url
    path = tmp_path / "legacy-bootstrap.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    try:
        with TestClient(app):
            pass
        with database.transaction() as db:
            user = (
                db.query(User)
                .filter(User.username == settings.bootstrap_username)
                .one()
            )
            user.first_login = False

        with TestClient(app) as client:
            status = client.get("/auth/setup-status")
            assert status.status_code == 200
            assert status.json()["required"] is False
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        database.reset_database(path)


def test_bootstrap_never_promotes_an_unrelated_existing_user(tmp_path: Path) -> None:
    original_path = settings.db_path
    original_url = settings.database_url
    path = tmp_path / "existing-user-before-bootstrap.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    try:
        database.reset_database(path)
        database.initialize_database()
        with database.transaction() as db:
            db.add(
                User(
                    id="usr_existing_member",
                    username="existing-member",
                    email="existing-member@example.com",
                    display_name="现有成员",
                    password_hash=hash_password("Member-password-123"),
                    role="user",
                    first_login=False,
                    created_at=now(),
                )
            )

        with TestClient(app) as client:
            status = client.get("/auth/setup-status")
            member = client.post(
                "/auth/login",
                json={
                    "username": "existing-member",
                    "password": "Member-password-123",
                },
            )

        assert status.status_code == 200
        assert status.json()["required"] is True
        assert member.status_code == 200
        assert "user:manage" not in member.json()["user"]["permissions"]
        with database.transaction() as db:
            assert (
                db.query(User)
                .filter(User.username == settings.bootstrap_username)
                .one_or_none()
                is not None
            )
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        database.reset_database(path)


def test_bootstrap_rejects_username_owned_by_an_unrelated_user(tmp_path: Path) -> None:
    original_path = settings.db_path
    original_url = settings.database_url
    path = tmp_path / "conflicting-bootstrap-username.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    try:
        database.reset_database(path)
        database.initialize_database()
        with database.transaction() as db:
            db.add(
                User(
                    id="usr_unrelated_admin_name",
                    username=settings.bootstrap_username,
                    email="unrelated-admin-name@example.com",
                    display_name="同名普通成员",
                    password_hash=hash_password("Member-password-123"),
                    role="user",
                    first_login=False,
                    created_at=now(),
                )
            )

        with pytest.raises(RuntimeError, match="bootstrap username"), TestClient(app):
            pass
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        database.reset_database(path)


def test_existing_bootstrap_identity_survives_username_config_change(
    tmp_path: Path,
) -> None:
    original_path = settings.db_path
    original_url = settings.database_url
    original_username = settings.bootstrap_username
    original_environment = settings.environment
    original_password = settings.bootstrap_password
    path = tmp_path / "renamed-bootstrap-config.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    try:
        database.reset_database(path)
        database.initialize_database()
        with database.transaction() as db:
            legacy_user = User(
                id="usr_legacy_bootstrap",
                username="legacy-bootstrap",
                email="legacy-bootstrap@example.com",
                display_name="既有系统管理员",
                password_hash=hash_password("Admin-password-123"),
                role="admin",
                first_login=False,
                created_at=now(),
            )
            db.add(legacy_user)
            db.flush()
            users.remember_bootstrap_admin(db, legacy_user)
        settings.bootstrap_username = "new-bootstrap-name"

        with TestClient(app) as client:
            status = client.get("/auth/setup-status")

        assert status.status_code == 200
        assert status.json()["required"] is False
        assert status.json()["username"] == "legacy-bootstrap"
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        settings.bootstrap_username = original_username
        settings.environment = original_environment
        settings.bootstrap_password = original_password
        database.reset_database(path)


def test_bootstrap_login_block_applies_to_marker_admin_after_username_change(
    tmp_path: Path,
) -> None:
    """生产环境下，即使配置的 bootstrap 用户名在初始化前被修改，
    旧 bootstrap admin（由 marker 固化）也不能绕过初始化链接直接登录。"""
    original_path = settings.db_path
    original_url = settings.database_url
    original_username = settings.bootstrap_username
    original_environment = settings.environment
    original_password = settings.bootstrap_password
    path = tmp_path / "renamed-bootstrap-block.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    try:
        database.reset_database(path)
        database.initialize_database()
        with database.transaction() as db:
            legacy_user = User(
                id="usr_legacy_bootstrap",
                username="legacy-bootstrap",
                email="legacy-bootstrap@example.com",
                display_name="既有系统管理员",
                password_hash=hash_password("Install-secret-123"),
                role="admin",
                first_login=True,
                created_at=now(),
            )
            db.add(legacy_user)
            db.flush()
            users.remember_bootstrap_admin(db, legacy_user)
        settings.environment = "production"
        settings.bootstrap_password = "Install-secret-123"
        settings.bootstrap_username = "new-bootstrap-name"

        with TestClient(app) as client:
            blocked = client.post(
                "/auth/login",
                json={
                    "username": "legacy-bootstrap",
                    "password": "Install-secret-123",
                },
            )
            assert blocked.status_code == 403
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        settings.bootstrap_username = original_username
        settings.environment = original_environment
        settings.bootstrap_password = original_password
        database.reset_database(path)


def test_startup_rotates_pending_bootstrap_password(tmp_path: Path) -> None:
    original_path = settings.db_path
    original_url = settings.database_url
    original_environment = settings.environment
    original_password = settings.bootstrap_password
    path = tmp_path / "rotated-setup-secret.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    try:
        database.reset_database(path)
        database.initialize_database()
        with database.transaction() as db:
            pending_admin = User(
                id="usr_pending_bootstrap",
                username=settings.bootstrap_username,
                email="pending-bootstrap@example.com",
                display_name="待初始化管理员",
                password_hash=hash_password("Old-install-secret-123"),
                role="admin",
                first_login=True,
                created_at=now(),
            )
            db.add(pending_admin)
            db.flush()
            users.remember_bootstrap_admin(db, pending_admin)

        settings.environment = "production"
        settings.bootstrap_password = "New-install-secret-123"
        with TestClient(app):
            with database.transaction() as db:
                pending_admin = users.get_bootstrap_admin(
                    db, settings.bootstrap_username
                )
                assert pending_admin is not None
                assert verify_password(
                    pending_admin.password_hash, "New-install-secret-123"
                )
                assert not verify_password(
                    pending_admin.password_hash, "Old-install-secret-123"
                )
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        settings.environment = original_environment
        settings.bootstrap_password = original_password
        database.reset_database(path)


def test_linkmind_bootstrap_requires_setup_and_installs_fixed_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_path = settings.db_path
    original_url = settings.database_url
    original_environment = settings.environment
    original_linkmind_enabled = settings.linkmind_enabled
    original_linkmind_base_url = settings.linkmind_base_url
    path = tmp_path / "linkmind-bootstrap.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    settings.environment = "production"
    settings.linkmind_enabled = True
    settings.linkmind_base_url = "http://linkmind.example:8080"
    synced_user_ids: list[str] = []
    provisioned_user_ids: list[str] = []
    initialized_user_ids: list[str] = []
    monkeypatch.setattr("app.services.linkmind.sync_user_by_id", synced_user_ids.append)
    monkeypatch.setattr(
        "app.services.linkmind.sync_user",
        lambda _db, user: synced_user_ids.append(user.id),
    )
    monkeypatch.setattr(
        "app.services.linkmind.ensure_user_key", provisioned_user_ids.append
    )
    monkeypatch.setattr(
        "app.services.linkmind.initialize_box", initialized_user_ids.append
    )
    try:
        with TestClient(app) as client:
            status = client.get("/auth/setup-status")
            assert status.status_code == 200
            assert status.json()["required"] is True
            assert status.json()["linkmind_enabled"] is True

            public_password_login = client.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": "88888888",
                },
            )
            assert public_password_login.status_code == 401

            direct_bootstrap_login = client.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": settings.bootstrap_password,
                },
            )
            assert direct_bootstrap_login.status_code == 403

            initialized = client.post(
                "/auth/setup",
                json={
                    "new_password": "LinkMind-admin-password-123",
                    "password_confirmation": "LinkMind-admin-password-123",
                },
            )
            assert initialized.status_code == 200
            admin_user_id = initialized.json()["user"]["id"]
            assert initialized.json()["user"]["first_login"] is False
            admin_headers = {
                "Authorization": f"Bearer {initialized.json()['access_token']}"
            }
            assert (
                client.get("/admin/model-endpoints", headers=admin_headers).status_code
                == 200
            )
            assert client.get("/auth/setup-status").json()["required"] is False
            onboarding_status = client.get(
                "/admin/onboarding/status", headers=admin_headers
            )
            assert onboarding_status.status_code == 200
            assert onboarding_status.json()["required"] is True
            completed_onboarding = client.post(
                "/admin/onboarding/complete",
                headers=admin_headers,
                json={"company_name": "缔智元", "persona_name": "翠花"},
            )
            assert completed_onboarding.status_code == 200
            old_installer_login = client.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": settings.bootstrap_password,
                },
            )
            assert old_installer_login.status_code == 401
            repeat_setup = client.post(
                "/auth/setup",
                json={
                    "new_password": "Another-password-123",
                    "password_confirmation": "Another-password-123",
                },
            )
            assert repeat_setup.status_code == 409

        assert synced_user_ids == [admin_user_id, admin_user_id]
        assert provisioned_user_ids == [admin_user_id]
        assert initialized_user_ids == [admin_user_id]

        with database.transaction() as db:
            chat = (
                db.query(ModelEndpoint)
                .filter(
                    ModelEndpoint.capability == "chat",
                    ModelEndpoint.endpoint_url == default_chat_url(),
                )
                .one()
            )
            embedding = (
                db.query(ModelEndpoint)
                .filter(
                    ModelEndpoint.capability == "embedding",
                    ModelEndpoint.endpoint_url == default_embedding_url(),
                )
                .one()
            )
            assert chat.secret_ref == linkmind.LINKMIND_ENDPOINT_SECRET_REF
            assert embedding.secret_ref == linkmind.LINKMIND_ENDPOINT_SECRET_REF
            assert chat.model == DEFAULT_CHAT_MODEL
            assert chat.extra_models == DEFAULT_CHAT_EXTRA_MODELS.replace(",", ", ")
            assert [profile.model for profile in chat.models] == [
                DEFAULT_CHAT_MODEL,
                *DEFAULT_CHAT_EXTRA_MODELS.split(","),
            ]
            assert embedding.model == DEFAULT_EMBEDDING_MODEL
            assert [profile.model for profile in embedding.models] == [
                DEFAULT_EMBEDDING_MODEL
            ]
            assert db.get(SystemConfig, "pi_chat_model").value == chat.id
            assert db.get(SystemConfig, "embedding_model").value == embedding.id
            assert db.get(SystemConfig, "app_name").value == "缔智元"
            assert db.get(SystemConfig, "pi_persona_name").value == "翠花"
            assert db.get(SystemConfig, "onboarding_completed").value == "1"
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        settings.environment = original_environment
        settings.linkmind_enabled = original_linkmind_enabled
        settings.linkmind_base_url = original_linkmind_base_url
        database.reset_database(path)
