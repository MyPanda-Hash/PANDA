from __future__ import annotations

from pathlib import Path
import pytest


from sqlalchemy import select

from app.core import database
from app.models.access import Role
from app.models.auth import User
from app.core.config import settings
from app.main import app
from fastapi.testclient import TestClient
from app.repositories import access, system_config


@pytest.fixture(autouse=True)
def _development_auth_environment():
    original = settings.environment
    settings.environment = "development"
    try:
        yield
    finally:
        settings.environment = original


def test_builtin_role_description_tracks_default_until_edited(tmp_path: Path):
    """内置角色描述只在仍是历史默认值时跟随新文案；管理员改过的不会被覆盖。"""
    original_path = settings.db_path
    original_url = settings.database_url
    path = tmp_path / "role-copy.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    try:
        database.initialize_database()
        with database.SessionLocal() as db:
            access.seed_system_access(db)
            db.commit()
            auditor = db.scalar(select(Role).where(Role.code == "auditor"))
            assert auditor is not None
            assert auditor.description == "查看审计事件和授权元数据"
            assert "治理" not in auditor.description

            # 旧版本的默认描述会被新文案取代。
            auditor.description = "查看治理事件和授权元数据"
            db.commit()
            access.seed_system_access(db)
            db.commit()
            assert auditor.description == "查看审计事件和授权元数据"

            # 管理员自定义过的描述保持原样。
            auditor.description = "本司审计口径说明"
            db.commit()
            access.seed_system_access(db)
            db.commit()
            assert auditor.description == "本司审计口径说明"
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        database.reset_database(path)


def test_builtin_employee_role_is_seeded_without_system_permissions(tmp_path: Path):
    """员工角色在旧库补齐，重复初始化不重复创建，也不授予系统权限。"""
    original_path = settings.db_path
    original_url = settings.database_url
    path = tmp_path / "employee-role.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    try:
        database.initialize_database()
        with database.SessionLocal() as db:
            access.seed_system_access(db)
            db.commit()
            employee = db.scalar(select(Role).where(Role.code == "employee"))
            assert employee is not None
            assert employee.builtin is True
            assert employee.permissions == []
            employee_id = employee.id
            employee.name = "本司员工"
            db.commit()
            access.seed_system_access(db)
            db.commit()
            roles = db.scalars(select(Role).where(Role.code == "employee")).all()
            assert [role.id for role in roles] == [employee_id]
            assert roles[0].name == "本司员工"
            assert roles[0].permissions == []
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        database.reset_database(path)


def test_invite_login_first_login_and_logout(tmp_path: Path):
    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = tmp_path / "test.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = client.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": settings.bootstrap_password,
                },
            )
            assert admin.status_code == 200
            assert "space:create" in admin.json()["user"]["permissions"]
            admin_token = admin.json()["access_token"]
            invite = client.post(
                "/admin/users",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "username": "chenwei",
                    "email": "chenwei@example.com",
                    "display_name": "陈伟",
                },
            )
            assert invite.json()["api_key"].startswith("dnx_")
            with database.transaction() as db:
                persisted = db.get(User, invite.json()["user_id"])
                assert persisted is not None
                assert persisted.api_key == invite.json()["api_key"]
            token = invite.json()["invite_token"]
            inspected = client.get(f"/auth/invitations/{token}")
            assert inspected.status_code == 200
            assert inspected.json()["username"] == "chenwei"
            assert inspected.json()["email"] == "chenwei@example.com"
            accepted = client.post(
                f"/auth/invitations/{token}/accept",
                json={
                    "password": "Strong-password-123",
                    "password_confirmation": "Strong-password-123",
                },
            )
            assert accepted.status_code == 200
            assert accepted.json()["username"] == "chenwei"
            assert accepted.json()["email"] == "chenwei@example.com"
            assert "space:create" not in accepted.json()["permissions"]
            with database.transaction() as db:
                activated = db.get(User, invite.json()["user_id"])
                assert activated is not None
                assert activated.status == "active"
            second = client.post(
                f"/auth/invitations/{token}/accept",
                json={
                    "password": "Strong-password-123",
                    "password_confirmation": "Strong-password-123",
                },
            )
            assert second.status_code == 404
            login = client.post(
                "/auth/login",
                json={"username": "chenwei", "password": "Strong-password-123"},
            )
            assert login.status_code == 200
            assert "space:create" not in login.json()["user"]["permissions"]
            assert (
                client.post(
                    "/knowledge-spaces",
                    headers={"Authorization": f"Bearer {login.json()['access_token']}"},
                    json={"name": "无权创建", "description": ""},
                ).status_code
                == 403
            )
            user_token = login.json()["access_token"]
            api_key_response = client.get(
                "/auth/api-key", headers={"Authorization": f"Bearer {user_token}"}
            )
            assert api_key_response.status_code == 200
            assert api_key_response.json() == {"api_key": invite.json()["api_key"]}
            assert login.json()["user"]["first_login"] is False
            with database.transaction() as db:
                system_config.upsert(db, "app_name", "甲公司")
                system_config.upsert(db, "pi_persona_name", "乙助手")
            member_identity = client.get(
                "/auth/me", headers={"Authorization": f"Bearer {user_token}"}
            )
            assert member_identity.status_code == 200
            assert member_identity.json()["company_name"] == "甲公司"
            assert member_identity.json()["persona_name"] == "乙助手"
            member_workspace = client.get(
                "/workspace", headers={"Authorization": f"Bearer {user_token}"}
            )
            assert member_workspace.status_code == 200
            assert member_workspace.json()["user"]["company_name"] == "甲公司"
            assert member_workspace.json()["user"]["persona_name"] == "乙助手"
            assert (
                client.get(
                    "/auth/me", headers={"Authorization": f"Bearer {user_token}"}
                ).status_code
                == 200
            )
            # Email login (including mixed-case) must resolve the same account
            by_email = client.post(
                "/auth/login",
                json={
                    "username": "chenwei@example.com",
                    "password": "Strong-password-123",
                },
            )
            assert by_email.status_code == 200
            assert by_email.json()["user"]["id"] == login.json()["user"]["id"]
            by_email_case = client.post(
                "/auth/login",
                json={
                    "username": "ChenWei@Example.com",
                    "password": "Strong-password-123",
                },
            )
            assert by_email_case.status_code == 200
            assert by_email_case.json()["user"]["username"] == "chenwei"
            logged_out = client.post(
                "/auth/logout", headers={"Authorization": f"Bearer {user_token}"}
            )
            assert logged_out.status_code == 204
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(tmp_path / "test.sqlite3")


def test_display_name_update(tmp_path: Path):
    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = tmp_path / "display-name.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = client.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": settings.bootstrap_password,
                },
            ).json()
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            updated = client.patch(
                "/auth/display-name",
                headers=headers,
                json={"display_name": " 新展示名 "},
            )
            assert updated.status_code == 200
            assert updated.json()["display_name"] == "新展示名"
            me = client.get("/auth/me", headers=headers)
            assert me.status_code == 200
            assert me.json()["display_name"] == "新展示名"
            rejected = client.patch(
                "/auth/display-name",
                headers=headers,
                json={"display_name": "   "},
            )
            assert rejected.status_code == 422
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(tmp_path / "display-name.sqlite3")


def test_password_change_and_lockout(tmp_path: Path):
    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = tmp_path / "lockout.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = client.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": settings.bootstrap_password,
                },
            ).json()
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            changed = client.post(
                "/auth/change-password",
                headers=headers,
                json={
                    "current_password": settings.bootstrap_password,
                    "new_password": "Bootstrap-new-123",
                    "password_confirmation": "Bootstrap-new-123",
                },
            )
            assert changed.status_code == 200
            assert changed.json()["first_login"] is False
            assert client.get("/auth/me", headers=headers).status_code == 401
            assert client.get("/auth/api-key", headers=headers).status_code == 401
            assert (
                client.post(
                    "/auth/login",
                    json={
                        "username": settings.bootstrap_username,
                        "password": settings.bootstrap_password,
                    },
                ).status_code
                == 401
            )
            fresh_login = client.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": "Bootstrap-new-123",
                },
            )
            assert fresh_login.status_code == 200
            fresh_headers = {
                "Authorization": f"Bearer {fresh_login.json()['access_token']}"
            }
            assert (
                client.post(
                    "/auth/change-password",
                    headers=fresh_headers,
                    json={
                        "current_password": "wrong-old",
                        "new_password": "Another-password-123",
                        "password_confirmation": "Another-password-123",
                    },
                ).status_code
                == 400
            )
            for _ in range(5):
                assert (
                    client.post(
                        "/auth/login",
                        json={
                            "username": settings.bootstrap_username,
                            "password": "wrong-password",
                        },
                    ).status_code
                    == 401
                )
            assert (
                client.post(
                    "/auth/login",
                    json={
                        "username": settings.bootstrap_username,
                        "password": "Bootstrap-new-123",
                    },
                ).status_code
                == 423
            )
            client.get("/health")
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(tmp_path / "lockout.sqlite3")


def test_password_change_revokes_other_refresh_sessions_but_keeps_current(
    tmp_path: Path,
) -> None:
    original = settings.db_path
    original_url = settings.database_url
    path = tmp_path / "password-sessions.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    try:
        with TestClient(app) as current, TestClient(app) as other:
            current_login = current.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": settings.bootstrap_password,
                },
            )
            other_login = other.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": settings.bootstrap_password,
                },
            )
            assert current_login.status_code == other_login.status_code == 200

            changed = current.post(
                "/auth/change-password",
                headers={
                    "Authorization": f"Bearer {current_login.json()['access_token']}"
                },
                json={
                    "current_password": settings.bootstrap_password,
                    "new_password": "Bootstrap-new-123",
                    "password_confirmation": "Bootstrap-new-123",
                },
            )
            assert changed.status_code == 200
            assert current.post("/auth/refresh").status_code == 200
            assert other.post("/auth/refresh").status_code == 401
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(path)


def test_private_space_isolation_returns_404(tmp_path: Path):
    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = tmp_path / "spaces.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = client.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": settings.bootstrap_password,
                },
            ).json()
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
            space = client.post(
                "/knowledge-spaces",
                headers=admin_headers,
                json={"name": "仅授权空间", "description": "权限隔离测试"},
            ).json()
            invited = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "space-reader",
                    "email": "space-reader@example.com",
                    "display_name": "空间读者",
                },
            ).json()
            client.post(
                f"/auth/invitations/{invited['invite_token']}/accept",
                json={
                    "password": "Reader-password-123",
                    "password_confirmation": "Reader-password-123",
                },
            )
            reader = client.post(
                "/auth/login",
                json={"username": "space-reader", "password": "Reader-password-123"},
            ).json()
            reader_headers = {"Authorization": f"Bearer {reader['access_token']}"}
            assert (
                client.post(
                    f"/knowledge-spaces/{space['id']}/members",
                    headers=admin_headers,
                    json={"user_id": reader["user"]["id"], "role": "reader"},
                ).status_code
                == 201
            )
            assert (
                client.get(
                    f"/knowledge-spaces/{space['id']}", headers=reader_headers
                ).status_code
                == 200
            )
            invited_b = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "unrelated",
                    "email": "unrelated@example.com",
                    "display_name": "无关用户",
                },
            ).json()
            client.post(
                f"/auth/invitations/{invited_b['invite_token']}/accept",
                json={
                    "password": "Unrelated-password-123",
                    "password_confirmation": "Unrelated-password-123",
                },
            )
            unrelated = client.post(
                "/auth/login",
                json={"username": "unrelated", "password": "Unrelated-password-123"},
            ).json()
            assert (
                client.get(
                    f"/knowledge-spaces/{space['id']}",
                    headers={"Authorization": f"Bearer {unrelated['access_token']}"},
                ).status_code
                == 404
            )
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(tmp_path / "spaces.sqlite3")


def test_role_and_member_patch_delete_contract(tmp_path: Path):
    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = tmp_path / "management.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = client.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": settings.bootstrap_password,
                },
            ).json()
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
            permissions = client.get("/admin/permissions", headers=admin_headers).json()
            role = client.post(
                "/admin/roles",
                headers=admin_headers,
                json={
                    "code": "space_operator",
                    "name": "空间运营",
                    "permission_codes": [permissions[0]["code"]],
                },
            ).json()
            patched = client.patch(
                f"/admin/roles/{role['id']}",
                headers=admin_headers,
                json={"name": "空间运营（更新）"},
            ).json()
            assert patched["name"] == "空间运营（更新）"
            assert (
                client.patch(
                    "/admin/roles/does-not-exist",
                    headers=admin_headers,
                    json={"name": "不存在"},
                ).status_code
                == 404
            )
            builtin = next(
                item
                for item in client.get("/admin/roles", headers=admin_headers).json()
                if item["builtin"]
            )
            system_admin = next(
                item
                for item in client.get("/admin/roles", headers=admin_headers).json()
                if item["code"] == "system_admin"
            )
            bootstrap_role_change = client.put(
                f"/admin/users/{admin['user']['id']}/roles",
                headers=admin_headers,
                json={"role_ids": []},
            )
            assert bootstrap_role_change.status_code == 409
            assert system_admin["id"] in {
                role["id"]
                for role in client.get(
                    f"/admin/users/{admin['user']['id']}/roles",
                    headers=admin_headers,
                ).json()
            }
            assert (
                client.delete(
                    f"/admin/roles/{builtin['id']}", headers=admin_headers
                ).status_code
                == 409
            )
            assert (
                client.delete(
                    f"/admin/roles/{role['id']}", headers=admin_headers
                ).status_code
                == 204
            )

            role_user = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "role-user",
                    "email": "role-user@example.com",
                    "display_name": "角色用户",
                    "password": "Role-password-123",
                },
            ).json()
            auditor = next(
                item
                for item in client.get("/admin/roles", headers=admin_headers).json()
                if item["code"] == "auditor"
            )
            assigned = client.put(
                f"/admin/users/{role_user['user_id']}/roles",
                headers=admin_headers,
                json={"role_ids": [auditor["id"]]},
            )
            assert assigned.status_code == 200
            assert [item["code"] for item in assigned.json()] == ["auditor"]
            assert (
                client.get(
                    f"/admin/users/{role_user['user_id']}/roles", headers=admin_headers
                ).json()[0]["code"]
                == "auditor"
            )

            space = client.post(
                "/knowledge-spaces",
                headers=admin_headers,
                json={"name": "成员管理测试", "description": ""},
            ).json()
            invited = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "member-change",
                    "email": "member-change@example.com",
                    "display_name": "成员变更",
                },
            ).json()
            client.post(
                f"/auth/invitations/{invited['invite_token']}/accept",
                json={
                    "password": "Member-password-123",
                    "password_confirmation": "Member-password-123",
                },
            )
            assert (
                client.post(
                    f"/knowledge-spaces/{space['id']}/members",
                    headers=admin_headers,
                    json={"user_id": invited["user_id"], "role": "reader"},
                ).status_code
                == 201
            )
            updated = client.patch(
                f"/knowledge-spaces/{space['id']}/members/{invited['user_id']}",
                headers=admin_headers,
                json={"role": "contributor"},
            )
            assert (
                updated.status_code == 200 and updated.json()["role"] == "contributor"
            )
            assert updated.json()["display_name"] == "成员变更"
            assert (
                client.delete(
                    f"/knowledge-spaces/{space['id']}/members/{invited['user_id']}",
                    headers=admin_headers,
                ).status_code
                == 204
            )
            # Re-add by username / email identifiers
            by_username = client.post(
                f"/knowledge-spaces/{space['id']}/members",
                headers=admin_headers,
                json={"identifier": "member-change", "role": "reader"},
            )
            assert by_username.status_code == 201
            assert by_username.json()["username"] == "member-change"
            assert (
                client.delete(
                    f"/knowledge-spaces/{space['id']}/members/{invited['user_id']}",
                    headers=admin_headers,
                ).status_code
                == 204
            )
            by_email = client.post(
                f"/knowledge-spaces/{space['id']}/members",
                headers=admin_headers,
                json={"identifier": "member-change@example.com", "role": "reader"},
            )
            assert by_email.status_code == 201
            assert by_email.json()["email"] == "member-change@example.com"
            assert (
                client.delete(
                    f"/knowledge-spaces/{space['id']}/members/{invited['user_id']}",
                    headers=admin_headers,
                ).status_code
                == 204
            )
            member_login = client.post(
                "/auth/login",
                json={"username": "member-change", "password": "Member-password-123"},
            ).json()
            assert (
                client.get(
                    f"/knowledge-spaces/{space['id']}",
                    headers={"Authorization": f"Bearer {member_login['access_token']}"},
                ).status_code
                == 404
            )
            assert (
                client.delete(
                    f"/knowledge-spaces/{space['id']}/members/{admin['user']['id']}",
                    headers=admin_headers,
                ).status_code
                == 409
            )
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(tmp_path / "management.sqlite3")


def test_add_all_active_space_members(tmp_path: Path):
    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = tmp_path / "bulk-members.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = client.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": settings.bootstrap_password,
                },
            ).json()
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
            space = client.post(
                "/knowledge-spaces",
                headers=admin_headers,
                json={"name": "全员空间", "description": ""},
            ).json()
            employee_a = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "bulk-a",
                    "email": "bulk-a@example.com",
                    "display_name": "员工 A",
                },
            ).json()
            employee_b = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "bulk-b",
                    "email": "bulk-b@example.com",
                    "display_name": "员工 B",
                },
            ).json()
            deleted = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "bulk-deleted",
                    "email": "bulk-deleted@example.com",
                    "display_name": "已删除员工",
                },
            ).json()
            assert (
                client.delete(
                    f"/admin/users/{deleted['user_id']}", headers=admin_headers
                ).status_code
                == 200
            )
            assert (
                client.post(
                    f"/knowledge-spaces/{space['id']}/members",
                    headers=admin_headers,
                    json={"user_id": employee_a["user_id"], "role": "reader"},
                ).status_code
                == 201
            )

            bulk = client.post(
                f"/knowledge-spaces/{space['id']}/members:all",
                headers=admin_headers,
                json={"role": "contributor"},
            )
            assert bulk.status_code == 200
            assert bulk.json()["added_count"] == 1
            assert bulk.json()["existing_count"] == 2
            members = {item["user_id"]: item for item in bulk.json()["items"]}
            assert members[admin["user"]["id"]]["role"] == "manager"
            assert members[employee_a["user_id"]]["role"] == "reader"
            assert members[employee_b["user_id"]]["role"] == "contributor"
            assert deleted["user_id"] not in members

            repeated = client.post(
                f"/knowledge-spaces/{space['id']}/members:all",
                headers=admin_headers,
                json={"role": "manager"},
            )
            assert repeated.status_code == 200
            assert repeated.json()["added_count"] == 0
            assert repeated.json()["existing_count"] == 3
            repeated_members = {
                item["user_id"]: item for item in repeated.json()["items"]
            }
            assert repeated_members[employee_a["user_id"]]["role"] == "reader"
            assert repeated_members[employee_b["user_id"]]["role"] == "contributor"
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(tmp_path / "bulk-members.sqlite3")


def test_remove_all_space_members_except_owner(tmp_path: Path):
    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = tmp_path / "bulk-remove-members.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = client.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": settings.bootstrap_password,
                },
            ).json()
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
            space = client.post(
                "/knowledge-spaces",
                headers=admin_headers,
                json={"name": "清理空间", "description": ""},
            ).json()
            employee = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "remove-me",
                    "email": "remove-me@example.com",
                    "display_name": "待移除员工",
                },
            ).json()
            assert (
                client.post(
                    f"/knowledge-spaces/{space['id']}/members:all",
                    headers=admin_headers,
                    json={"role": "contributor"},
                ).status_code
                == 200
            )

            removed = client.delete(
                f"/knowledge-spaces/{space['id']}/members:all",
                headers=admin_headers,
            )
            assert removed.status_code == 200
            payload = removed.json()
            assert payload["removed_count"] == 1
            assert len(payload["items"]) == 1
            assert payload["items"][0]["user_id"] == admin["user"]["id"]

            repeated = client.delete(
                f"/knowledge-spaces/{space['id']}/members:all",
                headers=admin_headers,
            )
            assert repeated.status_code == 200
            assert repeated.json()["removed_count"] == 0
            assert len(repeated.json()["items"]) == 1
            assert employee["user_id"] not in {
                item["user_id"] for item in repeated.json()["items"]
            }
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(tmp_path / "bulk-remove-members.sqlite3")


def test_space_directory_users_are_searchable_for_managers(tmp_path: Path):
    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = tmp_path / "directory-users.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = client.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": settings.bootstrap_password,
                },
            ).json()
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
            space = client.post(
                "/knowledge-spaces",
                headers=admin_headers,
                json={"name": "目录空间", "description": ""},
            ).json()
            employee = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "lisi",
                    "email": "lisi@example.com",
                    "display_name": "李四",
                },
            ).json()
            reader = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "reader-dir",
                    "email": "reader-dir@example.com",
                    "display_name": "只读同事",
                },
            ).json()
            client.post(
                f"/auth/invitations/{reader['invite_token']}/accept",
                json={
                    "password": "Reader-password-123",
                    "password_confirmation": "Reader-password-123",
                },
            )
            reader_login = client.post(
                "/auth/login",
                json={"username": "reader-dir", "password": "Reader-password-123"},
            ).json()
            reader_headers = {"Authorization": f"Bearer {reader_login['access_token']}"}
            assert (
                client.post(
                    f"/knowledge-spaces/{space['id']}/members",
                    headers=admin_headers,
                    json={"user_id": reader_login["user"]["id"], "role": "reader"},
                ).status_code
                == 201
            )

            listed = client.get(
                f"/knowledge-spaces/{space['id']}/directory-users",
                headers=admin_headers,
            )
            assert listed.status_code == 200
            listed_payload = listed.json()
            assert listed_payload["total_active_users"] == 3
            by_id = {item["id"]: item for item in listed_payload["items"]}
            assert by_id[admin["user"]["id"]]["already_member"] is True
            assert by_id[reader_login["user"]["id"]]["already_member"] is True
            assert by_id[employee["user_id"]]["already_member"] is False
            assert by_id[employee["user_id"]]["display_name"] == "李四"

            searched = client.get(
                f"/knowledge-spaces/{space['id']}/directory-users",
                headers=admin_headers,
                params={"q": "李四"},
            )
            assert searched.status_code == 200
            assert searched.json()["total_active_users"] == 3
            assert [item["username"] for item in searched.json()["items"]] == ["lisi"]

            limited = client.get(
                f"/knowledge-spaces/{space['id']}/directory-users",
                headers=admin_headers,
                params={"q": "李四", "limit": 1},
            )
            assert limited.status_code == 200
            assert len(limited.json()["items"]) == 1
            assert limited.json()["total_active_users"] == 3

            assert (
                client.get(
                    f"/knowledge-spaces/{space['id']}/directory-users",
                    headers=reader_headers,
                ).status_code
                == 404
            )
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(tmp_path / "directory-users.sqlite3")


def test_space_update_and_soft_delete(tmp_path: Path):
    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = tmp_path / "space-crud.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = client.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": settings.bootstrap_password,
                },
            ).json()
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
            space = client.post(
                "/knowledge-spaces",
                headers=admin_headers,
                json={"name": "可编辑空间", "description": "原始说明"},
            ).json()
            assert space["owner_display_name"]
            assert space["owner_user_id"] == admin["user"]["id"]
            updated = client.patch(
                f"/knowledge-spaces/{space['id']}",
                headers=admin_headers,
                json={"name": "已改名空间", "description": "新说明"},
            )
            assert updated.status_code == 200
            assert updated.json()["name"] == "已改名空间"
            assert updated.json()["description"] == "新说明"

            invited = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "space-mgr",
                    "email": "space-mgr@example.com",
                    "display_name": "空间经理",
                },
            ).json()
            client.post(
                f"/auth/invitations/{invited['invite_token']}/accept",
                json={
                    "password": "Manager-password-123",
                    "password_confirmation": "Manager-password-123",
                },
            )
            manager = client.post(
                "/auth/login",
                json={"username": "space-mgr", "password": "Manager-password-123"},
            ).json()
            manager_headers = {"Authorization": f"Bearer {manager['access_token']}"}
            assert (
                client.post(
                    f"/knowledge-spaces/{space['id']}/members",
                    headers=admin_headers,
                    json={"user_id": manager["user"]["id"], "role": "manager"},
                ).status_code
                == 201
            )
            assert (
                client.patch(
                    f"/knowledge-spaces/{space['id']}",
                    headers=manager_headers,
                    json={"description": "管理者可改说明"},
                ).status_code
                == 200
            )
            assert (
                client.delete(
                    f"/knowledge-spaces/{space['id']}", headers=manager_headers
                ).status_code
                == 403
            )

            archived = client.post(
                f"/knowledge-spaces/{space['id']}:archive", headers=manager_headers
            )
            assert archived.status_code == 200
            assert archived.json()["status"] == "archived"
            listed_after_archive = client.get(
                "/knowledge-spaces", headers=admin_headers
            ).json()
            assert any(
                item["id"] == space["id"] and item["status"] == "archived"
                for item in listed_after_archive
            )
            restored = client.post(
                f"/knowledge-spaces/{space['id']}:restore", headers=manager_headers
            )
            assert restored.status_code == 200
            assert restored.json()["status"] == "active"
            restored_again = client.post(
                f"/knowledge-spaces/{space['id']}:restore", headers=manager_headers
            )
            assert restored_again.status_code == 200
            assert restored_again.json()["status"] == "active"
            assert (
                client.patch(
                    f"/knowledge-spaces/{space['id']}",
                    headers=manager_headers,
                    json={"description": "恢复后可改"},
                ).status_code
                == 200
            )
            assert (
                client.post(
                    f"/knowledge-spaces/{space['id']}:archive", headers=manager_headers
                ).status_code
                == 200
            )

            deleted = client.delete(
                f"/knowledge-spaces/{space['id']}", headers=admin_headers
            )
            assert deleted.status_code == 204
            assert (
                client.get(
                    f"/knowledge-spaces/{space['id']}", headers=admin_headers
                ).status_code
                == 404
            )
            listed = client.get("/knowledge-spaces", headers=admin_headers).json()
            assert all(item["id"] != space["id"] for item in listed)
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(tmp_path / "space-crud.sqlite3")


def _login_invited_user(
    client: TestClient,
    admin_headers: dict[str, str],
    *,
    username: str,
    email: str,
    display_name: str,
    password: str,
) -> dict:
    invited = client.post(
        "/admin/users",
        headers=admin_headers,
        json={
            "username": username,
            "email": email,
            "display_name": display_name,
        },
    ).json()
    client.post(
        f"/auth/invitations/{invited['invite_token']}/accept",
        json={"password": password, "password_confirmation": password},
    )
    login = client.post(
        "/auth/login", json={"username": username, "password": password}
    ).json()
    return {
        "user_id": invited["user_id"],
        "headers": {"Authorization": f"Bearer {login['access_token']}"},
        "user": login["user"],
    }


def test_user_created_space_is_private_and_owner_admin_can_manage_members(
    tmp_path: Path,
):
    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = tmp_path / "private-user-space.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = client.post(
                "/auth/login",
                json={
                    "username": settings.bootstrap_username,
                    "password": settings.bootstrap_password,
                },
            ).json()
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
            role = client.post(
                "/admin/roles",
                headers=admin_headers,
                json={
                    "code": "space_creator",
                    "name": "空间创建者",
                    "permission_codes": ["space:create"],
                },
            ).json()
            owner = _login_invited_user(
                client,
                admin_headers,
                username="space-owner",
                email="space-owner@example.com",
                display_name="空间创建人",
                password="Owner-password-123",
            )
            outsider = _login_invited_user(
                client,
                admin_headers,
                username="space-outsider",
                email="space-outsider@example.com",
                display_name="无关同事",
                password="Outsider-password-123",
            )
            member = _login_invited_user(
                client,
                admin_headers,
                username="space-member",
                email="space-member@example.com",
                display_name="已授权同事",
                password="Member-password-123",
            )
            assigned = client.put(
                f"/admin/users/{owner['user_id']}/roles",
                headers=admin_headers,
                json={"role_ids": [role["id"]]},
            )
            assert assigned.status_code == 200

            created = client.post(
                "/knowledge-spaces",
                headers=owner["headers"],
                json={"name": "我的私有资料", "description": "仅授权可见"},
            )
            assert created.status_code == 201, created.text
            space = created.json()
            assert space["owner_user_id"] == owner["user_id"]
            assert space["member_role"] == "manager"

            owner_listed = client.get(
                "/knowledge-spaces", headers=owner["headers"]
            ).json()
            assert [item["id"] for item in owner_listed] == [space["id"]]

            outsider_listed = client.get(
                "/knowledge-spaces", headers=outsider["headers"]
            ).json()
            assert all(item["id"] != space["id"] for item in outsider_listed)
            assert (
                client.get(
                    f"/knowledge-spaces/{space['id']}", headers=outsider["headers"]
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/knowledge-spaces/{space['id']}/members",
                    headers=outsider["headers"],
                    json={"user_id": member["user_id"], "role": "contributor"},
                ).status_code
                == 404
            )

            admin_listed = client.get("/knowledge-spaces", headers=admin_headers).json()
            assert space["id"] in [item["id"] for item in admin_listed]
            assert (
                client.get(
                    f"/knowledge-spaces/{space['id']}", headers=admin_headers
                ).status_code
                == 200
            )

            added_by_owner = client.post(
                f"/knowledge-spaces/{space['id']}/members",
                headers=owner["headers"],
                json={"user_id": member["user_id"], "role": "contributor"},
            )
            assert added_by_owner.status_code == 201, added_by_owner.text
            member_listed = client.get(
                "/knowledge-spaces", headers=member["headers"]
            ).json()
            assert [item["id"] for item in member_listed] == [space["id"]]
            assert (
                client.get(
                    f"/knowledge-spaces/{space['id']}", headers=member["headers"]
                ).status_code
                == 200
            )

            added_by_admin = client.post(
                f"/knowledge-spaces/{space['id']}/members",
                headers=admin_headers,
                json={"user_id": outsider["user_id"], "role": "reader"},
            )
            assert added_by_admin.status_code == 201, added_by_admin.text
            outsider_after = client.get(
                "/knowledge-spaces", headers=outsider["headers"]
            ).json()
            assert space["id"] in [item["id"] for item in outsider_after]
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(tmp_path / "private-user-space.sqlite3")
