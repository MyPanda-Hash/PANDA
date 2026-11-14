from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import database
from app.core.config import settings
from app.main import app
from tests._auth import bootstrap_admin_response

PASSWORD = "Admin-watch-tests-123"


@pytest.fixture
def client(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "assistant-watch-api.sqlite3"
    database.reset_database(path)
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        database.reset_database(path)
        settings.db_path, settings.database_url = original


def _admin(client: TestClient) -> dict[str, str]:
    payload = bootstrap_admin_response(client, password=PASSWORD).json()
    return {"Authorization": f"Bearer {payload['access_token']}"}


def test_scan_endpoint_requires_admin(client: TestClient):
    admin = _admin(client)
    created = client.post(
        "/admin/users",
        headers=admin,
        json={"username": "planner", "display_name": "李计划", "password": "Planner-pass-123"},
    )
    assert created.status_code == 201, created.text
    login = client.post("/auth/login", json={"username": "planner", "password": "Planner-pass-123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    denied = client.post("/assistant/scan", headers=headers)
    assert denied.status_code == 403


def test_status_reports_configuration(client: TestClient):
    admin = _admin(client)
    status = client.get("/assistant/status", headers=admin).json()
    assert status["feed_group"] == settings.mes_watch_group_name
    assert status["interval_seconds"] == settings.mes_watch_interval_seconds
    assert status["open_for_me"] == 0


def test_ack_requires_target_or_admin(client: TestClient):
    admin = _admin(client)
    created = client.post(
        "/admin/users",
        headers=admin,
        json={"username": "worker", "display_name": "赵车间", "password": "Worker-pass-123"},
    )
    assert created.status_code == 201
    login = client.post("/auth/login", json={"username": "worker", "password": "Worker-pass-123"})
    worker = {"Authorization": f"Bearer {login.json()['access_token']}"}

    from app.core.database import transaction
    from app.models.assistant_watch import AssistantEvent
    from app.core.security import now

    with transaction() as db:
        stamp = now()
        db.add(
            AssistantEvent(
                id="aev_test",
                dedupe_key="so_unstarted:SO-1",
                kind="so_unstarted",
                title="销售订单 SO-1 已审核，尚未安排生产",
                detail="",
                suggested_action="建生产工单",
                severity="warning",
                status="open",
                target_usernames_json='["worker"]',
                target_user_ids_json='["usr_not_worker"]',
                payload_json="{}",
                created_at=stamp,
                updated_at=stamp,
            )
        )

    blocked = client.post("/assistant/events/aev_test:ack", headers=worker)
    assert blocked.status_code == 403

    ok = client.post("/assistant/events/aev_test:ack", headers=admin)
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "handled"


def test_event_link_points_to_mes_form_route(client: TestClient):
    """链接必须是 MES 真实路由（hash 模式 + /panelx/form/<panel>?code=）。"""
    admin = _admin(client)
    from app.core.database import transaction
    from app.core.security import now
    from app.models.assistant_watch import AssistantEvent

    with transaction() as db:
        stamp = now()
        db.add(
            AssistantEvent(
                id="aev_link",
                dedupe_key="so_unstarted:SO-LINK",
                kind="so_unstarted",
                panel_code="SO_ORDER",
                doc_no="SO-2026-11-001",
                title="销售订单 SO-2026-11-001 已审核，尚未安排生产",
                severity="warning",
                status="open",
                target_usernames_json='["admin"]',
                target_user_ids_json='[]',
                payload_json="{}",
                created_at=stamp,
                updated_at=stamp,
                first_seen_at=stamp,
            )
        )

    events = client.get("/assistant/events", headers=admin).json()
    target = next(e for e in events if e["id"] == "aev_link")
    assert target["monday_url"].endswith("/#/panelx/form/SO_ORDER?code=SO-2026-11-001")
    assert target["age_hours"] >= 0


def _seed_event(db, **overrides):
    from app.core.security import now
    from app.models.assistant_watch import AssistantEvent

    stamp = now()
    values = dict(
        id="aev_x",
        dedupe_key="so_unstarted:SO-X",
        kind="so_unstarted",
        panel_code="SO_ORDER",
        doc_no="SO-X",
        title="销售订单 SO-X 已审核，尚未安排生产",
        severity="warning",
        status="open",
        target_usernames_json='["admin"]',
        target_user_ids_json='[]',
        payload_json="{}",
        created_at=stamp,
        updated_at=stamp,
        first_seen_at=stamp,
    )
    values.update(overrides)
    db.add(AssistantEvent(**values))
    return values["id"]


def test_ack_accepts_note_and_reports_acked_by(client: TestClient):
    admin = _admin(client)
    from app.core.database import transaction

    with transaction() as db:
        _seed_event(db)

    acked = client.post(
        "/assistant/events/aev_x:ack", headers=admin, json={"note": "已在路上"}
    )
    assert acked.status_code == 200, acked.text
    body = acked.json()
    assert body["status"] == "handled"
    assert body["note"] == "已在路上"
    assert body["acked_by"] == "系统管理员"


def test_reopen_puts_event_back_to_open(client: TestClient):
    admin = _admin(client)
    from app.core.database import transaction

    with transaction() as db:
        _seed_event(db)
    client.post("/assistant/events/aev_x:ack", headers=admin, json={})

    reopened = client.post("/assistant/events/aev_x:reopen", headers=admin)
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "open"
    assert reopened.json()["acked_by"] is None


def test_digest_endpoint_returns_counts(client: TestClient):
    admin = _admin(client)
    from app.core.database import transaction

    with transaction() as db:
        _seed_event(db)

    digest = client.get("/assistant/digest", headers=admin).json()
    assert digest["created"] == 1
    assert digest["open"] == 1
    assert digest["by_kind"]["so_unstarted"] == 1


def test_archive_endpoint_requires_admin(client: TestClient):
    admin = _admin(client)
    created = client.post(
        "/admin/users",
        headers=admin,
        json={"username": "arch", "display_name": "归档员", "password": "Arch-pass-123"},
    )
    assert created.status_code == 201
    login = client.post("/auth/login", json={"username": "arch", "password": "Arch-pass-123"})
    user_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert client.post("/assistant/archive", headers=user_headers).status_code == 403
    ok = client.post("/assistant/archive?days=30", headers=admin)
    assert ok.status_code == 200
    assert "archived" in ok.json()


def test_list_events_accepts_acked_filter(client: TestClient):
    admin = _admin(client)
    from app.core.database import transaction

    with transaction() as db:
        _seed_event(db, id="aev_a", dedupe_key="k-a")
        _seed_event(db, id="aev_b", dedupe_key="k-b")

    client.post("/assistant/events/aev_a:ack", headers=admin, json={"note": "处理中"})

    unacked = client.get(
        "/assistant/events?status=all&acked=none", headers=admin
    ).json()
    assert [e["id"] for e in unacked] == ["aev_b"]

    mine = client.get("/assistant/events?status=all&acked=me", headers=admin).json()
    assert [e["id"] for e in mine] == ["aev_a"]

    others = client.get("/assistant/events?status=all&acked=others", headers=admin).json()
    assert others == []


def test_list_events_rejects_unknown_acked_value(client: TestClient):
    admin = _admin(client)
    resp = client.get("/assistant/events?acked=bogus", headers=admin)
    assert resp.status_code == 422


def test_facets_endpoint_reports_counts(client: TestClient):
    admin = _admin(client)
    from app.core.database import transaction

    with transaction() as db:
        _seed_event(db, id="aev_c", dedupe_key="k-c")

    facets = client.get("/assistant/facets", headers=admin).json()
    assert facets["total"] == 1
    assert facets["unacked"] == 1
    assert facets["acked_by_me"] == 0
