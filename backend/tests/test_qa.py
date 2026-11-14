from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from app.api import qa as qa_api
from app.core import database
from app.core.config import settings
from app.core.database import transaction
from app.main import app
from app.models.qa import QaTurn
from app.core.security import now
from app.models.model_endpoints import ModelEndpoint
from app.repositories import qa as qa_repository
from app.services import qa_service
from tests._auth import bootstrap_admin_headers


def _admin_headers(client: TestClient) -> dict[str, str]:
    return bootstrap_admin_headers(client)


def _accept_and_login(client: TestClient, username: str) -> dict:
    invited = client.post(
        "/admin/users",
        headers=_admin_headers(client),
        json={
            "username": username,
            "email": f"{username}@example.com",
            "display_name": username,
        },
    )
    assert invited.status_code == 201
    accepted = client.post(
        f"/auth/invitations/{invited.json()['invite_token']}/accept",
        json={"password": "Member-password-123", "password_confirmation": "Member-password-123"},
    )
    assert accepted.status_code == 200
    login = client.post(
        "/auth/login", json={"username": username, "password": "Member-password-123"}
    )
    assert login.status_code == 200
    return login.json()


def _create_space(client: TestClient, name: str) -> tuple[dict, dict[str, str]]:
    headers = _admin_headers(client)
    response = client.post(
        "/knowledge-spaces", headers=headers, json={"name": name, "description": ""}
    )
    assert response.status_code == 201
    return response.json(), headers


def _grant_reader(client: TestClient, space_id: str, username: str) -> tuple[dict, dict[str, str]]:
    login = _accept_and_login(client, username)
    response = client.post(
        f"/knowledge-spaces/{space_id}/members",
        headers=_admin_headers(client),
        json={"user_id": login["user"]["id"], "role": "reader"},
    )
    assert response.status_code == 201
    return login["user"], {"Authorization": f"Bearer {login['access_token']}"}


def _create_ready_document(client: TestClient, space_id: str, headers: dict[str, str], name: str) -> dict:
    created = client.post(
        f"/knowledge-spaces/{space_id}/documents",
        headers=headers,
        files={"file": (f"{name}.txt", f"{name} test content.".encode(), "text/plain")},
        data={"name": name},
    )
    assert created.status_code == 201
    document = created.json()["document"]
    version_id = document["latest_version"]["id"]
    with transaction() as db:
        db.execute(
            text(
                "UPDATE document_versions SET processing_stage = 'ready', queryable = 1, "
                "is_current_published = 1 WHERE id = :id"
            ),
            {"id": version_id},
        )
    return {**document, "latest_version": {**document["latest_version"], "id": version_id}}


def _create_session(client: TestClient, headers: dict[str, str], space_ids: list[str]) -> dict:
    response = client.post("/qa/sessions", headers=headers, json={"space_ids": space_ids})
    assert response.status_code == 201
    return response.json()


def _isolated_client(tmp_path: Path, name: str):
    original_path, original_url = settings.db_path, settings.database_url
    database_path = tmp_path / name
    settings.db_path = database_path
    settings.database_url = f"sqlite:///{database_path}"
    return original_path, original_url, database_path


def _restore_database(original_path: Path, original_url: str, database_path: Path) -> None:
    settings.db_path, settings.database_url = original_path, original_url
    database.reset_database(database_path)


def test_scope_is_authorized_before_a_session_can_select_it(tmp_path: Path):
    """A retrieval scope must not be created around an unauthorized space."""
    original_path, original_url, database_path = _isolated_client(tmp_path, "qa-scope.sqlite3")
    try:
        with TestClient(app) as client:
            allowed, _ = _create_space(client, "已授权资料")
            private, _ = _create_space(client, "未授权资料")
            _, reader_headers = _grant_reader(client, allowed["id"], "qa-scope-reader")

            created = client.post(
                "/qa/sessions",
                headers=reader_headers,
                json={"space_ids": [allowed["id"], private["id"]]},
            )

            assert created.status_code == 404
            assert "未授权资料" not in created.text
            allowed_session = client.post(
                "/qa/sessions", headers=reader_headers, json={"space_ids": [allowed["id"]]}
            )
            assert allowed_session.status_code == 201
            assert allowed_session.json()["space_ids"] == [allowed["id"]]
            current_scope = client.get(
                f"/knowledge-spaces/{allowed['id']}", headers=reader_headers
            ).json()["scope_version"]
            assert allowed_session.json()["scope_version"] == {allowed["id"]: current_scope}
    finally:
        _restore_database(original_path, original_url, database_path)


def test_no_evidence_finishes_as_knowledge_gap_without_a_fabricated_answer(tmp_path: Path):
    """Removing the no-evidence branch must fail this test by inventing answer text."""
    original_path, original_url, database_path = _isolated_client(tmp_path, "qa-gap.sqlite3")
    try:
        with TestClient(app) as client:
            space, _ = _create_space(client, "待处理资料")
            _, reader_headers = _grant_reader(client, space["id"], "qa-gap-reader")
            session = _create_session(client, reader_headers, [space["id"]])

            turn = client.post(
                f"/qa/sessions/{session['id']}/turns",
                headers=reader_headers,
                json={"question": "产品支持哪些部署方式？"},
            )

            assert turn.status_code == 201
            payload = turn.json()
            assert payload["status"] == "knowledge_gap"
            assert payload["answer"] is None
            assert payload["citations"] == []
            assert [event["type"] for event in payload["events"]] == [
                "status",
                "retrieval_summary",
                "verification_summary",
                "usage",
                "status",
            ]
            assert payload["events"][-1]["status"] == "knowledge_gap"
    finally:
        _restore_database(original_path, original_url, database_path)


def test_turn_rejects_client_citation_injection_from_another_scope(tmp_path: Path):
    """A client must never attach a Citation record that the provider did not create."""
    original_path, original_url, database_path = _isolated_client(tmp_path, "qa-citation-injection.sqlite3")
    try:
        with TestClient(app) as client:
            space, _ = _create_space(client, "授权资料")
            _, reader_headers = _grant_reader(client, space["id"], "qa-citation-reader")
            session = _create_session(client, reader_headers, [space["id"]])

            turn = client.post(
                f"/qa/sessions/{session['id']}/turns",
                headers=reader_headers,
                json={
                    "question": "请总结授权资料。",
                    "citation_ids": ["cit_from_another_scope"],
                },
            )

            assert turn.status_code == 422
    finally:
        _restore_database(original_path, original_url, database_path)


def test_revoked_source_open_returns_no_new_content(tmp_path: Path):
    """A Citation source-open request rechecks access instead of replaying stored evidence."""
    original_path, original_url, database_path = _isolated_client(tmp_path, "qa-revoked-source.sqlite3")
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "可核验证据")
            document = _create_ready_document(client, space["id"], admin_headers, "产品白皮书")
            reader, reader_headers = _grant_reader(client, space["id"], "qa-revoked-reader")
            session = _create_session(client, reader_headers, [space["id"]])
            turn = client.post(
                f"/qa/sessions/{session['id']}/turns",
                headers=reader_headers,
                json={"question": "产品白皮书是否可供核验？"},
            )
            assert turn.status_code == 201
            citation = turn.json()["citations"][0]
            assert citation["document_id"] == document["id"]

            removed = client.delete(
                f"/knowledge-spaces/{space['id']}/members/{reader['id']}", headers=admin_headers
            )
            assert removed.status_code == 204
            source = client.get(f"/qa/citations/{citation['id']}/source", headers=reader_headers)

            assert source.status_code == 404
            assert "产品白皮书" not in source.text
            assert citation["excerpt"] not in source.text
    finally:
        _restore_database(original_path, original_url, database_path)


@pytest.mark.parametrize("evidence_change", ["revoked", "moved", "disabled"])
def test_get_turn_redacts_a_citation_when_current_evidence_is_no_longer_usable(
    tmp_path: Path, evidence_change: str
):
    """Changing Citation access after completion must never replay its title or excerpt."""
    original_path, original_url, database_path = _isolated_client(
        tmp_path, f"qa-turn-redaction-{evidence_change}.sqlite3"
    )
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "原始范围")
            document = _create_ready_document(client, space["id"], admin_headers, "机密产品白皮书")
            reader, reader_headers = _grant_reader(client, space["id"], f"qa-redacted-{evidence_change}")
            session = _create_session(client, reader_headers, [space["id"]])
            created = client.post(
                f"/qa/sessions/{session['id']}/turns",
                headers=reader_headers,
                json={"question": "这份资料是否仍可核验？"},
            )
            assert created.status_code == 201
            citation = created.json()["citations"][0]

            if evidence_change == "revoked":
                assert client.delete(
                    f"/knowledge-spaces/{space['id']}/members/{reader['id']}", headers=admin_headers
                ).status_code == 204
            elif evidence_change == "moved":
                moved_space, _ = _create_space(client, "迁移后的私有范围")
                with transaction() as db:
                    db.execute(
                        text("UPDATE documents SET space_id = :space_id WHERE id = :id"),
                        {"space_id": moved_space["id"], "id": document["id"]},
                    )
            else:
                with transaction() as db:
                    db.execute(
                        text("UPDATE documents SET status = 'archived' WHERE id = :id"),
                        {"id": document["id"]},
                    )

            fetched = client.get(f"/qa/turns/{created.json()['id']}", headers=reader_headers)

            assert fetched.status_code == 200
            payload = fetched.json()
            assert payload["status"] == "source_permission_denied"
            assert payload["answer"] is None
            assert payload["citations"] == []
            assert payload["events"][-1]["type"] == "status"
            assert payload["events"][-1]["status"] == "source_permission_denied"
            assert "机密产品白皮书" not in fetched.text
            assert citation["excerpt"] not in fetched.text
            stable = client.get(f"/qa/turns/{created.json()['id']}", headers=reader_headers)
            assert stable.status_code == 200
            assert stable.json()["status"] == "source_permission_denied"
            assert stable.json()["citations"] == []
            with transaction() as db:
                persisted_turn = db.get(QaTurn, created.json()["id"])
                assert persisted_turn is not None
                assert persisted_turn.status == "source_permission_denied"
                assert persisted_turn.answer is None
                assert qa_repository.load_snapshot(persisted_turn.retrieval_summary) == {
                    "provider": "permission_safe_snapshot",
                    "candidate_count": 0,
                }
                assert qa_repository.load_snapshot(persisted_turn.usage) == {
                    "provider": "deterministic_document_version_metadata",
                    "input_tokens": 0,
                    "input_uncached_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost": "0",
                    "latency_ms": 0,
                }
                assert qa_repository.load_snapshot(persisted_turn.events)[-1] == {
                    "type": "status",
                    "status": "source_permission_denied",
                }
                assert qa_repository.citations_for_turn(db, persisted_turn.id) == []
    finally:
        _restore_database(original_path, original_url, database_path)


def test_turn_creation_persists_source_permission_denied_after_scope_revocation(tmp_path: Path):
    """A revoked scope becomes a safe terminal Turn, never a raw authorization response."""
    original_path, original_url, database_path = _isolated_client(tmp_path, "qa-create-revoked.sqlite3")
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "可撤销范围")
            reader, reader_headers = _grant_reader(client, space["id"], "qa-create-revoked")
            session = _create_session(client, reader_headers, [space["id"]])
            assert client.delete(
                f"/knowledge-spaces/{space['id']}/members/{reader['id']}", headers=admin_headers
            ).status_code == 204

            created = client.post(
                f"/qa/sessions/{session['id']}/turns",
                headers=reader_headers,
                json={"question": "撤权后不得读取资料"},
            )

            assert created.status_code == 201
            payload = created.json()
            assert payload["status"] == "source_permission_denied"
            assert payload["answer"] is None
            assert payload["citations"] == []
            assert payload["usage"] == {
                "provider": "deterministic_document_version_metadata",
                "input_tokens": 0,
                "input_uncached_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost": "0",
                "latency_ms": 0,
            }
            assert payload["events"][-1]["type"] == "status"
            assert payload["events"][-1]["status"] == "source_permission_denied"
            persisted = client.get(f"/qa/turns/{payload['id']}", headers=reader_headers)
            assert persisted.status_code == 200
            assert persisted.json()["status"] == "source_permission_denied"
    finally:
        _restore_database(original_path, original_url, database_path)


def test_turn_creation_persists_budget_exceeded_from_the_budget_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A provider budget refusal becomes a zero-usage terminal snapshot rather than an HTTP error."""
    original_path, original_url, database_path = _isolated_client(tmp_path, "qa-budget.sqlite3")
    try:
        with TestClient(app) as client:
            space, _ = _create_space(client, "预算范围")
            _, reader_headers = _grant_reader(client, space["id"], "qa-budget-reader")
            session = _create_session(client, reader_headers, [space["id"]])

            def reject_budget(*_args, **_kwargs):
                raise qa_api.QaBudgetExceeded()

            monkeypatch.setattr(qa_api, "preflight_budget", reject_budget)
            created = client.post(
                f"/qa/sessions/{session['id']}/turns",
                headers=reader_headers,
                json={"question": "预算不足时不应泄露资料"},
            )

            assert created.status_code == 201
            payload = created.json()
            assert payload["status"] == "budget_exceeded"
            assert payload["answer"] is None
            assert payload["citations"] == []
            assert payload["usage"] == {
                "provider": "deterministic_document_version_metadata",
                "input_tokens": 0,
                "input_uncached_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost": "0",
                "latency_ms": 0,
            }
            assert payload["events"][-1]["type"] == "status"
            assert payload["events"][-1]["status"] == "budget_exceeded"
            assert "预算范围" not in created.text
            persisted = client.get(f"/qa/turns/{payload['id']}", headers=reader_headers)
            assert persisted.status_code == 200
            assert persisted.json()["status"] == "budget_exceeded"
    finally:
        _restore_database(original_path, original_url, database_path)


def test_llm_generates_answer_with_citations_and_usage_from_knowledge_base(tmp_path: Path):
    """When a Chat model endpoint is configured, the turn answer is LLM-generated
    with real document excerpts as citations and accurate usage tracking."""
    original_path, original_url, database_path = _isolated_client(tmp_path, "qa-llm.sqlite3")
    try:
        with TestClient(app) as client:
            space, admin_headers = _create_space(client, "产品知识库")
            _create_ready_document(client, space["id"], admin_headers, "产品白皮书v3")
            reader, reader_headers = _grant_reader(client, space["id"], "qa-llm-reader")

            # Insert a Chat model endpoint and route QA through pi_chat_model
            with transaction() as db:
                timestamp = now()
                endpoint = ModelEndpoint(
                    id="mep_test_qa_llm",
                    provider="openai",
                    model="gpt-4o-mini",
                    capability="chat",
                    endpoint_url="https://api.example.com/v1",
                    region="global",
                    status="active",
                    secret_ref="env://TEST_API_KEY",
                    allowed_data_level="internal",
                    max_context=128000,
                    timeout_ms=30000,
                    concurrency_limit=8,
                    supports_streaming_usage=True,
                    supports_structured_output=True,
                    supports_vision=False,
                    supports_cache_pricing=False,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                from app.repositories import system_config
                from app.repositories.model_endpoints import sync_legacy_models

                sync_legacy_models(endpoint, timestamp=timestamp)
                db.add(endpoint)

                system_config.upsert(db, "pi_chat_model", endpoint.id, "chat route")
                db.flush()

            # Mock the LLM HTTP call
            mock_response = MagicMock()
            mock_response.raise_for_status.return_value = None
            mock_response.json.return_value = {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "根据产品白皮书v3，产品支持以下部署方式：\n\n"
                                "1. **私有化部署**：在企业内部服务器上安装运行 [CIT-XXXX]。\n"
                                "2. **云托管部署**：通过云端 SaaS 平台使用 [CIT-XXXX]。\n\n"
                                "具体配置请参考产品白皮书详细说明。"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 250,
                    "completion_tokens": 80,
                    "total_tokens": 330,
                },
            }

            # The citation IDs in the response won't match the actual generated IDs,
            # but the test verifies that the LLM path produces a non-fallback answer
            # with proper citations and usage metrics.
            with patch("httpx.post", return_value=mock_response) as mocked_post:
                session = _create_session(client, reader_headers, [space["id"]])
                turn = client.post(
                    f"/qa/sessions/{session['id']}/turns",
                    headers=reader_headers,
                    json={"question": "产品支持哪些部署方式？"},
                )

            assert turn.status_code == 201
            payload = turn.json()
            mocked_post.assert_called()
            assert (
                mocked_post.call_args.args[0]
                == "https://api.example.com/v1/chat/completions"
            )

            # Answer is LLM-generated (not the fallback text)
            assert payload["status"] == "completed"
            assert payload["answer"] is not None
            assert "私有化部署" in payload["answer"]
            assert "已找到可核验的授权文档版本" not in payload["answer"]  # not fallback

            # Citations exist and contain real document excerpts
            assert len(payload["citations"]) > 0
            citation = payload["citations"][0]
            assert citation["document_name"] == "产品白皮书v3"
            assert "产品白皮书v3 test content" in citation["excerpt"]
            assert citation["support_state"] == "supported"

            # Usage is tracked from the LLM response
            assert payload["usage"]["provider"] == "openai"
            assert payload["usage"]["model"] == "gpt-4o-mini"
            assert payload["usage"]["input_tokens"] == 250
            assert payload["usage"]["output_tokens"] == 80
            assert payload["usage"]["total_tokens"] == 330
            assert payload["usage"]["latency_ms"] >= 0  # 0 when mocked

            # Verification summary includes citation coverage
            assert payload["verification_summary"]["state"] in ("supported", "partial")
            assert payload["verification_summary"]["total_evidence"] > 0

            # Events include all required phases
            event_types = [e["type"] for e in payload["events"]]
            assert "retrieval_summary" in event_types
            assert "answer_delta" in event_types
            assert "citation" in event_types
            assert "verification_summary" in event_types
            assert "usage" in event_types
    finally:
        _restore_database(original_path, original_url, database_path)


def test_qa_messages_use_runtime_persona(monkeypatch):
    monkeypatch.setattr(qa_service, "qa_system_prompt", lambda: "动态默认人设")

    messages = qa_service._build_messages("证据内容", "用户问题")

    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith("动态默认人设\n\n")
    assert "严格基于提供的证据材料" in messages[0]["content"]


def test_call_anthropic_llm_official_host_uses_messages_wire_format(monkeypatch):
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "sk-ant-test123")
    endpoint = SimpleNamespace(
        id="mep-anthropic",
        provider="anthropic",
        adapter="anthropic",
        api_protocol="messages",
        model="claude-sonnet-4-5",
        endpoint_url="https://api.anthropic.com",
        secret_ref="env://TEST_ANTHROPIC_KEY",
    )
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "content": [{"type": "text", "text": "答案文本"}],
        "usage": {
            "input_tokens": 120,
            "output_tokens": 40,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 7,
        },
    }
    with patch("httpx.post", return_value=mock_response) as mocked_post:
        answer, usage = qa_service._call_anthropic_llm(
            endpoint,
            [
                {"role": "system", "content": "系统指令"},
                {"role": "user", "content": "你好"},
            ],
        )
    mocked_post.assert_called()
    request_url = mocked_post.call_args.args[0]
    assert request_url == "https://api.anthropic.com/v1/messages"
    _, kwargs = mocked_post.call_args
    payload = kwargs["json"]
    assert payload["model"] == "claude-sonnet-4-5"
    assert payload["messages"] == [{"role": "user", "content": "你好"}]
    assert payload["system"] == "系统指令"
    assert "max_tokens" in payload
    headers = kwargs["headers"]
    assert headers["x-api-key"] == "sk-ant-test123"
    assert "anthropic-version" in headers

    assert answer == "答案文本"
    assert usage["model"] == "claude-sonnet-4-5"
    assert usage["input_tokens"] == 120
    assert usage["output_tokens"] == 40
    assert usage["cache_write_tokens"] == 10
    assert usage["provider"] == "anthropic"
    assert usage["reported_total_tokens"] == 177


def test_call_llm_dispatches_anthropic_protocol_to_messages_route(monkeypatch):
    monkeypatch.setenv("TEST_ANTHROPIC_DISPATCH_KEY", "sk-ant-dispatch")
    endpoint = SimpleNamespace(
        id="mep-anthropic",
        provider="anthropic",
        adapter="anthropic",
        api_protocol="messages",
        model="claude-sonnet-4-5",
        endpoint_url="https://proxy.example.test",
        secret_ref="env://TEST_ANTHROPIC_DISPATCH_KEY",
    )
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "content": [{"type": "text", "text": "分派答案"}],
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }
    with patch("httpx.post", return_value=mock_response) as mocked_post:
        answer, usage = qa_service._call_llm(
            endpoint,
            [{"role": "user", "content": "hi"}],
        )
    assert mocked_post.call_args.args[0].endswith("/v1/messages")
    assert answer == "分派答案"
    assert usage["input_tokens"] == 3


def test_call_anthropic_llm_proxy_host_uses_bearer(monkeypatch):
    monkeypatch.setenv("TEST_ANTHROPIC_PROXY_KEY", "sk-ant-proxy-456")
    endpoint = SimpleNamespace(
        id="mep-anthropic-proxy",
        provider="anthropic",
        adapter="anthropic",
        api_protocol="messages",
        model="claude-sonnet-4-5",
        endpoint_url="https://proxy.example.test/v1",
        secret_ref="env://TEST_ANTHROPIC_PROXY_KEY",
    )
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "content": [{"type": "text", "text": "代理答案"}],
        "usage": {"input_tokens": 5, "output_tokens": 5},
    }
    with patch("httpx.post", return_value=mock_response) as mocked_post:
        answer, _ = qa_service._call_anthropic_llm(
            endpoint,
            [{"role": "user", "content": "hi"}],
        )
    assert (
        mocked_post.call_args.args[0]
        == "https://proxy.example.test/v1/messages"
    )
    headers = mocked_post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer sk-ant-proxy-456"
    assert "x-api-key" not in headers
    assert answer == "代理答案"
