"""Authorisation and idempotency for the personal memory API and capabilities.

The repository tests cover the state machine. These cover the layer above it:
who is allowed to ask, what an old grant can still do, and whether a retry can
save twice. They run against the real routes and the real grant validation
rather than mocked echoes, because owner isolation proved by a mock proves
nothing about the code that actually enforces it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import database
from app.core.config import settings
from app.core.database import transaction
from app.main import app
from app.repositories import user_memory as repo
from app.services.agent_capability import (
    CapabilityGrantError,
    create_capability_token,
    validate_claims,
)
from app.services.pi_runtime.config import PI_RUNTIME_AGENT_ID
from tests._auth import bootstrap_admin_response


@pytest.fixture
def memory_api(tmp_path: Path, monkeypatch):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "memory-api.sqlite3"
    database.reset_database(path)
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(
        settings, "agent_capability_secret", "capability-secret-for-tests-32bytes"
    )
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "personal_memory_enabled", True)
    from app.services.user_memory_dispatcher import UserMemoryDispatcher

    # API tests isolate the consumer from real provider work; worker integration
    # is exercised separately against a controlled HTTP upstream.
    monkeypatch.setattr(UserMemoryDispatcher, "start", lambda self: None)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        database.reset_database(path)
        settings.db_path, settings.database_url = old_path, old_url


def _admin(client: TestClient) -> tuple[dict[str, str], str]:
    body = bootstrap_admin_response(client).json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


def _member(
    client: TestClient, headers: dict[str, str], username: str
) -> tuple[dict[str, str], str]:
    password = "Member-backend-tests-123"
    created = client.post(
        "/admin/users",
        headers=headers,
        json={
            "username": username,
            "email": f"{username}@example.com",
            # Both members share a display name on purpose: nothing may fall
            # back to matching employees by what they are called.
            "display_name": "同名同事",
            "password": password,
        },
    )
    assert created.status_code == 201, created.text
    login = client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert login.status_code == 200, login.text
    body = login.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


def _save(
    client: TestClient,
    headers: dict[str, str],
    *,
    key: str,
    value: str,
    request_id: str,
):
    return client.post(
        "/me/memories",
        headers=headers,
        json={"key": key, "value": value, "request_id": request_id},
    )


# ---------------------------------------------------------------------------
# API authorisation (A03).
# ---------------------------------------------------------------------------


def test_one_employee_cannot_reach_another_employees_memory(
    memory_api: TestClient,
) -> None:
    admin_headers, _ = _admin(memory_api)
    mei_headers, _ = _member(memory_api, admin_headers, "mei")
    fang_headers, _ = _member(memory_api, admin_headers, "fang")

    saved = _save(
        memory_api,
        mei_headers,
        key="profile.self_reported_name",
        value="李梅",
        request_id="mei-1",
    )
    assert saved.status_code == 200, saved.text
    memory_id = saved.json()["entry"]["id"]

    # Fang's own list is empty even though both employees share a display name.
    assert memory_api.get("/me/memories", headers=fang_headers).json()["items"] == []

    # Guessing Mei's id answers 404, not 403: a distinct status would confirm
    # that the row exists.
    for response in (
        memory_api.get(f"/me/memories/{memory_id}/sources", headers=fang_headers),
        memory_api.patch(
            f"/me/memories/{memory_id}",
            headers=fang_headers,
            json={"value": "王芳", "expected_version": 2, "request_id": "f-1"},
        ),
        memory_api.post(
            f"/me/memories/{memory_id}/forget",
            headers=fang_headers,
            json={"expected_version": 2, "request_id": "f-2"},
        ),
    ):
        assert response.status_code == 404, response.text

    # And Mei's value is untouched.
    items = memory_api.get("/me/memories", headers=mei_headers).json()["items"]
    assert [item["value"] for item in items] == ["李梅"]


def test_the_administrator_has_no_route_into_another_employees_memory(
    memory_api: TestClient,
) -> None:
    admin_headers, _ = _admin(memory_api)
    mei_headers, _ = _member(memory_api, admin_headers, "mei")
    saved = _save(
        memory_api,
        mei_headers,
        key="profile.preferred_name",
        value="小梅",
        request_id="mei-1",
    )
    assert saved.status_code == 200, saved.text

    # There is no owner parameter to abuse, and the admin's own memory is
    # empty: being an administrator grants nothing here.
    assert memory_api.get("/me/memories", headers=admin_headers).json()["items"] == []
    probe = memory_api.get(
        f"/me/memories/{saved.json()['entry']['id']}/sources", headers=admin_headers
    )
    assert probe.status_code == 404


# ---------------------------------------------------------------------------
# Idempotency (A07).
# ---------------------------------------------------------------------------


def test_a_retried_save_does_not_store_the_value_twice(memory_api: TestClient) -> None:
    admin_headers, _ = _admin(memory_api)
    mei_headers, _ = _member(memory_api, admin_headers, "mei")

    first = _save(
        memory_api,
        mei_headers,
        key="profile.self_reported_name",
        value="李梅",
        request_id="same-key",
    )
    assert first.status_code == 200, first.text
    again = _save(
        memory_api,
        mei_headers,
        key="profile.self_reported_name",
        value="李梅",
        request_id="same-key",
    )
    assert again.status_code == 200, again.text
    assert again.json()["entry"]["id"] == first.json()["entry"]["id"]
    assert again.json()["revision"] == first.json()["revision"]
    items = memory_api.get("/me/memories", headers=mei_headers).json()["items"]
    assert len(items) == 1

    # The same key carrying different content is a new request wearing an old
    # id; answering it from the old receipt would silently drop the change.
    conflict = _save(
        memory_api,
        mei_headers,
        key="profile.self_reported_name",
        value="王芳",
        request_id="same-key",
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["error_code"] == "memory_request_conflict"


def test_forgetting_reports_revoked_on_replay_and_keeps_the_chat(
    memory_api: TestClient,
) -> None:
    admin_headers, _ = _admin(memory_api)
    mei_headers, _ = _member(memory_api, admin_headers, "mei")
    saved = _save(
        memory_api,
        mei_headers,
        key="profile.preferred_name",
        value="小梅",
        request_id="save-1",
    )
    entry = saved.json()["entry"]

    forgotten = memory_api.post(
        f"/me/memories/{entry['id']}/forget",
        headers=mei_headers,
        json={"expected_version": entry["version"], "request_id": "forget-1"},
    )
    assert forgotten.status_code == 200, forgotten.text
    assert forgotten.json()["entry"]["value"] is None

    # Replaying the original save reports the revoked state instead of handing
    # the deleted value back or bringing it back.
    replay = _save(
        memory_api,
        mei_headers,
        key="profile.preferred_name",
        value="小梅",
        request_id="save-1",
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["receipt"]["revoked"] is True
    assert replay.json()["entry"] is None


def test_settings_use_compare_and_swap_on_the_revision(memory_api: TestClient) -> None:
    admin_headers, _ = _admin(memory_api)
    mei_headers, _ = _member(memory_api, admin_headers, "mei")
    current = memory_api.get("/me/memories/settings", headers=mei_headers).json()
    assert current["learning_mode"] == "manual"

    stale = memory_api.patch(
        "/me/memories/settings",
        headers=mei_headers,
        json={
            "learning_mode": "suggest",
            "expected_revision": current["revision"] + 5,
            "request_id": "settings-1",
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["error_code"] == "memory_revision_conflict"

    applied = memory_api.patch(
        "/me/memories/settings",
        headers=mei_headers,
        json={
            "learning_mode": "suggest",
            "expected_revision": current["revision"],
            "request_id": "settings-2",
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["learning_mode"] == "suggest"


# ---------------------------------------------------------------------------
# Capability scope (A03, A12).
# ---------------------------------------------------------------------------


def test_an_old_personal_history_grant_cannot_touch_memory(
    memory_api: TestClient,
) -> None:
    """A grant issued before memory existed carries no memory scope.

    Absence must mean none, or every existing read-only "own history" grant
    would silently gain the power to write the employee's profile.
    """

    admin_headers, _ = _admin(memory_api)
    _, mei_id = _member(memory_api, admin_headers, "mei")
    token = create_capability_token(
        user_id=mei_id,
        agent_id=PI_RUNTIME_AGENT_ID,
        personal_data_scope="own",
        allowed_tools=["search_my_chat_history"],
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
    }
    for tool in ("get_my_memory", "propose_my_memory", "propose_forget_my_memory"):
        response = memory_api.post(
            f"/internal/v1/agent-capabilities/{tool}",
            headers=headers,
            json={"arguments": {}},
        )
        assert response.status_code == 403, response.text


def test_a_read_grant_cannot_propose_and_needs_no_live_turn(
    memory_api: TestClient,
) -> None:
    admin_headers, _ = _admin(memory_api)
    mei_headers, mei_id = _member(memory_api, admin_headers, "mei")
    _save(
        memory_api,
        mei_headers,
        key="preference.language",
        value="zh-CN",
        request_id="lang-1",
    )
    with transaction() as db:
        from app.models.auth import User

        auth_version = db.get(User, mei_id).auth_version

    token = create_capability_token(
        user_id=mei_id,
        agent_id=PI_RUNTIME_AGENT_ID,
        personal_data_scope="own",
        memory_scope="read",
        auth_version=auth_version,
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
    }
    # Reading works with no registered turn at all, which is what lets an older
    # Open API request keep using memory it already has.
    read = memory_api.post(
        "/internal/v1/agent-capabilities/get_my_memory",
        headers=headers,
        json={"arguments": {}},
    )
    assert read.status_code == 200, read.text
    inline = read.json()["inline"]
    assert inline["active_count"] == 1
    # The tool result carries keys and counts, never the values: anything
    # returned here is written into the Pi transcript.
    assert inline["keys"] == ["preference.language"]
    assert "zh-CN" not in read.text

    # Writing is not in the grant at all.
    write = memory_api.post(
        "/internal/v1/agent-capabilities/propose_my_memory",
        headers=headers,
        json={"arguments": {}},
    )
    assert write.status_code == 403, write.text


def test_a_propose_grant_requires_a_registered_turn() -> None:
    with pytest.raises(CapabilityGrantError):
        create_capability_token(
            user_id="user-mei",
            agent_id=PI_RUNTIME_AGENT_ID,
            memory_scope="propose",
            auth_version=0,
        )


def test_memory_scope_is_part_of_the_signed_scope_hash(monkeypatch) -> None:
    """Rewriting the scope claim without re-signing must not widen the grant."""

    import jwt

    from app.services import agent_capability as capability

    monkeypatch.setattr(
        settings, "agent_capability_secret", "capability-secret-for-tests-32bytes"
    )
    monkeypatch.setattr(settings, "environment", "test")

    token = create_capability_token(
        user_id="user-mei",
        agent_id=PI_RUNTIME_AGENT_ID,
        memory_scope="read",
        auth_version=0,
    )
    claims = jwt.decode(
        token,
        capability._secret(),
        algorithms=["HS256"],
        audience=capability.CAPABILITY_AUDIENCE,
    )
    claims["memory_scope"] = "propose"
    claims["allowed_tools"] = list(capability.MEMORY_CAPABILITY_TOOLS)
    with pytest.raises(CapabilityGrantError):
        validate_claims(claims, tool_name="propose_my_memory")


def test_a_revoked_auth_version_kills_the_grant(memory_api: TestClient) -> None:
    admin_headers, _ = _admin(memory_api)
    mei_headers, mei_id = _member(memory_api, admin_headers, "mei")
    token = create_capability_token(
        user_id=mei_id,
        agent_id=PI_RUNTIME_AGENT_ID,
        memory_scope="read",
        auth_version=0,
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
    }
    assert (
        memory_api.post(
            "/internal/v1/agent-capabilities/get_my_memory",
            headers=headers,
            json={"arguments": {}},
        ).status_code
        == 200
    )

    # Bumping auth_version is how a password change or forced sign-out revokes
    # tokens; the memory grant must go with them rather than run to expiry.
    with transaction() as db:
        from app.models.auth import User

        user = db.get(User, mei_id)
        user.auth_version += 1
    revoked = memory_api.post(
        "/internal/v1/agent-capabilities/get_my_memory",
        headers=headers,
        json={"arguments": {}},
    )
    assert revoked.status_code == 403, revoked.text


# ---------------------------------------------------------------------------
# The snapshot route the Pi extension uses (A01, A08, A09).
# ---------------------------------------------------------------------------


def _snapshot(client: TestClient, token: str, **body):
    return client.post(
        "/internal/v1/memory/snapshot",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
        },
        json=body or {},
    )


def _read_token(user_id: str, auth_version: int = 0) -> str:
    return create_capability_token(
        user_id=user_id,
        agent_id=PI_RUNTIME_AGENT_ID,
        memory_scope="read",
        auth_version=auth_version,
    )


def test_each_employee_gets_only_their_own_snapshot(memory_api: TestClient) -> None:
    admin_headers, _ = _admin(memory_api)
    mei_headers, mei_id = _member(memory_api, admin_headers, "mei")
    fang_headers, fang_id = _member(memory_api, admin_headers, "fang")
    _save(
        memory_api,
        mei_headers,
        key="profile.self_reported_name",
        value="李梅",
        request_id="mei-1",
    )
    _save(
        memory_api,
        fang_headers,
        key="profile.self_reported_name",
        value="王芳",
        request_id="fang-1",
    )

    mei = _snapshot(memory_api, _read_token(mei_id)).json()
    fang = _snapshot(memory_api, _read_token(fang_id)).json()
    assert mei["addressing_name"] == "李梅"
    assert fang["addressing_name"] == "王芳"
    # The other employee's name is nowhere in the payload, even though both
    # accounts carry the same display name.
    assert "王芳" not in str(mei)
    assert "李梅" not in str(fang)


def test_an_unchanged_snapshot_is_not_resent(memory_api: TestClient) -> None:
    admin_headers, _ = _admin(memory_api)
    mei_headers, mei_id = _member(memory_api, admin_headers, "mei")
    _save(
        memory_api,
        mei_headers,
        key="preference.language",
        value="zh-CN",
        request_id="lang-1",
    )
    token = _read_token(mei_id)
    first = _snapshot(memory_api, token).json()
    again = _snapshot(
        memory_api,
        token,
        known_revision=first["revision"],
        known_epoch=first["epoch"],
    ).json()
    assert again["unchanged"] is True
    assert "entries" not in again

    # A write moves the revision, so the next call gets the new content rather
    # than being told nothing changed.
    _save(
        memory_api,
        mei_headers,
        key="preference.response_length",
        value="concise",
        request_id="len-1",
    )
    refreshed = _snapshot(
        memory_api,
        token,
        known_revision=first["revision"],
        known_epoch=first["epoch"],
    ).json()
    assert refreshed["unchanged"] is False
    assert {item["key"] for item in refreshed["entries"]} == {
        "preference.language",
        "preference.response_length",
    }


def test_forgetting_removes_the_value_from_the_next_snapshot(
    memory_api: TestClient,
) -> None:
    admin_headers, _ = _admin(memory_api)
    mei_headers, mei_id = _member(memory_api, admin_headers, "mei")
    saved = _save(
        memory_api,
        mei_headers,
        key="profile.preferred_name",
        value="小梅",
        request_id="nick-1",
    ).json()["entry"]
    token = _read_token(mei_id)
    assert _snapshot(memory_api, token).json()["addressing_name"] == "小梅"

    memory_api.post(
        f"/me/memories/{saved['id']}/forget",
        headers=mei_headers,
        json={"expected_version": saved["version"], "request_id": "forget-1"},
    )
    after = _snapshot(memory_api, token).json()
    assert "小梅" not in str(after)
    assert after["entries"] == []


def test_switching_memory_off_empties_the_snapshot_without_deleting_anything(
    memory_api: TestClient,
) -> None:
    admin_headers, _ = _admin(memory_api)
    mei_headers, mei_id = _member(memory_api, admin_headers, "mei")
    _save(
        memory_api,
        mei_headers,
        key="preference.language",
        value="zh-CN",
        request_id="lang-1",
    )
    settings_body = memory_api.get("/me/memories/settings", headers=mei_headers).json()
    memory_api.patch(
        "/me/memories/settings",
        headers=mei_headers,
        json={
            "use_enabled": False,
            "expected_revision": settings_body["revision"],
            "request_id": "off-1",
        },
    )
    payload = _snapshot(memory_api, _read_token(mei_id)).json()
    assert payload["use_enabled"] is False
    assert payload["entries"] == []
    # Turning it off stops the next model call using it; it deletes nothing.
    assert memory_api.get("/me/memories", headers=mei_headers).json()["items"]


def test_the_snapshot_route_cannot_be_reached_without_a_memory_scope(
    memory_api: TestClient,
) -> None:
    admin_headers, _ = _admin(memory_api)
    _, mei_id = _member(memory_api, admin_headers, "mei")
    stale = create_capability_token(
        user_id=mei_id,
        agent_id=PI_RUNTIME_AGENT_ID,
        personal_data_scope="own",
        allowed_tools=["search_my_chat_history"],
    )
    assert _snapshot(memory_api, stale).status_code == 403

    # And a revoked account loses it immediately rather than at expiry.
    token = _read_token(mei_id)
    assert _snapshot(memory_api, token).status_code == 200
    with transaction() as db:
        from app.models.auth import User

        db.get(User, mei_id).auth_version += 1
    assert _snapshot(memory_api, token).status_code == 403


# ---------------------------------------------------------------------------
# Which tools the Pi run is even told about (A03, A12).
# ---------------------------------------------------------------------------


def test_the_pi_run_derives_its_memory_scope_from_the_signed_grant(monkeypatch) -> None:
    """The runner reads the scope out of the token rather than being told it.

    One source of truth: a caller cannot hand Pi a read grant while announcing
    a propose scope, because there is no separate channel to disagree on.
    """

    from app.services.pi_runtime.runner import _memory_scope_from_capability

    monkeypatch.setattr(
        settings, "agent_capability_secret", "capability-secret-for-tests-32bytes"
    )
    monkeypatch.setattr(settings, "environment", "test")

    assert _memory_scope_from_capability(None) == "none"
    assert _memory_scope_from_capability("not-a-token") == "none"
    # A grant issued before memory existed: absence means none.
    assert (
        _memory_scope_from_capability(
            create_capability_token(
                user_id="user-mei",
                agent_id=PI_RUNTIME_AGENT_ID,
                personal_data_scope="own",
            )
        )
        == "none"
    )
    assert _memory_scope_from_capability(_read_token("user-mei")) == "read"
    assert (
        _memory_scope_from_capability(
            create_capability_token(
                user_id="user-mei",
                agent_id=PI_RUNTIME_AGENT_ID,
                memory_scope="propose",
                memory_turn_id="umt_x",
                memory_source_hash="hash",
                memory_epoch=1,
                auth_version=0,
            )
        )
        == "propose"
    )


def _web_memory_source(
    client: TestClient, headers: dict[str, str], owner: str, *, text: str = "我叫李梅"
):
    from app.api.chat import _persist_server_chat_messages

    created = client.post(
        "/chat/sessions", headers=headers, json={"title": "Memory source"}
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    context = {}
    _persist_server_chat_messages(
        user_id=owner,
        local_session_id=session_id,
        payloads=[{"id": f"memory-{session_id}", "role": "user", "content": text}],
        memory_context=context,
    )
    from app.models.auth import User

    with transaction() as db:
        auth_version = db.get(User, owner).auth_version
    token = create_capability_token(
        user_id=owner,
        agent_id=PI_RUNTIME_AGENT_ID,
        conversation_id=session_id,
        memory_scope="propose",
        memory_turn_id=context["turn_id"],
        memory_source_hash=context["source_hash"],
        memory_epoch=context["epoch"],
        auth_version=auth_version,
    )
    return session_id, context["turn_id"], token


@pytest.mark.parametrize("terminal_action", ["reject", "expire"])
def test_tool_proposal_replay_never_turns_an_unconfirmed_candidate_into_saved(
    memory_api: TestClient,
    terminal_action: str,
) -> None:
    headers, owner = _admin(memory_api)
    session_id, turn_id, token = _web_memory_source(memory_api, headers, owner)
    request = {
        "request_id": "same-tool-call",
        "arguments": {
            "key": "profile.self_reported_name",
            "value": "李梅",
            "quote": "我叫李梅",
            "quote_start": 0,
            "quote_end": 4,
            "self_attribution": "self",
            "temporality": "persistent",
            "explicitness": "explicit",
        },
    }
    grant = {
        "Authorization": f"Bearer {token}",
        "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
    }
    for _ in range(2):
        response = memory_api.post(
            "/internal/v1/agent-capabilities/propose_my_memory",
            headers=grant,
            json=request,
        )
        assert response.status_code == 200, response.text
        assert response.json()["inline"]["state"] == "pending"
        with transaction() as db:
            repo.close_turn(db, owner_user_id=owner, turn_id=turn_id)
    recovered = memory_api.get(
        "/me/memories/turns", headers=headers, params={"session_id": session_id}
    ).json()
    assert recovered["items"][0]["memory_status"] == "needs_confirmation"
    candidate = recovered["items"][0]["candidates"][0]
    if terminal_action == "reject":
        rejected = memory_api.post(
            f"/me/memories/{candidate['id']}/reject",
            headers=headers,
            json={
                "request_id": "reject-once",
                "expected_version": candidate["version"],
            },
        )
        assert rejected.status_code == 200, rejected.text
    else:
        from datetime import timedelta
        from app.core.security import now
        from app.models.user_memory import UserMemoryEntry

        with transaction() as db:
            db.get(UserMemoryEntry, candidate["id"]).expires_at = now() - timedelta(
                seconds=1
            )
    replay = memory_api.post(
        "/internal/v1/agent-capabilities/propose_my_memory", headers=grant, json=request
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["inline"]["state"] not in {"saved", "pending"}
    status = memory_api.get(f"/me/memories/turns/{turn_id}", headers=headers).json()
    assert status["memory_status"] == "none"
    assert status["candidates"] == []


def test_only_new_authenticated_messages_enqueue_with_an_unset_cutoff(
    memory_api: TestClient,
) -> None:
    from sqlalchemy import select
    from app.api.chat import _persist_server_chat_messages
    from app.models.user_memory import UserMemoryJob

    headers, owner = _admin(memory_api)
    created = memory_api.post(
        "/chat/sessions", headers=headers, json={"title": "Restore"}
    )
    session_id = created.json()["id"]
    with transaction() as db:
        state = repo.get_or_create_state(db, owner)
        state.learning_mode = "suggest"
        state.automatic_source_cutoff_at = None
    copied = {"id": "restored-source", "role": "user", "content": "我叫李梅"}
    _persist_server_chat_messages(
        user_id=owner, local_session_id=session_id, payloads=[copied]
    )
    context = {}
    _persist_server_chat_messages(
        user_id=owner,
        local_session_id=session_id,
        payloads=[copied],
        memory_context=context,
    )
    assert (
        memory_api.get(
            "/me/memories/turns", headers=headers, params={"session_id": session_id}
        ).json()["items"]
        == []
    )
    _persist_server_chat_messages(
        user_id=owner,
        local_session_id=session_id,
        payloads=[
            {"id": "new-authenticated-source", "role": "user", "content": "我叫王芳"}
        ],
        memory_context=context,
    )
    recovered = memory_api.get(
        "/me/memories/turns", headers=headers, params={"session_id": session_id}
    ).json()["items"]
    assert len(recovered) == 1
    assert recovered[0]["memory_status"] == "pending"
    with transaction() as db:
        job = db.scalar(
            select(UserMemoryJob).where(UserMemoryJob.owner_user_id == owner)
        )
        assert job is not None and job.turn_id == recovered[0]["turn_id"]


def test_closed_turn_loses_online_authority_and_other_owner_cannot_recover_notice(
    memory_api: TestClient,
) -> None:
    from app.api.chat import _rotate_memory_turn_for_control_input

    admin_headers, _ = _admin(memory_api)
    mei_headers, mei_id = _member(memory_api, admin_headers, "mei")
    session_id, turn_id, token = _web_memory_source(memory_api, mei_headers, mei_id)
    _rotate_memory_turn_for_control_input(user_id=mei_id, session_id=session_id, turn_id=turn_id)
    denied = memory_api.post(
        "/internal/v1/agent-capabilities/propose_forget_my_memory",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
        },
        json={
            "request_id": "closed-turn",
            "arguments": {
                "key": "profile.self_reported_name",
                "quote": "我叫李梅",
                "quote_start": 0,
                "quote_end": 4,
            },
        },
    )
    assert denied.status_code == 403
    assert (
        memory_api.get(
            "/me/memories/turns",
            headers=admin_headers,
            params={"session_id": session_id},
        ).status_code
        == 404
    )
    assert (
        memory_api.get(
            f"/me/memories/turns/{turn_id}", headers=admin_headers
        ).status_code
        == 404
    )
    own = memory_api.get(f"/me/memories/turns/{turn_id}", headers=mei_headers)
    assert own.status_code == 200 and own.json()["memory_status"] == "none"


@pytest.mark.parametrize(
    "require_approval,execute",
    [(False, False), (True, False), (True, True)],
)
def test_memory_tool_preflight_logs_only_metadata(
    memory_api, monkeypatch, caplog, require_approval, execute
):
    from app.api import harness as harness_api
    from app.models.harness import HarnessApproval
    from app.repositories import harness as harness_repo
    from app.services.harness.audit import HarnessAuditLogger
    from app.services.harness.policy import DecisionKind, PolicyEngine, PolicyRule

    monkeypatch.setattr(settings, "pi_harness_gateway_token", "isolated-memory-gateway")
    value = "PRIVATE-MEMORY-VALUE-7341"
    quote = "PRIVATE-SOURCE-QUOTE-9287"
    rules = (
        [
            PolicyRule(
                id="private-memory-review",
                tool_pattern="nexus_propose_my_memory",
                arg_pattern=value,
                decision=DecisionKind.ASK,
            )
        ]
        if require_approval
        else []
    )
    monkeypatch.setattr(harness_api, "_policy_engine", PolicyEngine(rules=rules))
    task_id = "memory-private-task" if execute else "anonymous"
    headers = (
        _admin(memory_api)[0]
        if execute
        else {"Authorization": "Bearer isolated-memory-gateway"}
    )
    with caplog.at_level("INFO"):
        response = memory_api.post(
            f"/admin/harness/tasks/{task_id}/execute"
            if execute
            else "/admin/harness/tools/evaluate",
            headers=headers,
            json={
                "tool_name": "nexus_propose_my_memory",
                "arguments": {
                    "key": "profile.preferred_name",
                    "value": value,
                    "quote": quote,
                },
            },
        )
    assert response.status_code == 200
    assert response.json()["success"] is not require_approval
    assert response.json()["approval_required"] is require_approval
    entries = HarnessAuditLogger().list_entries(task_id=task_id)
    assert any(
        entry["details"].get("tool") == "nexus_propose_my_memory" for entry in entries
    )
    assert value not in caplog.text and quote not in caplog.text
    assert value not in str(entries) and quote not in str(entries)
    if require_approval:
        with transaction() as db:
            approvals = harness_repo.list_approvals(db, task_id=task_id)
            approval = db.get(HarnessApproval, response.json()["approval_request_id"])
            assert approval is not None and approval.status == "pending"
            assert value not in approval.arguments_json
            assert quote not in approval.arguments_json
        assert len(approvals) == 1
        assert approvals[0]["request_id"] == response.json()["approval_request_id"]
        assert value not in str(approvals) and quote not in str(approvals)


def test_forget_proposal_receipt_survives_closed_turn_and_revocation(memory_api):
    headers, owner = _admin(memory_api)
    saved = _save(
        memory_api,
        headers,
        key="profile.preferred_name",
        value="小梅",
        request_id="saved-name",
    ).json()["entry"]
    text = "请忘记我的称呼"
    _, turn_id, token = _web_memory_source(memory_api, headers, owner, text=text)
    grant = {
        "Authorization": f"Bearer {token}",
        "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
    }
    path = "/internal/v1/agent-capabilities/propose_forget_my_memory"
    request = {
        "request_id": "forget-tool-call",
        "arguments": {
            "key": "profile.preferred_name",
            "quote": text,
            "quote_start": 0,
            "quote_end": len(text),
        },
    }
    first = memory_api.post(path, headers=grant, json=request)
    assert first.status_code == 200
    assert first.json()["inline"]["memory_id"] == saved["id"]
    with transaction() as db:
        repo.close_turn(db, owner_user_id=owner, turn_id=turn_id)
    replay = memory_api.post(path, headers=grant, json=request)
    assert replay.status_code == 200
    assert replay.json()["inline"] == first.json()["inline"]
    changed = memory_api.post(
        path,
        headers=grant,
        json={
            **request,
            "arguments": {**request["arguments"], "key": "profile.self_reported_name"},
        },
    )
    assert changed.status_code == 409
    new_request = memory_api.post(
        path,
        headers=grant,
        json={**request, "request_id": "new-after-close"},
    )
    assert new_request.status_code == 403
    forgotten = memory_api.post(
        f"/me/memories/{saved['id']}/forget",
        headers=headers,
        json={"request_id": "explicit-forget", "expected_version": saved["version"]},
    )
    assert forgotten.status_code == 200
    revoked = memory_api.post(path, headers=grant, json=request)
    assert revoked.status_code == 200
    assert revoked.json()["inline"]["memory_id"] is None
    assert revoked.json()["inline"]["state"] == "nothing_to_forget"


@pytest.mark.parametrize("action", ["confirm", "edit", "reject", "forget"])
def test_expired_candidate_cannot_mutate_but_can_be_forgotten(memory_api, action):
    from datetime import timedelta
    from app.core.security import now
    from app.models.user_memory import UserMemoryEntry

    headers, owner = _admin(memory_api)
    with transaction() as db:
        turn = repo.register_turn(
            db,
            owner_user_id=owner,
            channel="manual",
            request_key="ttl-source",
            raw_text="当前工作重点",
        )
        entry = repo.propose_entry(
            db,
            owner_user_id=owner,
            key="work.current_focus",
            value="当前工作重点",
            origin="manual",
            turn_id=turn.id,
            expires_at=now() + timedelta(minutes=1),
        )
        entry_id, version = entry.id, entry.version
    visible = memory_api.get("/me/memories?status=candidate", headers=headers)
    assert visible.status_code == 200
    assert visible.json()["items"][0]["id"] == entry_id
    with transaction() as db:
        db.get(UserMemoryEntry, entry_id).expires_at = now() - timedelta(seconds=1)
    payload = {"request_id": f"ttl-{action}", "expected_version": version}
    if action == "edit":
        result = memory_api.patch(
            f"/me/memories/{entry_id}",
            headers=headers,
            json={**payload, "value": "不得保存的过期改动"},
        )
    else:
        result = memory_api.post(
            f"/me/memories/{entry_id}/{action}",
            headers=headers,
            json=payload,
        )
    assert result.status_code == (200 if action == "forget" else 409), result.text
    if action == "forget":
        assert result.json()["entry"]["status"] == "forgotten"
        assert result.json()["entry"]["value"] is None
    current = memory_api.get("/me/memories?status=candidate", headers=headers)
    assert current.json()["items"] == []
