from pathlib import Path

from fastapi.testclient import TestClient

from app.core import database
from app.core.config import settings
from app.main import app
from tests._auth import bootstrap_admin_response


def test_governance_user_lifecycle_price_budget_and_masked_audit(tmp_path: Path):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "governance.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            headers = {"Authorization": f"Bearer {admin['access_token']}"}

            invite = client.post("/admin/users", headers=headers, json={"username": "governed", "email": "governed@example.com", "display_name": "受治理用户"})
            assert invite.status_code == 201
            assert invite.json()["api_key"].startswith("dnx_")
            user_id = invite.json()["user_id"]
            users = client.get("/admin/users", headers=headers)
            assert users.status_code == 200
            assert "password_hash" not in users.text and "invite_token" not in users.text
            assert users.json()["items"][1]["api_key"] == invite.json()["api_key"]
            first_users = client.get("/admin/users?limit=1&offset=0", headers=headers)
            assert first_users.status_code == 200
            assert first_users.json()["next_offset"] == 1
            remaining_users = client.get("/admin/users?limit=1&offset=1", headers=headers)
            assert remaining_users.status_code == 200
            assert remaining_users.json()["next_offset"] is None
            assert [item["id"] for item in first_users.json()["items"] + remaining_users.json()["items"]] == [
                item["id"] for item in users.json()["items"]
            ]
            past_users = client.get("/admin/users?limit=1&offset=2", headers=headers)
            assert past_users.status_code == 200
            assert past_users.json()["items"] == []
            assert past_users.json()["next_offset"] is None

            disabled = client.post(f"/admin/users/{user_id}:disable", headers=headers)
            assert disabled.status_code == 200
            assert disabled.json()["audit_event_id"].startswith("aud_")
            revoked = client.post(f"/admin/users/{user_id}:revoke-sessions", headers=headers)
            assert revoked.status_code == 200
            assert revoked.json()["audit_event_id"].startswith("aud_")

            builtin_role = next(role for role in client.get("/admin/roles", headers=headers).json() if role["builtin"])
            assert client.delete(f"/admin/roles/{builtin_role['id']}", headers=headers).status_code == 409

            first_price = client.post("/admin/model-prices", headers=headers, json={"provider": "test", "model": "gpt-test", "currency": "CNY", "input_per_million": "1.20", "output_per_million": "2.40"})
            assert first_price.status_code == 201
            second_price = client.post("/admin/model-prices", headers=headers, json={"provider": "test", "model": "gpt-test", "currency": "CNY", "input_per_million": "1.50", "output_per_million": "2.90"})
            assert second_price.status_code == 201
            prices = client.get("/admin/model-prices", headers=headers).json()["items"]
            assert len(prices) == 2
            assert prices[0]["superseded_at"] is not None and prices[1]["superseded_at"] is None

            preview = client.post("/admin/budgets:preview", headers=headers, json={"scope": "system", "cost_limit": "100.00", "soft_threshold": 0.8, "hard_stop": True})
            assert preview.status_code == 200
            assert preview.json()["affected_resource_count"] == 1
            saved = client.post("/admin/budgets", headers=headers, json={"scope": "system", "cost_limit": "100.00", "soft_threshold": 0.8, "hard_stop": True})
            assert saved.status_code == 201
            assert saved.json()["enforcement"] == "hard_stop"
            assert saved.json()["audit_event_id"].startswith("aud_")

            audits = client.get("/admin/audit-events", headers=headers)
            assert audits.status_code == 200
            assert any(item["event_type"] == "user_disabled" for item in audits.json()["items"])
            assert "password" not in audits.text.lower() and "token" not in audits.text.lower()
            assert audits.json()["total"] >= len(audits.json()["items"])
            assert audits.json()["offset"] == 0
            assert audits.json()["limit"] == 20

            paged = client.get("/admin/audit-events?limit=1&offset=0", headers=headers)
            assert paged.status_code == 200
            assert len(paged.json()["items"]) == 1
            assert paged.json()["total"] >= 1
            assert paged.json()["next_offset"] in (1, None) or paged.json()["total"] > 1
            if paged.json()["total"] > 1:
                assert paged.json()["next_offset"] == 1
                page2 = client.get("/admin/audit-events?limit=1&offset=1", headers=headers)
                assert page2.status_code == 200
                assert page2.json()["items"][0]["id"] != paged.json()["items"][0]["id"]

            failed_only = client.get("/admin/audit-events?result=other", headers=headers)
            assert failed_only.status_code == 200
            assert all(item["result"] != "success" for item in failed_only.json()["items"])

            # recreate a user for reset/delete coverage
            invited2 = client.post(
                "/admin/users",
                headers=headers,
                json={"username": "to-reset", "email": "to-reset@example.com", "display_name": "待重置", "password": "Initial-password-123"},
            )
            assert invited2.status_code == 201
            assert invited2.json()["api_key"].startswith("dnx_")
            initial_api_key = invited2.json()["api_key"]
            assert invited2.json()["mode"] == "password"
            reset_id = invited2.json()["user_id"]
            reset = client.post(f"/admin/users/{reset_id}:reset-password", headers=headers, json={})
            assert reset.status_code == 200
            temp_password = reset.json()["temporary_password"]
            assert len(temp_password) >= 8
            assert "password_hash" not in reset.text
            login_ok = client.post("/auth/login", json={"username": "to-reset", "password": temp_password})
            assert login_ok.status_code == 200

            deleted = client.delete(f"/admin/users/{reset_id}", headers=headers)
            assert deleted.status_code == 200
            assert deleted.json()["user"]["status"] == "deleted"
            assert deleted.json()["purged"] is False
            assert client.post("/auth/login", json={"username": "to-reset", "password": temp_password}).status_code == 403

            # Soft-deleted username/email can be re-registered (restores the same user id).
            restored = client.post(
                "/admin/users",
                headers=headers,
                json={
                    "username": "to-reset",
                    "email": "to-reset@example.com",
                    "display_name": "已恢复",
                    "password": "Restored-password-123",
                },
            )
            assert restored.status_code == 201
            assert restored.json()["api_key"].startswith("dnx_")
            assert restored.json()["api_key"] != initial_api_key
            assert restored.json()["user_id"] == reset_id
            assert restored.json()["user"]["status"] == "active"
            assert restored.json()["user"]["display_name"] == "已恢复"
            assert client.post("/auth/login", json={"username": "to-reset", "password": "Restored-password-123"}).status_code == 200
            assert client.post("/admin/users", headers=headers, json={"username": "to-reset", "email": "other@example.com", "display_name": "冲突"}).status_code == 409

            # Soft-delete again, then purge permanently so the username can be freshly created.
            assert client.delete(f"/admin/users/{reset_id}", headers=headers).status_code == 200
            purged = client.delete(f"/admin/users/{reset_id}", headers=headers)
            assert purged.status_code == 200
            assert purged.json()["purged"] is True
            assert purged.json()["user"] is None
            assert client.delete(f"/admin/users/{reset_id}", headers=headers).status_code == 404
            recreated = client.post(
                "/admin/users",
                headers=headers,
                json={
                    "username": "to-reset",
                    "email": "to-reset@example.com",
                    "display_name": "全新账号",
                    "password": "Fresh-password-123",
                },
            )
            assert recreated.status_code == 201
            assert recreated.json()["user_id"] != reset_id
            assert recreated.json()["user"]["display_name"] == "全新账号"

            assert client.delete(f"/admin/users/{admin['user']['id']}", headers=headers).status_code == 409
            assert client.post(f"/admin/users/{admin['user']['id']}:reset-password", headers=headers, json={}).status_code == 409
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "governance.sqlite3")


def test_governance_routes_remain_admin_only(tmp_path: Path):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "governance-permissions.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            invite = client.post("/admin/users", headers=headers, json={"username": "reader", "email": "reader@example.com", "display_name": "普通用户"}).json()
            client.post(f"/auth/invitations/{invite['invite_token']}/accept", json={"password": "Reader-password-123", "password_confirmation": "Reader-password-123"})
            reader = client.post("/auth/login", json={"username": "reader", "password": "Reader-password-123"}).json()
            reader_headers = {"Authorization": f"Bearer {reader['access_token']}"}
            assert client.get("/admin/users", headers=reader_headers).status_code == 403
            assert client.get("/admin/audit-events", headers=reader_headers).status_code == 403
            budget = client.post("/admin/budgets", headers=headers, json={
                "scope": "system", "cost_limit": "1", "soft_threshold": 0.8, "hard_stop": True,
            }).json()
            assert client.get("/admin/budgets", headers=reader_headers).status_code == 403
            assert client.patch(f"/admin/budgets/{budget['id']}", headers=reader_headers, json={
                "cost_limit": "2", "soft_threshold": 0.8, "hard_stop": True,
            }).status_code == 403
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "governance-permissions.sqlite3")


def test_create_user_without_email_invite_and_login(tmp_path: Path):
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path = tmp_path / "governance-optional-email.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/admin/users",
                headers=headers,
                json={"username": "noemail", "display_name": "无邮箱用户"},
            )
            assert created.status_code == 201
            body = created.json()
            assert body["mode"] == "invite"
            assert body["user"]["email"] is None
            assert body["invite_token"]

            inspected = client.get(f"/auth/invitations/{body['invite_token']}")
            assert inspected.status_code == 200
            assert inspected.json()["email"] is None
            assert inspected.json()["username"] == "noemail"

            accepted = client.post(
                f"/auth/invitations/{body['invite_token']}/accept",
                json={
                    "password": "No-email-pass-123",
                    "password_confirmation": "No-email-pass-123",
                },
            )
            assert accepted.status_code == 200
            assert accepted.json()["email"] is None

            login = client.post(
                "/auth/login",
                json={"username": "noemail", "password": "No-email-pass-123"},
            )
            assert login.status_code == 200
            assert login.json()["user"]["email"] is None

            blank_email = client.post(
                "/admin/users",
                headers=headers,
                json={
                    "username": "blankmail",
                    "email": "  ",
                    "display_name": "空邮箱",
                    "password": "Blank-email-123",
                },
            )
            assert blank_email.status_code == 201
            assert blank_email.json()["user"]["email"] is None
            assert blank_email.json()["mode"] == "password"
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(tmp_path / "governance-optional-email.sqlite3")


def test_budget_rule_toggle_delete_and_duplicate_scope_guard(tmp_path: Path):
    from decimal import Decimal

    import pytest

    from app.services.budget import VisionBudgetExceeded, reserve_vision_budget

    original_path, original_url = settings.db_path, settings.database_url
    path = tmp_path / "budget-toggle.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post("/admin/budgets", headers=headers, json={
                "scope": "system", "cost_limit": "0.5", "soft_threshold": 0.8, "hard_stop": True,
            })
            assert created.status_code == 201
            assert created.json()["is_active"] is True
            rule_id = created.json()["id"]

            duplicate = client.post("/admin/budgets", headers=headers, json={
                "scope": "system", "cost_limit": "1", "soft_threshold": 0.8, "hard_stop": True,
            })
            assert duplicate.status_code == 409
            assert "已有预算规则" in duplicate.json()["detail"]

            def reserve(operation: str):
                return reserve_vision_budget(
                    user_id=admin["user"]["id"], operation_id=operation,
                    provider="test", model="test", estimated_cost=Decimal("0.75"),
                )

            with pytest.raises(VisionBudgetExceeded):
                reserve("before-disable")

            disabled = client.post(f"/admin/budgets/{rule_id}:disable", headers=headers)
            assert disabled.status_code == 200
            assert disabled.json()["is_active"] is False
            assert disabled.json()["audit_event_id"].startswith("aud_")
            assert reserve("after-disable") is None
            listed = client.get("/admin/budgets", headers=headers).json()["items"]
            system = next(item for item in listed if item["id"] == rule_id)
            assert system["is_active"] is False
            assert system["usage"]["at_or_above_limit"] is False

            enabled = client.post(f"/admin/budgets/{rule_id}:enable", headers=headers)
            assert enabled.status_code == 200
            assert enabled.json()["is_active"] is True
            with pytest.raises(VisionBudgetExceeded):
                reserve("after-enable")

            deleted = client.delete(f"/admin/budgets/{rule_id}", headers=headers)
            assert deleted.status_code == 200
            assert deleted.json()["audit_event_id"].startswith("aud_")
            assert client.get("/admin/budgets", headers=headers).json()["items"] == []
            assert reserve("after-delete") is None

            assert client.post("/admin/budgets/missing:disable", headers=headers).status_code == 404
            assert client.post("/admin/budgets/missing:enable", headers=headers).status_code == 404
            assert client.delete("/admin/budgets/missing", headers=headers).status_code == 404

            for event_type, label in (
                ("budget_rule_disabled", "停用预算规则"),
                ("budget_rule_enabled", "启用预算规则"),
                ("budget_rule_deleted", "删除预算规则"),
            ):
                events = client.get(
                    f"/admin/audit-events?event_type={event_type}", headers=headers
                ).json()["items"]
                assert {item["target_id"] for item in events} == {rule_id}
                assert all(item["event_label"] == label for item in events)
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(path)


def test_budget_update_preserves_other_hard_rules_and_existing_usage(tmp_path: Path):
    from decimal import Decimal

    import pytest

    from app.core.database import transaction
    from app.models.governance import BudgetReservation, BudgetRule
    from app.services.budget import (
        VisionBudgetExceeded,
        release_vision_budget,
        reserve_vision_budget,
    )

    original_path, original_url = settings.db_path, settings.database_url
    path = tmp_path / "budget-edit.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin_response(client).json()
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            rules = []
            for scope in ("system", "user"):
                response = client.post("/admin/budgets", headers=headers, json={
                    "scope": scope, "cost_limit": "0.5", "soft_threshold": 0.8, "hard_stop": True,
                })
                assert response.status_code == 201
                rules.append(response.json()["id"])

            def reserve(operation: str):
                return reserve_vision_budget(
                    user_id=admin["user"]["id"], operation_id=operation,
                    provider="test", model="test", estimated_cost=Decimal("0.75"),
                )

            with pytest.raises(VisionBudgetExceeded):
                reserve("before-update")
            unchanged = client.get("/admin/budgets", headers=headers)
            assert unchanged.status_code == 200
            assert {item["id"] for item in unchanged.json()["items"]} == set(rules)
            assert all(item["usage"]["current_cost"] == "0" for item in unchanged.json()["items"] if item["scope"] == "system")
            assert all("usage" not in item for item in unchanged.json()["items"] if item["scope"] == "user")

            update = {"cost_limit": "2", "soft_threshold": 0.5, "hard_stop": True}
            first = client.patch(f"/admin/budgets/{rules[0]}", headers=headers, json=update)
            assert first.status_code == 200
            assert first.json()["scope"] == "system"
            assert first.json()["audit_event_id"].startswith("aud_")
            with pytest.raises(VisionBudgetExceeded):
                reserve("second-rule-still-blocks")
            second = client.patch(f"/admin/budgets/{rules[1]}", headers=headers, json=update)
            assert second.status_code == 200
            handle = reserve("after-both-updates")
            assert handle is not None
            listed = client.get("/admin/budgets", headers=headers).json()["items"]
            system = next(item for item in listed if item["id"] == rules[0])
            assert system["usage"]["held_cost"] == "0.75"
            assert system["usage"]["current_cost"] == "0"
            with transaction() as db:
                assert len(list(db.query(BudgetRule))) == 2
                assert len(list(db.query(BudgetReservation))) == 1
            release_vision_budget(handle.id)
            audits = client.get("/admin/audit-events?event_type=budget_rule_updated", headers=headers).json()["items"]
            assert {event["target_id"] for event in audits} == set(rules)
            assert all(event["event_label"] == "调整预算规则" for event in audits)

            assert client.patch("/admin/budgets/missing", headers=headers, json=update).status_code == 404
            for invalid in ("NaN", "Infinity", "-1", "0"):
                assert client.patch(f"/admin/budgets/{rules[0]}", headers=headers, json={**update, "cost_limit": invalid}).status_code == 422
            assert client.patch(f"/admin/budgets/{rules[0]}", headers=headers, json={**update, "soft_threshold": "Infinity"}).status_code == 422
            assert client.patch(f"/admin/budgets/{rules[0]}", headers=headers, json={**update, "scope": "space"}).status_code == 422
            assert client.get("/admin/budgets", headers=headers).json()["items"][0]["cost_limit"] == "2"
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(path)
