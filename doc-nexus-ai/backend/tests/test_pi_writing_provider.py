from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import database
from app.core.config import settings
from app.core.database import transaction
from app.main import app
from app.models.writing import (
    AgentRunBinding,
    WritingCandidate,
    WritingDispatchOutbox,
    WritingProject,
    WritingToolReceipt,
)
from app.repositories import writing as writing_repo
from app.services.pi_agent_client import (
    PiAgentCancelledError,
    PiAgentClient,
    PiAgentError,
    PiAgentResult,
    parse_json_reply,
    resolve_pi_agent_endpoint,
)
from app.services.pi_writing_provider import PiWritingProvider
from app.services.writing_dispatcher import WritingDispatcher
from tests._auth import bootstrap_admin_response


def _headers(client: TestClient) -> dict[str, str]:
    response = bootstrap_admin_response(client)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


class FakePiClient:
    agent_id = "pi-agent-test"

    def __init__(
        self,
        payload: dict | None = None,
        *,
        payloads: list[dict | BaseException] | None = None,
        cost: str | None = None,
        fixed_session_id: str = "pi-session-test",
    ):
        self.payload = payload
        self.payloads = list(payloads or [])
        self.cost = cost
        self.fixed_session_id = fixed_session_id
        self.prompts: list[str] = []
        self.session_ids: list[str | None] = []
        self.idempotency_keys: list[str | None] = []

    def chat(
        self,
        message: str,
        *,
        session_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> PiAgentResult:
        self.prompts.append(message)
        self.session_ids.append(session_id)
        self.idempotency_keys.append(idempotency_key)
        if self.payloads:
            payload = self.payloads.pop(0)
        else:
            assert self.payload is not None
            payload = self.payload
        # A queued exception models a provider-side failure for this attempt.
        if isinstance(payload, BaseException):
            raise payload
        return PiAgentResult(
            payload=payload,
            session_id=session_id or self.fixed_session_id,
            model="qwen3.7-plus",
            run_time_ms=321,
            token_usage={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
            trace_steps=7,
            cost=self.cost,
        )


def _usage_ledger(
    *,
    total_tokens: int = 0,
    scopes: dict[str, int] | None = None,
) -> dict:
    return {
        "input_tokens": total_tokens,
        "input_uncached_tokens": total_tokens,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "output_tokens": 0,
        "total_tokens": total_tokens,
        "latency_ms": 0,
        "cost": "unknown",
        "provider": "pi_agent_chat",
        "model": "qwen3.7-plus",
        "budget_tokens_by_scope": dict(scopes or {}),
    }


def test_parse_json_reply_accepts_fenced_object():
    assert parse_json_reply('```json\n{"passed":true}\n```') == {"passed": True}


@pytest.mark.parametrize(
    "scope",
    ["outline", "document:wch_Y9u2BEOBYo9UvQ", "rewrite:wch:with.dots", "verify"],
)
def test_writing_session_ids_satisfy_the_pi_cli_contract(scope: str):
    from app.services.pi_writing_provider import _writing_session_id

    first = _writing_session_id("wop_VEUXeRe3Sx4x3g", scope=scope, attempt=1)
    second = _writing_session_id("wop_VEUXeRe3Sx4x3g", scope=scope, attempt=2)
    # The Pi CLI refuses any other charset, and a retry must never resume the
    # session that already produced a rejected answer.
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]", first)
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]", second)
    assert first != second
    assert _writing_session_id("wop_other", scope=scope, attempt=1) != first


@pytest.mark.parametrize("markdown", [
    '本项目为"2026年全息舱投影数字人创新项目"，属于研发类创新项目。',
    '他说 "结论": 很重要。',
])
def test_parse_json_reply_repairs_quotes_inside_markdown(markdown: str):
    reply = (
        "Now let me compose the chapter.\n"
        '{"sections":[{"chapter_id":"wch_test",'
        f'"markdown":"{markdown}",'
        '"verification":{"passed":true}}]}'
    )
    payload = parse_json_reply(reply)
    assert payload["sections"][0]["markdown"] == markdown


def test_pi_agent_client_uses_managed_chat_contract(monkeypatch):
    monkeypatch.setattr(settings, "pi_agent_require_published", False)
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["authorization"] = request.headers["authorization"]
        observed["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "reply": '{"passed":true}',
                "session_id": "session-1",
                "effective_model": "qwen3.7-plus",
                "run_time_ms": 25,
                "token_usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
                "execution_trace": [{}, {}],
                "cost": "0.42",
            },
        )

    client = PiAgentClient(
        base_url="http://platform.test",
        api_key="managed-key",
        agent_id="pi-1",
        transport=httpx.MockTransport(handler),
    )
    result = client.chat("write")
    assert observed["authorization"] == "Bearer managed-key"
    assert observed["body"] == {
        "message": "write",
        "new_session": True,
        "require_published": False,
        "stream": False,
        "session_type": "api_chat",
    }
    assert result.payload == {"passed": True}
    assert result.trace_steps == 2
    assert result.cost == "0.42"


def test_pi_agent_client_resumes_session(monkeypatch):
    monkeypatch.setattr(settings, "pi_agent_require_published", False)
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "reply": '{"ok":true}',
                "session_id": "session-resume",
                "token_usage": {},
            },
        )

    client = PiAgentClient(
        base_url="http://platform.test",
        api_key="managed-key",
        agent_id="pi-1",
        transport=httpx.MockTransport(handler),
    )
    client.chat("continue", session_id="session-existing")
    assert observed["body"]["new_session"] is False
    assert observed["body"]["session_id"] == "session-existing"


def test_pi_agent_errors_when_not_configured(monkeypatch):
    from app.services.pi_agent_client import PiAgentError

    monkeypatch.setattr(settings, "pi_agent_url", "")
    monkeypatch.setattr(settings, "pi_agent_api_key", "")
    monkeypatch.setattr(settings, "pi_agent_id", "")

    with pytest.raises(PiAgentError, match="未配置"):
        resolve_pi_agent_endpoint()


def test_dispatch_cancellation_during_pi_call_is_terminal(tmp_path: Path, monkeypatch):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-active-cancel.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    provider = PiWritingProvider(FakePiClient({"unused": True}))

    def cancel_during_outline(**kwargs) -> None:
        with transaction() as db:
            row = writing_repo.operation_by_id(db, kwargs["operation_id"])
            assert row is not None
            row.status = "cancelling"
        raise PiAgentCancelledError("writing operation cancelled")

    monkeypatch.setattr(provider, "_execute_outline", cancel_during_outline)
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "取消中的写作", "space_ids": []},
            ).json()
            operation = client.post(
                f"/writing/projects/{project['id']}/outline-runs",
                headers=headers,
            ).json()

            assert WritingDispatcher(
                worker_id="cancel-worker", pi_provider=provider
            ).run_once()

            projected = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            assert projected["status"] == "cancelled"
            with database.SessionLocal() as db:
                outbox = db.query(WritingDispatchOutbox).one()
                assert outbox.status == "cancelled"
                assert outbox.lease_owner is None
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_outline_dispatch_keeps_candidate_pending_by_default(
    tmp_path: Path, monkeypatch
):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-outline.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", False)
    fake = FakePiClient(
        {
            "chapters": [
                {"title": "经营概览", "objective": "概述经营表现"},
                {"title": "后续计划", "objective": "列出后续行动"},
            ],
            "verification": {"passed": True, "findings": []},
        },
        cost="0.42",
    )
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid",
            "evidence": [{"id": "writing-evidence:ver_1"}],
            "knowledge_gaps": [],
        },
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "Pi Demo", "space_ids": []},
            ).json()
            operation = client.post(
                f"/writing/projects/{project['id']}/outline-runs",
                headers={**headers, "Idempotency-Key": "pi-outline-1"},
            ).json()
            dispatcher = WritingDispatcher(
                worker_id="pi-worker",
                pi_provider=PiWritingProvider(fake),
            )
            assert dispatcher.run_once()
            projected = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            refreshed = client.get(
                f"/writing/projects/{project['id']}", headers=headers
            ).json()
            candidates = client.get(
                f"/writing/projects/{project['id']}/outline-candidates",
                headers=headers,
            ).json()
            assert projected["status"] == "completed"
            assert projected["usage"]["provider"] == "pi_agent_chat"
            assert projected["result"]["auto_published"] is False
            assert refreshed["outline"]["chapters"] == []
            assert candidates[0]["status"] == "pending"
            assert [item["title"] for item in candidates[0]["payload"]["chapters"]] == [
                "经营概览",
                "后续计划",
            ]
            # A writing call owns a private Pi session: it must never inherit the
            # shared per-user chat session (``chat-<user>``), whose history would
            # let the model cite evidence this call never supplied.
            assert len(fake.session_ids) == 1
            assert fake.session_ids[0] == (f"pi-writing-{operation['id']}-outline-a1")
            assert fake.idempotency_keys == [
                f"writing:{operation['id']}:outline:generate"
            ]
            with database.SessionLocal() as db:
                stored = db.get(WritingProject, project["id"])
                assert stored is not None
                assert stored.pi_agent_session_id == fake.session_ids[0]
                assert stored.pi_lease_owner is None
                assert db.query(AgentRunBinding).count() == 0
                assert db.query(WritingToolReceipt).count() == 1
                assert db.query(WritingDispatchOutbox).one().status == "dispatched"
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_outline_auto_publish_when_enabled(tmp_path: Path, monkeypatch):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-outline-auto.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", True)
    fake = FakePiClient(
        {
            "chapters": [
                {"title": "经营概览", "objective": "概述经营表现"},
                {"title": "后续计划", "objective": "列出后续行动"},
            ],
            "verification": {"passed": True, "findings": []},
        }
    )
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid",
            "evidence": [],
            "knowledge_gaps": [],
        },
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "Pi Auto", "space_ids": []},
            ).json()
            operation = client.post(
                f"/writing/projects/{project['id']}/outline-runs",
                headers={**headers, "Idempotency-Key": "pi-outline-auto"},
            ).json()
            assert WritingDispatcher(
                worker_id="pi-auto-worker",
                pi_provider=PiWritingProvider(fake),
            ).run_once()
            projected = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            refreshed = client.get(
                f"/writing/projects/{project['id']}", headers=headers
            ).json()
            candidates = client.get(
                f"/writing/projects/{project['id']}/outline-candidates",
                headers=headers,
            ).json()
            assert projected["result"]["auto_published"] is True
            assert [item["title"] for item in refreshed["outline"]["chapters"]] == [
                "经营概览",
                "后续计划",
            ]
            assert candidates[0]["status"] == "published"
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_document_dispatch_isolates_chapter_sessions(tmp_path: Path, monkeypatch):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-document.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", False)
    citation_id = "writing-evidence:ver_1:0-40"
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid_chunk",
            "evidence": [
                {
                    "id": citation_id,
                    "excerpt": "本季度收入320万元。需关注回款节奏。",
                }
            ],
            "knowledge_gaps": [],
            "retrieval": {"injection_flags": []},
        },
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "Pi Document Demo", "space_ids": []},
            ).json()
            project = client.put(
                f"/writing/projects/{project['id']}/outline",
                headers=headers,
                json={
                    "chapters": [
                        {"title": "经营摘要"},
                        {"title": "风险提示"},
                    ]
                },
            ).json()
            chapter_a = project["outline"]["chapters"][0]["id"]
            chapter_b = project["outline"]["chapters"][1]["id"]
            client.post(
                f"/writing/projects/{project['id']}/outline:confirm",
                headers=headers,
            )
            with database.SessionLocal() as db:
                stored = db.get(WritingProject, project["id"])
                assert stored is not None
                # A session left over from an earlier stage must not be resumed:
                # its history holds evidence ids this run never supplied.
                stored.pi_agent_session_id = "pi-writing-wop_previous-outline-a1"
                stored.pi_session_stage = "outline"
                db.commit()

            fake = FakePiClient(
                payloads=[
                    {
                        "sections": [
                            {
                                "chapter_id": chapter_a,
                                "title": "经营摘要",
                                "markdown": "本季度收入320万元。",
                                "citations": [citation_id],
                                "verification": {
                                    "passed": True,
                                    "findings": [],
                                },
                            }
                        ],
                        "verification": {"passed": True, "findings": []},
                    },
                    PiAgentError("repair unavailable", code="rate_limited"),
                    {
                        "sections": [
                            {
                                "chapter_id": chapter_b,
                                "title": "风险提示",
                                "markdown": "| 风险 | 措施 |\n| --- | --- |\n| 回款 | 关注回款节奏 |",
                                "citations": [citation_id],
                                "verification": {
                                    "passed": True,
                                    "findings": [],
                                },
                            }
                        ],
                        "verification": {"passed": True, "findings": []},
                    },
                ]
            )
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "pi-document-1"},
            ).json()
            assert WritingDispatcher(
                worker_id="pi-document-worker",
                pi_provider=PiWritingProvider(fake),
            ).run_once()
            projected = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            refreshed = client.get(
                f"/writing/projects/{project['id']}", headers=headers
            ).json()
            sections = client.get(
                f"/writing/projects/{project['id']}/section-candidates",
                headers=headers,
            ).json()
            assert projected["status"] == "completed"
            assert projected["result"]["progress"] == {"done": 2, "total": 2}
            assert projected["result"]["auto_published"] is True
            assert fake.session_ids == [
                f"pi-writing-{operation['id']}-document-{chapter_a}-a1",
                f"pi-writing-{operation['id']}-document-repair-{chapter_a}-a1",
                f"pi-writing-{operation['id']}-document-{chapter_b}-a1",
            ]
            by_id = {
                item["id"]: item["content"] for item in refreshed["outline"]["chapters"]
            }
            assert by_id[chapter_a] == "本季度收入320万元。"
            assert (
                by_id[chapter_b]
                == "| 风险 | 措施 |\n| --- | --- |\n| 回款 | 关注回款节奏 |"
            )
            section_rows = [item for item in sections if item["kind"] == "section"]
            assert {item["status"] for item in section_rows} == {"published"}
            by_chapter = {
                item["chapter_id"]: item["payload"]["markdown"] for item in section_rows
            }
            assert by_chapter[chapter_a] == by_id[chapter_a]
            assert by_chapter[chapter_b] == by_id[chapter_b]
            assert all(row["payload"]["citations"] == [] for row in section_rows)
            assert all(
                row["payload"]["retrieved_citation_ids"] == [citation_id]
                for row in section_rows
            )
            with database.SessionLocal() as db:
                kinds = {item.kind for item in db.query(WritingCandidate).all()}
                assert kinds == {"section", "verification"}
                assert db.query(WritingToolReceipt).count() == 2
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_document_resumes_missing_chapters_and_binds_single_section_to_target(
    tmp_path: Path, monkeypatch
):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-document-resume.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", True)
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid",
            "evidence": [],
            "knowledge_gaps": [],
            "retrieval": {"injection_flags": []},
        },
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "Pi Resume Demo", "space_ids": []},
            ).json()
            project = client.put(
                f"/writing/projects/{project['id']}/outline",
                headers=headers,
                json={
                    "chapters": [
                        {"title": "已经完成"},
                        {"title": "等待续写"},
                    ]
                },
            ).json()
            chapter_a = project["outline"]["chapters"][0]["id"]
            chapter_b = project["outline"]["chapters"][1]["id"]
            client.post(
                f"/writing/projects/{project['id']}/outline:confirm",
                headers=headers,
            )
            with database.SessionLocal() as db:
                existing = writing_repo.chapter_for_project(
                    db, project["id"], chapter_a
                )
                assert existing is not None
                existing.content = "保留的第一章正文"
                db.commit()

            fake = FakePiClient(
                payload={
                    "sections": [
                        {
                            "chapter_id": f"{chapter_b}-typo",
                            "title": "等待续写",
                            "markdown": "新生成的第二章正文",
                            "citations": [],
                            "verification": {"passed": True, "findings": []},
                        }
                    ]
                }
            )
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "pi-resume-1"},
            ).json()
            assert operation["result"]["target_chapter_ids"] == [chapter_b]
            targets_before = client.get(
                f"/writing/operations/{operation['id']}/previews", headers=headers
            ).json()
            assert targets_before["target_chapter_ids"] == [chapter_b]
            assert WritingDispatcher(
                worker_id="pi-resume-worker",
                pi_provider=PiWritingProvider(fake),
            ).run_once()

            projected = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            refreshed = client.get(
                f"/writing/projects/{project['id']}", headers=headers
            ).json()
            assert projected["status"] == "completed"
            assert projected["result"]["progress"] == {"done": 2, "total": 2}
            assert projected["result"]["applied_chapter_ids"] == [
                chapter_a,
                chapter_b,
            ]
            assert len(fake.prompts) == 1
            by_id = {
                item["id"]: item["content"] for item in refreshed["outline"]["chapters"]
            }
            assert by_id[chapter_a] == "保留的第一章正文"
            assert by_id[chapter_b] == "新生成的第二章正文"
            targets_after = client.get(
                f"/writing/operations/{operation['id']}/previews", headers=headers
            ).json()
            assert targets_after["target_chapter_ids"] == [chapter_b]
            replay = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "pi-resume-1"},
            ).json()
            assert replay["id"] == operation["id"]
            assert replay["result"]["target_chapter_ids"] == [chapter_b]
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_document_regenerates_only_requested_chapter(tmp_path: Path, monkeypatch):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-document-single-chapter.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", True)
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid",
            "evidence": [],
            "knowledge_gaps": [],
            "retrieval": {"injection_flags": []},
        },
    )
    monkeypatch.setattr(
        "app.services.pi_writing_provider._support_check_for_section",
        lambda *args, **kwargs: {"passed": True, "findings": []},
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "Pi Targeted Demo", "space_ids": []},
            ).json()
            project = client.put(
                f"/writing/projects/{project['id']}/outline",
                headers=headers,
                json={
                    "chapters": [
                        {"title": "保留章节"},
                        {"title": "重新生成章节"},
                    ]
                },
            ).json()
            chapter_a = project["outline"]["chapters"][0]["id"]
            chapter_b = project["outline"]["chapters"][1]["id"]
            client.post(
                f"/writing/projects/{project['id']}/outline:confirm",
                headers=headers,
            )
            with database.SessionLocal() as db:
                first = writing_repo.chapter_for_project(db, project["id"], chapter_a)
                second = writing_repo.chapter_for_project(db, project["id"], chapter_b)
                assert first is not None and second is not None
                first.content = "必须保留的第一章正文"
                second.content = "即将被替换的第二章正文"
                db.commit()

            missing = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "pi-target-missing"},
                json={"chapter_ids": ["wch-missing"]},
            )
            assert missing.status_code == 404
            assert missing.json()["detail"] == "指定章节不存在"

            fake = FakePiClient(
                payload={
                    "sections": [
                        {
                            "chapter_id": chapter_b,
                            "title": "重新生成章节",
                            "markdown": "重新生成后的第二章正文",
                            "citations": [],
                            "verification": {"passed": True, "findings": []},
                        }
                    ]
                }
            )
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "pi-target-1"},
                json={"chapter_ids": [chapter_b]},
            ).json()
            assert WritingDispatcher(
                worker_id="pi-target-worker",
                pi_provider=PiWritingProvider(fake),
            ).run_once()

            projected = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            refreshed = client.get(
                f"/writing/projects/{project['id']}", headers=headers
            ).json()
            assert projected["status"] == "completed"
            assert projected["result"]["progress"] == {"done": 1, "total": 1}
            assert projected["result"]["applied_chapter_ids"] == [chapter_b]
            assert projected["result"]["auto_published"] is False
            assert len(fake.prompts) == 1
            by_id = {
                item["id"]: item["content"] for item in refreshed["outline"]["chapters"]
            }
            assert by_id[chapter_a] == "必须保留的第一章正文"
            assert by_id[chapter_b] == "即将被替换的第二章正文"

            candidates = client.get(
                f"/writing/projects/{project['id']}/section-candidates",
                headers=headers,
            ).json()
            candidate = next(
                item for item in candidates if item["chapter_id"] == chapter_b
            )
            assert candidate["status"] == "pending"
            assert candidate["payload"]["markdown"] == "重新生成后的第二章正文"
            published = client.post(
                f"/writing/projects/{project['id']}/section-candidates/{candidate['id']}:publish",
                headers=headers,
            )
            assert published.status_code == 200

            adopted = client.get(
                f"/writing/projects/{project['id']}", headers=headers
            ).json()
            adopted_by_id = {
                item["id"]: item["content"] for item in adopted["outline"]["chapters"]
            }
            assert adopted_by_id[chapter_a] == "必须保留的第一章正文"
            assert adopted_by_id[chapter_b] == "重新生成后的第二章正文"
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_document_auto_publishes_first_empty_chapter_when_support_check_fails(
    tmp_path: Path, monkeypatch
):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-document-first-draft.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", False)
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid",
            "evidence": [],
            "knowledge_gaps": [],
            "retrieval": {"injection_flags": []},
        },
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "慧云科技的调查", "space_ids": []},
            ).json()
            project = client.put(
                f"/writing/projects/{project['id']}/outline",
                headers=headers,
                json={"chapters": [{"title": "公司基本情况"}]},
            ).json()
            chapter_id = project["outline"]["chapters"][0]["id"]
            client.post(
                f"/writing/projects/{project['id']}/outline:confirm",
                headers=headers,
            )
            fake = FakePiClient(
                payload={
                    "sections": [
                        {
                            "chapter_id": chapter_id,
                            "title": "公司基本情况",
                            "markdown": "慧云科技是一家聚焦 AIOps 的科技公司。",
                            "citations": [],
                            "verification": {"passed": True, "findings": []},
                        }
                    ],
                    "verification": {"passed": True, "findings": []},
                }
            )
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "pi-first-draft-1"},
            ).json()
            assert WritingDispatcher(
                worker_id="pi-first-draft-worker",
                pi_provider=PiWritingProvider(fake),
            ).run_once()
            projected = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            refreshed = client.get(
                f"/writing/projects/{project['id']}", headers=headers
            ).json()
            sections = client.get(
                f"/writing/projects/{project['id']}/section-candidates",
                headers=headers,
            ).json()
            assert projected["status"] == "completed"
            assert projected["result"]["auto_published"] is True
            assert refreshed["outline"]["chapters"][0]["content"].startswith("慧云科技")
            assert sections[0]["status"] == "published"
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_document_keeps_first_draft_pending_when_injection_flags_present(
    tmp_path: Path, monkeypatch
):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-document-injection.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", False)
    citation_id = "writing-evidence:ver_1:0-20"
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid",
            "evidence": [{"id": citation_id, "excerpt": "慧云科技是一家科技公司。"}],
            "knowledge_gaps": [],
            "retrieval": {
                "injection_flags": [
                    {"document_version_id": "v1", "match": "ignore previous"}
                ]
            },
        },
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "慧云科技的调查", "space_ids": []},
            ).json()
            project = client.put(
                f"/writing/projects/{project['id']}/outline",
                headers=headers,
                json={"chapters": [{"title": "公司基本情况"}]},
            ).json()
            chapter_id = project["outline"]["chapters"][0]["id"]
            client.post(
                f"/writing/projects/{project['id']}/outline:confirm",
                headers=headers,
            )
            fake = FakePiClient(
                payload={
                    "sections": [
                        {
                            "chapter_id": chapter_id,
                            "title": "公司基本情况",
                            "markdown": "慧云科技是一家科技公司。",
                            "citations": [citation_id],
                            "verification": {"passed": True, "findings": []},
                        }
                    ],
                    "verification": {"passed": True, "findings": []},
                }
            )
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "pi-injection-1"},
            ).json()
            assert WritingDispatcher(
                worker_id="pi-injection-worker",
                pi_provider=PiWritingProvider(fake),
            ).run_once()
            projected = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            refreshed = client.get(
                f"/writing/projects/{project['id']}", headers=headers
            ).json()
            sections = client.get(
                f"/writing/projects/{project['id']}/section-candidates",
                headers=headers,
            ).json()
            assert projected["status"] == "completed"
            assert projected["result"]["auto_published"] is False
            assert refreshed["outline"]["chapters"][0]["content"] == ""
            assert sections[0]["status"] == "pending"
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_document_retry_skips_applied_chapters(tmp_path: Path, monkeypatch):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-document-retry.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", True)
    citation_id = "writing-evidence:ver_1:0-40"
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid_chunk",
            "evidence": [{"id": citation_id, "excerpt": "第一章正文。第二章正文。"}],
            "knowledge_gaps": [],
            "retrieval": {"injection_flags": []},
        },
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "Pi Retry Demo", "space_ids": []},
            ).json()
            project = client.put(
                f"/writing/projects/{project['id']}/outline",
                headers=headers,
                json={
                    "chapters": [
                        {"title": "第一章"},
                        {"title": "第二章"},
                    ]
                },
            ).json()
            chapter_a = project["outline"]["chapters"][0]["id"]
            chapter_b = project["outline"]["chapters"][1]["id"]
            client.post(
                f"/writing/projects/{project['id']}/outline:confirm",
                headers=headers,
            )
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "pi-retry-1"},
            ).json()

            class FlakyClient(FakePiClient):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self._failed_once = False

                def chat(
                    self,
                    message: str,
                    *,
                    session_id: str | None = None,
                    idempotency_key: str | None = None,
                ):
                    result = super().chat(
                        message,
                        session_id=session_id,
                        idempotency_key=idempotency_key,
                    )
                    if len(self.prompts) >= 2 and not self._failed_once:
                        self._failed_once = True
                        error = PiAgentError("boom after first chapter")
                        error.token_usage = result.token_usage
                        raise error
                    return result

            flaky = FlakyClient(
                payloads=[
                    {
                        "sections": [
                            {
                                "chapter_id": chapter_a,
                                "title": "第一章",
                                "markdown": "第一章正文",
                                "citations": [citation_id],
                                "verification": {"passed": True, "findings": []},
                            }
                        ]
                    },
                    {
                        "sections": [
                            {
                                "chapter_id": chapter_b,
                                "title": "第二章",
                                "markdown": "第二章正文",
                                "citations": [citation_id],
                                "verification": {"passed": True, "findings": []},
                            }
                        ]
                    },
                    {
                        "sections": [
                            {
                                "chapter_id": chapter_b,
                                "title": "第二章",
                                "markdown": "第二章正文",
                                "citations": [citation_id],
                                "verification": {"passed": True, "findings": []},
                            }
                        ]
                    },
                ]
            )
            dispatcher = WritingDispatcher(
                worker_id="pi-retry-worker",
                pi_provider=PiWritingProvider(flaky),
                max_attempts=3,
            )
            assert dispatcher.run_once()
            mid = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            assert mid["status"] == "queued"
            assert mid["result"]["applied_chapter_ids"] == [chapter_a]
            assert mid["usage"]["total_tokens"] == 240
            partial_project = client.get(
                f"/writing/projects/{project['id']}", headers=headers
            ).json()
            applied_content = next(
                item["content"]
                for item in partial_project["outline"]["chapters"]
                if item["id"] == chapter_a
            )
            with database.SessionLocal() as db:
                outbox = db.query(WritingDispatchOutbox).one()
                assert outbox.status == "retry"
                outbox.next_attempt_at = outbox.created_at
                stored = db.get(WritingProject, project["id"])
                assert stored is not None
                assert stored.pi_lease_owner is None
                db.commit()

            assert dispatcher.run_once()
            done = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            refreshed = client.get(
                f"/writing/projects/{project['id']}", headers=headers
            ).json()
            assert done["status"] == "completed"
            assert done["result"]["applied_chapter_ids"] == [chapter_a, chapter_b]
            # chapter A once, chapter B failed once then succeeded on retry.
            assert len(flaky.prompts) == 3
            assert done["usage"]["total_tokens"] == 360
            assert chapter_b in flaky.prompts[1] and chapter_b in flaky.prompts[2]
            by_id = {
                item["id"]: item["content"] for item in refreshed["outline"]["chapters"]
            }
            assert by_id[chapter_a] == applied_content
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_rejects_citations_outside_supplied_evidence(tmp_path: Path, monkeypatch):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-untrusted-citation.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid",
            "evidence": [{"id": "writing-evidence:ver_1"}],
            "knowledge_gaps": [],
        },
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "Pi Citation Demo", "space_ids": []},
            ).json()
            project = client.put(
                f"/writing/projects/{project['id']}/outline",
                headers=headers,
                json={"chapters": [{"title": "经营摘要"}]},
            ).json()
            chapter_id = project["outline"]["chapters"][0]["id"]
            client.post(
                f"/writing/projects/{project['id']}/outline:confirm",
                headers=headers,
            )
            fake = FakePiClient(
                {
                    "sections": [
                        {
                            "chapter_id": chapter_id,
                            "title": "经营摘要",
                            "markdown": "无来源正文",
                            "citations": ["writing-evidence:unknown"],
                            "verification": {"passed": True, "findings": []},
                        }
                    ],
                    "verification": {"passed": True, "findings": []},
                }
            )
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "pi-citation-1"},
            ).json()

            # The dispatcher's own budget is 5 attempts, but a rejected answer is
            # deterministic: it earns exactly one regeneration, so a 3-minute call
            # cannot turn into a 15-minute wait before failing.
            dispatcher = WritingDispatcher(
                worker_id="pi-citation-worker",
                pi_provider=PiWritingProvider(fake),
            )
            assert dispatcher.run_once()
            with database.SessionLocal() as db:
                outbox = writing_repo.dispatch_for_operation(db, operation["id"])
                assert outbox is not None
                assert outbox.attempt == 1
                assert outbox.status == "retry"
                outbox.status = "claimed"
                outbox.lease_owner = "pi-citation-worker"
                outbox.lease_expires_at = writing_repo.now()
                outbox.next_attempt_at = writing_repo.now()
                db.commit()
            assert dispatcher.run_once()

            projected = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            candidates = client.get(
                f"/writing/projects/{project['id']}/section-candidates",
                headers=headers,
            ).json()
            assert projected["status"] == "failed"
            assert projected["error_code"] == "writing_dispatch_exhausted"
            assert projected["attempt"] == 2
            assert len(fake.prompts) == 2
            # The rejected payload must not survive as a reusable receipt, and the
            # user must read why instead of seeing the exception name.
            assert "引用" in projected["error_message"]
            assert projected["error_message"] != "PiAgentError"
            assert candidates == []
            with database.SessionLocal() as db:
                assert db.query(WritingToolReceipt).count() == 0
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_document_contract_cap_survives_a_later_transient_failure(
    tmp_path: Path, monkeypatch
):
    """A rejected answer bounds the whole operation, not just its own attempt."""
    from app.services.pi_agent_client import PiAgentError
    from app.services.writing_dispatcher import _attempt_limit

    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-citation-mixed.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", True)
    citation_id = "writing-evidence:ver_1:0-40"
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid_chunk",
            "evidence": [{"id": citation_id, "excerpt": "本季度收入320万元。"}],
            "knowledge_gaps": [],
            "retrieval": {"injection_flags": []},
        },
    )
    # A hint-less transient failure on top of a recorded contract violation still
    # keeps the two-attempt ceiling; a clean operation keeps the full budget.
    assert _attempt_limit(PiAgentError("boom"), default=5) == 5
    assert (
        _attempt_limit(
            PiAgentError("boom"), default=5, previous_code="citations_outside_evidence"
        )
        == 2
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "Pi Mixed Failure", "space_ids": []},
            ).json()
            project = client.put(
                f"/writing/projects/{project['id']}/outline",
                headers=headers,
                json={"chapters": [{"title": "经营摘要"}]},
            ).json()
            chapter_id = project["outline"]["chapters"][0]["id"]
            client.post(
                f"/writing/projects/{project['id']}/outline:confirm",
                headers=headers,
            )
            fake = FakePiClient(
                payloads=[
                    {
                        "sections": [
                            {
                                "chapter_id": chapter_id,
                                "title": "经营摘要",
                                "markdown": "本季度收入320万元。",
                                "citations": ["writing-evidence:ver_previous:0-4000"],
                                "verification": {"passed": True, "findings": []},
                            }
                        ],
                        "verification": {"passed": True, "findings": []},
                    },
                    # Second attempt dies in the provider, with no cap of its own.
                    PiAgentError("Pi RPC exited before agent_settled (exit=1)"),
                ]
            )
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "pi-mixed-1"},
            ).json()
            dispatcher = WritingDispatcher(
                worker_id="pi-mixed-worker",
                pi_provider=PiWritingProvider(fake),
            )
            assert dispatcher.run_once()
            with database.SessionLocal() as db:
                outbox = writing_repo.dispatch_for_operation(db, operation["id"])
                assert outbox is not None
                assert outbox.status == "retry"
                stored = writing_repo.operation_by_id(db, operation["id"])
                assert stored is not None
                assert stored.error_code == "citations_outside_evidence"
                outbox.status = "claimed"
                outbox.lease_owner = "pi-mixed-worker"
                outbox.lease_expires_at = writing_repo.now()
                outbox.next_attempt_at = writing_repo.now()
                db.commit()
            assert dispatcher.run_once()

            projected = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            assert projected["status"] == "failed"
            assert projected["attempt"] == 2
            assert len(fake.prompts) == 2
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_document_retry_regenerates_after_rejected_citation(
    tmp_path: Path, monkeypatch
):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-citation-retry.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", True)
    citation_id = "writing-evidence:ver_1:0-40"
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid_chunk",
            "evidence": [{"id": citation_id, "excerpt": "本季度收入320万元。"}],
            "knowledge_gaps": [],
            "retrieval": {"injection_flags": []},
        },
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "Pi Citation Retry", "space_ids": []},
            ).json()
            project = client.put(
                f"/writing/projects/{project['id']}/outline",
                headers=headers,
                json={"chapters": [{"title": "经营摘要"}]},
            ).json()
            chapter_id = project["outline"]["chapters"][0]["id"]
            client.post(
                f"/writing/projects/{project['id']}/outline:confirm",
                headers=headers,
            )
            fake = FakePiClient(
                payloads=[
                    {
                        # First attempt cites an id this call never supplied.
                        "sections": [
                            {
                                "chapter_id": chapter_id,
                                "title": "经营摘要",
                                "markdown": "本季度收入320万元。",
                                "citations": ["writing-evidence:ver_previous:0-4000"],
                                "verification": {"passed": True, "findings": []},
                            }
                        ],
                        "verification": {"passed": True, "findings": []},
                    },
                    {
                        "sections": [
                            {
                                "chapter_id": chapter_id,
                                "title": "经营摘要",
                                "markdown": "本季度收入320万元。",
                                "citations": [citation_id],
                                "verification": {"passed": True, "findings": []},
                            }
                        ],
                        "verification": {"passed": True, "findings": []},
                    },
                    PiAgentError("repair unavailable", code="rate_limited"),
                ]
            )
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "pi-citation-retry-1"},
            ).json()
            dispatcher = WritingDispatcher(
                worker_id="pi-citation-retry-worker",
                pi_provider=PiWritingProvider(fake),
                max_attempts=2,
            )
            assert dispatcher.run_once()
            # Retry backoff is not what this case exercises; make it due now.
            with database.SessionLocal() as db:
                outbox = writing_repo.dispatch_for_operation(db, operation["id"])
                assert outbox is not None
                assert outbox.attempt == 1
                outbox.status = "claimed"
                outbox.lease_owner = "pi-citation-retry-worker"
                outbox.lease_expires_at = writing_repo.now()
                outbox.next_attempt_at = writing_repo.now()
                db.commit()
            assert dispatcher.run_once()

            projected = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            refreshed = client.get(
                f"/writing/projects/{project['id']}", headers=headers
            ).json()
            # The rejected payload is not replayable: the retry asks the model
            # again in a fresh session instead of failing on the cached output.
            assert len(fake.prompts) == 3
            assert fake.prompts[0] == fake.prompts[1]
            assert fake.session_ids == [
                f"pi-writing-{operation['id']}-document-{chapter_id}-a1",
                f"pi-writing-{operation['id']}-document-{chapter_id}-a2",
                f"pi-writing-{operation['id']}-document-repair-{chapter_id}-a1",
            ]
            assert projected["status"] == "completed"
            assert {
                item["id"]: item["content"] for item in refreshed["outline"]["chapters"]
            }[chapter_id] == "本季度收入320万元。"
            with database.SessionLocal() as db:
                receipt = db.query(WritingToolReceipt).one()
                assert json.loads(receipt.result_json)["allowed_citation_ids"] == [
                    citation_id
                ]
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_project_lease_serializes_same_project_operations(
    tmp_path: Path, monkeypatch
):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-lease.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", False)
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid",
            "evidence": [],
            "knowledge_gaps": [],
        },
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "Pi Lease", "space_ids": []},
            ).json()
            first = client.post(
                f"/writing/projects/{project['id']}/outline-runs",
                headers={**headers, "Idempotency-Key": "lease-1"},
            ).json()
            second = client.post(
                f"/writing/projects/{project['id']}/outline-runs",
                headers={**headers, "Idempotency-Key": "lease-2"},
            ).json()
            with database.SessionLocal() as db:
                from app.repositories import writing as writing_repo

                claimed = writing_repo.claim_dispatch(
                    db,
                    worker_id="lease-worker-a",
                    lease_seconds=120,
                    serialize_by_project=True,
                )
                assert claimed is not None
                assert claimed.operation_id == first["id"]
                blocked = writing_repo.claim_dispatch(
                    db,
                    worker_id="lease-worker-b",
                    lease_seconds=120,
                    serialize_by_project=True,
                )
                assert blocked is None
                second_outbox = writing_repo.dispatch_for_operation(db, second["id"])
                assert second_outbox is not None
                assert second_outbox.status in {"pending", "retry"}
                writing_repo.release_project_pi_lease(
                    db, operation_id=first["id"], worker_id="lease-worker-a"
                )
                db.commit()
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_writing_scope_overage_warns_and_continues(
    tmp_path: Path, monkeypatch
):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-token-budget.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid",
            "evidence": [],
            "knowledge_gaps": [],
        },
    )
    fake = FakePiClient(
        {"chapters": [{"title": "继续生成"}], "verification": {"passed": True}}
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "Pi Token Budget", "space_ids": []},
            ).json()
            operation = client.post(
                f"/writing/projects/{project['id']}/outline-runs",
                headers={**headers, "Idempotency-Key": "token-budget-1"},
            ).json()
            with database.SessionLocal() as db:
                stored = writing_repo.operation_by_id(db, operation["id"])
                assert stored is not None
                stored.usage = json.dumps(
                    _usage_ledger(
                        total_tokens=3_000_000,
                        scopes={"outline": 3_000_000},
                    )
                )
                db.commit()

            assert WritingDispatcher(
                worker_id="token-budget-worker",
                pi_provider=PiWritingProvider(fake),
                max_attempts=3,
            ).run_once()
            status = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            assert status["status"] == "completed"
            assert status["attempt"] == 1
            assert status["usage"]["total_tokens"] == 3_000_120
            assert status["usage"]["token_warning"] is True
            assert status["usage"]["token_warning_threshold"] == 3_000_000
            assert status["usage"]["token_warning_scopes"] == ["outline"]
            assert len(fake.prompts) == 1
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_writing_allows_new_scope_after_previous_scope_reaches_budget(
    tmp_path: Path, monkeypatch
):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-token-budget-next-scope.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid",
            "evidence": [],
            "knowledge_gaps": [],
        },
    )
    fake = FakePiClient(
        {"chapters": [{"title": "允许继续的提纲"}], "verification": {"passed": True}}
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "Pi Scope Budget", "space_ids": []},
            ).json()
            operation = client.post(
                f"/writing/projects/{project['id']}/outline-runs",
                headers={**headers, "Idempotency-Key": "token-budget-next-scope"},
            ).json()
            with database.SessionLocal() as db:
                stored = writing_repo.operation_by_id(db, operation["id"])
                assert stored is not None
                stored.usage = json.dumps(
                    _usage_ledger(
                        total_tokens=3_000_000,
                        scopes={"document:wch_finished": 3_000_000},
                    )
                )
                db.commit()

            assert WritingDispatcher(
                worker_id="token-budget-next-scope-worker",
                pi_provider=PiWritingProvider(fake),
            ).run_once()
            status = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            assert status["status"] == "completed"
            assert status["usage"]["total_tokens"] == 3_000_120
            assert status["usage"]["budget_tokens_by_scope"] == {
                "document:wch_finished": 3_000_000,
                "outline": 120,
            }
            assert status["usage"].get("token_warning", False) is False
            assert len(fake.prompts) == 1
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_writing_corrupted_usage_fails_closed_before_provider_call(
    tmp_path: Path, monkeypatch
):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-token-budget-corrupted.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    fake = FakePiClient({"chapters": [{"title": "不应调用"}]})
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "Pi Broken Usage", "space_ids": []},
            ).json()
            operation = client.post(
                f"/writing/projects/{project['id']}/outline-runs",
                headers={**headers, "Idempotency-Key": "token-budget-corrupted"},
            ).json()
            with database.SessionLocal() as db:
                stored = writing_repo.operation_by_id(db, operation["id"])
                assert stored is not None
                stored.usage = "{broken"
                db.commit()

            assert WritingDispatcher(
                worker_id="token-budget-corrupted-worker",
                pi_provider=PiWritingProvider(fake),
                max_attempts=3,
            ).run_once()
            status = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            assert status["status"] == "failed"
            assert status["error_code"] == "writing_dispatch_exhausted"
            assert status["attempt"] == 1
            assert "用量账本不可用" in status["error_message"]
            assert fake.prompts == []
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_writing_scope_overage_keeps_chapter_preview_running(
    tmp_path: Path, monkeypatch
):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-token-budget-preview.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid",
            "evidence": [],
            "knowledge_gaps": [],
        },
    )
    fake = FakePiClient({"sections": []})
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "Pi Preview Budget", "space_ids": []},
            ).json()
            project = client.put(
                f"/writing/projects/{project['id']}/outline",
                headers=headers,
                json={"chapters": [{"title": "预算已耗尽章节"}]},
            ).json()
            chapter_id = project["outline"]["chapters"][0]["id"]
            fake.payload = {
                "sections": [
                    {
                        "chapter_id": chapter_id,
                        "title": "预算提醒章节",
                        "markdown": "这是超过建议用量后继续完成的章节正文。",
                        "citations": [],
                        "verification": {"passed": True, "findings": []},
                    }
                ],
                "verification": {"passed": True, "findings": []},
            }
            client.post(
                f"/writing/projects/{project['id']}/outline:confirm",
                headers=headers,
            )
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={
                    **headers,
                    "Idempotency-Key": "token-budget-preview-failed",
                },
            ).json()
            scope = f"document:{chapter_id}"
            with database.SessionLocal() as db:
                stored = writing_repo.operation_by_id(db, operation["id"])
                assert stored is not None
                stored.usage = json.dumps(
                    _usage_ledger(
                        total_tokens=3_000_000,
                        scopes={scope: 3_000_000},
                    )
                )
                db.commit()

            assert WritingDispatcher(
                worker_id="token-budget-preview-worker",
                pi_provider=PiWritingProvider(fake),
                max_attempts=3,
            ).run_once()
            status = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            previews = _preview_snapshots(operation["id"])
            assert status["status"] == "completed"
            assert status["usage"]["token_warning"] is True
            assert status["usage"]["token_warning_scopes"] == [scope]
            assert previews
            assert previews[-1]["chapter_id"] == chapter_id
            assert previews[-1]["status"] == "saved"
            assert previews[-1]["error_message"] is None
            assert len(fake.prompts) == 1
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_chat_receipt_prevents_duplicate_provider_calls(tmp_path: Path, monkeypatch):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-idempotent.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", False)
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid",
            "evidence": [],
            "knowledge_gaps": [],
        },
    )
    fake = FakePiClient(
        {
            "chapters": [{"title": "唯一章节", "objective": "目标"}],
            "verification": {"passed": True, "findings": []},
        }
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "Pi Idempotent", "space_ids": []},
            ).json()
            operation = client.post(
                f"/writing/projects/{project['id']}/outline-runs",
                headers={**headers, "Idempotency-Key": "pi-idemp-1"},
            ).json()
            provider = PiWritingProvider(fake)
            assert WritingDispatcher(
                worker_id="idemp-worker",
                pi_provider=provider,
            ).run_once()
            assert len(fake.prompts) == 1
            candidates = client.get(
                f"/writing/projects/{project['id']}/outline-candidates",
                headers=headers,
            ).json()
            assert len(candidates) == 1
            usage_before_replay = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()["usage"]

            # Simulate a replay after Pi already succeeded and receipt exists.
            outbox_id = ""
            with database.SessionLocal() as db:
                from app.repositories import writing as writing_repo

                outbox = writing_repo.dispatch_for_operation(db, operation["id"])
                assert outbox is not None
                outbox.status = "claimed"
                outbox.lease_owner = "idemp-worker"
                outbox.lease_expires_at = writing_repo.now()
                outbox.attempt = 1
                outbox_id = outbox.id
                op = writing_repo.operation_by_id(db, operation["id"])
                assert op is not None
                op.status = "running"
                op.completed_at = None
                writing_repo.try_acquire_project_pi_lease(
                    db,
                    project["id"],
                    worker_id="idemp-worker",
                    operation_id=operation["id"],
                    lease_seconds=120,
                )
                db.commit()

            def unexpected_retrieval(*args, **kwargs):
                raise AssertionError(
                    "Receipt replay must not run a current evidence search"
                )

            monkeypatch.setattr(
                "app.services.pi_writing_provider.writing_evidence.search",
                unexpected_retrieval,
            )
            provider.execute(
                operation_id=operation["id"],
                outbox_id=outbox_id,
                worker_id="idemp-worker",
            )
            assert len(fake.prompts) == 1
            usage_after_replay = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()["usage"]
            assert usage_after_replay == usage_before_replay
            candidates = client.get(
                f"/writing/projects/{project['id']}/outline-candidates",
                headers=headers,
            ).json()
            assert len(candidates) == 1
            assert candidates[0]["status"] == "pending"
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


@pytest.mark.parametrize(
    "event_kind", ["successful_tool", "failed_tool", "assistant_assertion"]
)
def test_pi_document_admits_only_successful_capability_evidence(
    tmp_path: Path, monkeypatch, event_kind
):
    from types import SimpleNamespace

    from app.models import Document, DocumentVersion
    from app.services.pi_local import LocalPiAgentClient

    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / f"pi-dynamic-{event_kind}.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", False)
    monkeypatch.setattr(
        "app.services.pi_local.resolve_local_pi_endpoint",
        lambda *args, **kwargs: SimpleNamespace(max_context=128_000),
    )
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid",
            "evidence": [],
            "knowledge_gaps": [],
            "retrieval": {"injection_flags": []},
        },
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            space = client.post(
                "/knowledge-spaces",
                headers=headers,
                json={"name": "动态证据", "description": ""},
            ).json()
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "动态证据写作", "space_ids": [space["id"]]},
            ).json()
            project = client.put(
                f"/writing/projects/{project['id']}/outline",
                headers=headers,
                json={"chapters": [{"title": "收入"}]},
            ).json()
            chapter_id = project["outline"]["chapters"][0]["id"]
            client.post(
                f"/writing/projects/{project['id']}/outline:confirm", headers=headers
            )
            text_path = tmp_path / "income.txt"
            text_path.write_text("收入为320万元。", encoding="utf-8")
            with transaction() as db:
                stored = db.get(WritingProject, project["id"])
                stamp = writing_repo.now()
                document = Document(
                    id="doc_dynamic",
                    space_id=space["id"],
                    name="收入说明",
                    status="active",
                    created_by_user_id=stored.owner_user_id,
                    created_at=stamp,
                    updated_at=stamp,
                )
                version = DocumentVersion(
                    id="ver_dynamic",
                    document_id=document.id,
                    version_number=1,
                    file_path=str(text_path),
                    processing_stage="ready",
                    queryable=True,
                    is_current_published=True,
                    created_at=stamp,
                    updated_at=stamp,
                )
                db.add_all([document, version])
            emitted_id = ""

            def chat(self, message, **kwargs):
                assert kwargs["token_warning_threshold"] == 3_000_000
                assert "token_budget" not in kwargs
                assert "initial_tokens" not in kwargs
                nonlocal emitted_id
                tool_response = client.post(
                    "/internal/v1/agent-capabilities/preview_document",
                    headers={
                        "Authorization": f"Bearer {kwargs['capability_token']}",
                        "X-Nexus-Agent-ID": self.agent_id,
                    },
                    json={
                        "arguments": {"document_id": "doc_dynamic", "max_chars": 4000},
                        "request_id": "dynamic-source",
                    },
                )
                assert tool_response.status_code == 200, tool_response.text
                tool_result = tool_response.json()
                emitted_id = tool_result["inline"]["citation_id"]
                if event_kind == "assistant_assertion":
                    event = {
                        "type": "message_update",
                        "assistantMessageEvent": {
                            "type": "text_delta",
                            "delta": json.dumps(tool_result),
                        },
                    }
                else:
                    event = {
                        "type": "tool_execution_end",
                        "toolName": "nexus_preview_document",
                        "toolCallId": "dynamic-source",
                        "isError": event_kind == "failed_tool",
                        "result": {"details": tool_result},
                    }
                asyncio.run(kwargs["on_event"](event))
                return FakePiClient(
                    {
                        "sections": [
                            {
                                "chapter_id": chapter_id,
                                "markdown": f"收入为320万元。 ^[{emitted_id}]",
                                "citations": [emitted_id],
                                "verification": {"passed": True, "findings": []},
                            }
                        ],
                        "verification": {"passed": True, "findings": []},
                    }
                ).chat(message)

            monkeypatch.setattr(LocalPiAgentClient, "chat", chat)
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "dynamic-evidence"},
            ).json()
            WritingDispatcher(
                worker_id="dynamic-worker",
                max_attempts=1,
                pi_provider=PiWritingProvider(LocalPiAgentClient(capability="writing")),
            ).run_once()
            status = client.get(
                f"/writing/operations/{operation['id']}",
                headers=headers,
            ).json()
            candidates = client.get(
                f"/writing/projects/{project['id']}/section-candidates",
                headers=headers,
            ).json()
            if event_kind != "successful_tool":
                assert status["status"] == "failed"
                assert candidates == []
                return
            assert status["status"] == "completed"
            section = next(row for row in candidates if row["kind"] == "section")
            assert section["status"] == "published"
            assert section["payload"]["citations"] == [emitted_id]
            support = section["payload"]["support_check"]
            assert support["structural_valid"] is True
            assert support["unsupported_tokens"] == []
            with database.SessionLocal() as db:
                receipt = (
                    db.query(WritingToolReceipt).filter_by(tool_name="pi_chat").one()
                )
                saved = json.loads(receipt.result_json)["evidence_payload"]
                assert saved["evidence"][0]["id"] == emitted_id
                assert saved["evidence"][0]["excerpt"] == "收入为320万元。"
                assert saved["evidence"][0]["text_sha256"] in emitted_id
                assert saved["tool_coverages"]
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


# ── live chapter previews ──────────────────────────────────────────


def _text_delta_events(reply: str, *, size: int) -> list[dict]:
    """Split a raw model reply into the text deltas the runtime streams."""
    return [
        {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "text_delta",
                "delta": reply[index : index + size],
            },
        }
        for index in range(0, len(reply), size)
    ]


def _streaming_chat(*, turns: list[dict]):
    """Build a ``LocalPiAgentClient.chat`` stand-in that streams scripted turns.

    Each turn is ``{"events": [...], "payload": {...}, "during": callable}``.
    A ``{"type": "test_pause"}`` event is consumed locally as a throttle window,
    and ``during`` runs after the last delta while the call is still in flight.
    """

    def chat(self, message, **kwargs):
        turn = turns.pop(0)

        async def run() -> None:
            for event in turn["events"]:
                if event.get("type") == "test_pause":
                    await asyncio.sleep(float(event.get("seconds") or 0.8))
                    continue
                await kwargs["on_event"](event)
            during = turn.get("during")
            if during is not None:
                during()

        asyncio.run(run())
        return PiAgentResult(
            payload=turn["payload"],
            session_id=kwargs["session_id"] or "pi-session-stream",
            model="qwen3.7-plus",
            run_time_ms=5,
            token_usage={
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
            trace_steps=1,
        )

    return chat


def _preview_snapshots(operation_id: str) -> list[dict]:
    with database.SessionLocal() as db:
        receipts = writing_repo.chapter_preview_events(
            db, platform_run_id=f"pi-intent:{operation_id}"
        )
        return [json.loads(receipt.payload_json)["preview"] for receipt in receipts]


def test_markdown_preview_decoder_handles_chunk_and_escape_boundaries():
    from app.services.writing_preview import MarkdownStreamDecoder

    markdown = '# 摘要\n\n他说"你好"，然后说 "再见"。\n\n| 项 | 值 |\n| --- | --- |'
    reply = json.dumps(
        {
            "sections": [
                {
                    "chapter_id": "wch_1",
                    "title": "摘要",
                    "markdown": markdown,
                    "citations": [],
                }
            ],
            "verification": {"passed": True},
        },
        ensure_ascii=False,
    )
    for size in (1, 2, 3, 5, 13, len(reply)):
        decoder = MarkdownStreamDecoder()
        for index in range(0, len(reply), size):
            decoder.feed(reply[index : index + size])
        assert decoder.markdown == markdown
        assert decoder.complete is True
        assert decoder.failed is False

    # Escapes and a surrogate pair split across deltas still decode exactly.
    decoder = MarkdownStreamDecoder()
    escaped = '{"sections":[{"markdown":"a\\nb\\ud83d\\ude00\\"c","x":1}]}'
    for index in range(len(escaped)):
        decoder.feed(escaped[index])
    assert decoder.markdown == 'a\nb\U0001f600"c'
    assert decoder.complete is True

    # Only the target field is decoded: sibling members never leak into it.
    decoder = MarkdownStreamDecoder()
    decoder.feed('{"note":"ignore","sections":[{"title":"t","markdown":"只取目标","x":1}]}')
    assert decoder.markdown == "只取目标"

    # A colon can terminate a key, never an unescaped quote inside a value.
    loose = '{"note":"他说 "结论": 很重要","sections":[{"markdown":"他说 "结论": 很重要。","citations":[]}]}'
    for size in (1, len(loose)):
        decoder = MarkdownStreamDecoder()
        for index in range(0, len(loose), size):
            decoder.feed(loose[index:index + size])
        assert decoder.markdown == '他说 "结论": 很重要。'
        assert decoder.complete is True
        assert decoder.failed is False

    # A stream that stops being valid JSON is reported as unavailable, never
    # guessed at — the chapter may still finish through the final parser.
    decoder = MarkdownStreamDecoder()
    decoder.feed('{"sections":[{"markdown":"abc\\qdef"}]}')
    assert decoder.markdown == ""
    assert decoder.failed is True
    assert decoder.complete is False


def test_chapter_preview_stream_throttles_and_supersedes_output():
    from app.services.writing_preview import ChapterPreviewStream

    now = [0.0]
    stream = ChapterPreviewStream(
        operation_id="wop_preview",
        chapter_id="wch_preview",
        attempt=2,
        base_revision=5,
        interval_seconds=0.75,
        clock=lambda: now[0],
    )
    assert stream.status == "preparing"
    assert stream.due() is True
    stream.mark_emitted(event_id=3)
    assert stream.event_id == 3
    assert stream.due() is False
    now[0] += 0.5
    assert stream.due() is False
    now[0] += 0.3
    assert stream.due() is True

    stream.feed('{"sections":[{"markdown":"第一版')
    assert stream.status == "streaming"
    assert stream.supersede() is True
    assert stream.status == "preparing"
    assert stream.markdown == ""
    assert stream.preview_id.endswith("a2r2")

    stream.feed('{"sections":[{"markdown":"第二版","x":1}]}')
    assert stream.markdown == "第二版"
    stream.transition("saved", markdown="第二版", revision=6)
    snapshot = stream.snapshot(event_id=9, updated_at="2026-09-15T00:00:00+00:00")
    assert snapshot["preview_id"] == stream.preview_id
    assert snapshot["status"] == "saved"
    assert snapshot["markdown"] == "第二版"
    assert snapshot["revision"] == 6
    assert snapshot["attempt"] == 2
    assert snapshot["base_revision"] == 5
    assert snapshot["event_id"] == 9


def test_chapter_preview_snapshot_keeps_the_newest_attempt_and_terminal_state(
    tmp_path: Path,
):
    from app.services.pi_writing_provider import _persist_chapter_preview
    from app.services.writing_preview import ChapterPreviewStream

    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-preview-attempts.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "预览身份", "space_ids": []},
            ).json()
            operation = client.post(
                f"/writing/projects/{project['id']}/outline-runs", headers=headers
            ).json()
            operation_id = operation["id"]

            def stored() -> dict:
                snapshots = _preview_snapshots(operation_id)
                # Superseded snapshots are dropped instead of accumulating.
                assert len(snapshots) == 1
                return snapshots[0]

            newer = ChapterPreviewStream(
                operation_id=operation_id,
                chapter_id="wch_target",
                attempt=2,
                base_revision=4,
            )
            newer.feed('{"sections":[{"markdown":"第二次输出","x":1}]}')
            _persist_chapter_preview(
                operation_id=operation_id, stream=newer, force=True
            )
            assert stored()["attempt"] == 2
            assert stored()["markdown"] == "第二次输出"

            stale = ChapterPreviewStream(
                operation_id=operation_id,
                chapter_id="wch_target",
                attempt=1,
                base_revision=4,
            )
            stale.feed('{"sections":[{"markdown":"过期的第一次输出","x":1}]}')
            _persist_chapter_preview(
                operation_id=operation_id, stream=stale, force=True
            )
            assert stored()["attempt"] == 2
            assert stored()["markdown"] == "第二次输出"

            newer.transition("saved", markdown="第二次输出", revision=5)
            _persist_chapter_preview(
                operation_id=operation_id, stream=newer, force=True
            )
            assert stored()["status"] == "saved"

            # A writer from the same attempt that was already in flight must not
            # un-settle a snapshot that reported its outcome.
            late = ChapterPreviewStream(
                operation_id=operation_id,
                chapter_id="wch_target",
                attempt=2,
                base_revision=4,
            )
            late.feed('{"sections":[{"markdown":"迟到的中间态","x":1}]}')
            assert late.preview_id == newer.preview_id
            _persist_chapter_preview(
                operation_id=operation_id, stream=late, force=True
            )
            assert stored()["status"] == "saved"
            assert stored()["markdown"] == "第二次输出"
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def _document_preview_fixture(
    client: TestClient, headers: dict[str, str], *, titles: list[str]
) -> tuple[dict, list[str]]:
    project = client.post(
        "/writing/projects",
        headers=headers,
        json={"title": "预览写作", "space_ids": []},
    ).json()
    project = client.put(
        f"/writing/projects/{project['id']}/outline",
        headers=headers,
        json={"chapters": [{"title": title} for title in titles]},
    ).json()
    chapter_ids = [item["id"] for item in project["outline"]["chapters"]]
    client.post(f"/writing/projects/{project['id']}/outline:confirm", headers=headers)
    return project, chapter_ids


@pytest.mark.parametrize("loose_reply", [False, True])
def test_pi_document_streams_decoded_preview_before_the_chapter_commits(
    tmp_path: Path, monkeypatch, loose_reply: bool
):
    from types import SimpleNamespace

    from app.services.pi_local import LocalPiAgentClient

    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-document-preview.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", False)
    monkeypatch.setattr(
        "app.services.pi_local.resolve_local_pi_endpoint",
        lambda *args, **kwargs: SimpleNamespace(max_context=128_000),
    )
    citation_id = "writing-evidence:ver_preview:0-40"
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid_chunk",
            "evidence": [{"id": citation_id, "excerpt": "本季度收入320万元。"}],
            "knowledge_gaps": [],
            "retrieval": {"injection_flags": []},
        },
    )
    markdown = '# 经营摘要\n\n本季度收入320万元。\n\n他说 "结论": 很重要。'
    observed: dict[str, object] = {}
    holder: dict[str, str] = {}
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project, chapter_ids = _document_preview_fixture(
                client, headers, titles=["经营摘要"]
            )
            chapter_id = chapter_ids[0]
            payload = {
                "sections": [
                    {
                        "chapter_id": chapter_id,
                        "title": "经营摘要",
                        "markdown": markdown,
                        "citations": [citation_id],
                        "verification": {"passed": True, "findings": []},
                    }
                ],
                "verification": {"passed": True, "findings": []},
            }
            reply = json.dumps(payload, ensure_ascii=False)
            if loose_reply:
                reply = reply.replace('\\"', '"')
            head_end = reply.index(markdown[:4]) + len(markdown[:4])
            events = (
                [{"type": "agent_start"}, {"type": "message_start"}]
                + _text_delta_events(reply[:head_end], size=7)
                + [{"type": "test_pause", "seconds": 0.8}]
                + _text_delta_events(reply[head_end:], size=7)
                + [{"type": "agent_settled"}]
            )

            def during() -> None:
                snapshots = _preview_snapshots(holder["id"])
                assert len(snapshots) == 1
                published = snapshots[0]
                # The chapter itself is still untouched while the model writes;
                # only the preview carries the decoded text so far.
                assert published["status"] == "streaming"
                assert published["markdown"]
                assert published["markdown"].startswith(markdown[:4])
                assert markdown.startswith(published["markdown"])
                assert len(published["markdown"]) < len(markdown)
                observed["published"] = published["markdown"]
                body = client.get(
                    f"/writing/projects/{project['id']}", headers=headers
                ).json()
                observed["body"] = body["outline"]["chapters"][0]["content"]

            async def raw_chat(self, message, **kwargs):
                for event in events:
                    if event.get("type") == "test_pause":
                        await asyncio.sleep(event["seconds"])
                        continue
                    await kwargs["on_event"](event)
                during()
                return SimpleNamespace(
                    reply=reply, stats={}, token_usage={}, events=events,
                ), None

            # Keep LocalPiAgentClient.chat's real parse_json_reply path: the
            # provider receives only what the raw model reply can actually parse.
            monkeypatch.setattr(LocalPiAgentClient, "chat_async", raw_chat)
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "pi-document-preview"},
            ).json()
            holder["id"] = operation["id"]
            WritingDispatcher(
                worker_id="pi-preview-worker",
                pi_provider=PiWritingProvider(
                    LocalPiAgentClient(capability="writing")
                ),
            ).run_once()

            assert observed["body"] == ""
            previews = client.get(
                f"/writing/operations/{operation['id']}/previews", headers=headers
            )
            assert previews.status_code == 200, previews.text
            body = previews.json()
            assert body["target_chapter_ids"] == [chapter_id]
            assert len(body["items"]) == 1
            preview = body["items"][0]
            assert preview["chapter_id"] == chapter_id
            assert preview["status"] == "saved"
            assert preview["markdown"] == markdown
            assert preview["candidate_id"]
            assert preview["revision"] == preview["base_revision"] + 1

            listed = client.get(
                f"/writing/operations/{operation['id']}/events", headers=headers
            ).json()
            snapshots = [
                item
                for item in listed["items"]
                if item["type"] == "chapter.preview.snapshot"
            ]
            assert len(snapshots) == 1
            assert snapshots[0]["chapter_id"] == chapter_id
            assert snapshots[0]["payload"]["preview"]["markdown"] == markdown
            assert snapshots[0]["payload"]["preview"]["status"] == "saved"

            refreshed = client.get(
                f"/writing/projects/{project['id']}", headers=headers
            ).json()
            assert refreshed["outline"]["chapters"][0]["content"] == markdown
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_document_preview_discards_superseded_model_output(
    tmp_path: Path, monkeypatch
):
    from types import SimpleNamespace

    from app.services.pi_local import LocalPiAgentClient

    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-document-preview-rounds.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", False)
    monkeypatch.setattr(
        "app.services.pi_local.resolve_local_pi_endpoint",
        lambda *args, **kwargs: SimpleNamespace(max_context=128_000),
    )
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid_chunk",
            "evidence": [],
            "knowledge_gaps": [],
            "retrieval": {"injection_flags": []},
        },
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project, chapter_ids = _document_preview_fixture(
                client, headers, titles=["经营摘要"]
            )
            chapter_id = chapter_ids[0]

            def payload_for(markdown: str) -> dict:
                return {
                    "sections": [
                        {
                            "chapter_id": chapter_id,
                            "title": "经营摘要",
                            "markdown": markdown,
                            "citations": [],
                            "verification": {"passed": True, "findings": []},
                        }
                    ],
                    "verification": {"passed": True, "findings": []},
                }

            draft = payload_for("旧稿正文。")
            final = payload_for("新稿正文。")
            draft_reply = json.dumps(draft, ensure_ascii=False)
            final_reply = json.dumps(final, ensure_ascii=False)
            events = (
                [{"type": "agent_start"}, {"type": "message_start"}]
                + _text_delta_events(draft_reply, size=9)
                # The model ends its message for a tool round and starts a new
                # one; the earlier text is a superseded output, not a prefix.
                + [{"type": "tool_execution_start", "toolName": "nexus_search_evidence"}]
                + [{"type": "message_start"}]
                + _text_delta_events(final_reply, size=9)
                + [{"type": "agent_settled"}]
            )
            monkeypatch.setattr(
                LocalPiAgentClient,
                "chat",
                _streaming_chat(
                    turns=[{"events": events, "payload": final, "during": None}]
                ),
            )
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "pi-document-preview-rounds"},
            ).json()
            WritingDispatcher(
                worker_id="pi-preview-rounds-worker",
                pi_provider=PiWritingProvider(
                    LocalPiAgentClient(capability="writing")
                ),
            ).run_once()

            snapshots = _preview_snapshots(operation["id"])
            assert len(snapshots) == 1
            assert snapshots[0]["status"] == "saved"
            assert snapshots[0]["markdown"] == "新稿正文。"
            assert snapshots[0]["preview_id"].endswith("r2")
            refreshed = client.get(
                f"/writing/projects/{project['id']}", headers=headers
            ).json()
            assert refreshed["outline"]["chapters"][0]["content"] == "新稿正文。"
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_document_regeneration_of_a_cleared_chapter_needs_adoption(
    tmp_path: Path, monkeypatch
):
    from types import SimpleNamespace

    from app.services.pi_local import LocalPiAgentClient

    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-document-preview-candidate.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", False)
    monkeypatch.setattr(
        "app.services.pi_local.resolve_local_pi_endpoint",
        lambda *args, **kwargs: SimpleNamespace(max_context=128_000),
    )
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid_chunk",
            "evidence": [],
            "knowledge_gaps": [],
            "retrieval": {"injection_flags": []},
        },
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project, chapter_ids = _document_preview_fixture(
                client, headers, titles=["经营摘要"]
            )
            chapter_id = chapter_ids[0]
            saved = client.put(
                f"/writing/projects/{project['id']}/chapters/{chapter_id}",
                headers=headers,
                json={"content": "人工初稿。"},
            )
            assert saved.status_code == 201, saved.text
            cleared = client.put(
                f"/writing/projects/{project['id']}/chapters/{chapter_id}",
                headers=headers,
                json={"content": ""},
            )
            assert cleared.status_code == 201, cleared.text
            assert cleared.json()["result"]["revision"] == 3
            payload = {
                "sections": [
                    {
                        "chapter_id": chapter_id,
                        "title": "经营摘要",
                        "markdown": "模型重写正文。",
                        "citations": [],
                        "verification": {"passed": True, "findings": []},
                    }
                ],
                "verification": {"passed": True, "findings": []},
            }
            monkeypatch.setattr(
                LocalPiAgentClient,
                "chat",
                _streaming_chat(
                    turns=[{"events": [], "payload": payload, "during": None}]
                ),
            )
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "pi-document-preview-candidate"},
            ).json()
            WritingDispatcher(
                worker_id="pi-preview-candidate-worker",
                pi_provider=PiWritingProvider(
                    LocalPiAgentClient(capability="writing")
                ),
            ).run_once()

            refreshed = client.get(
                f"/writing/projects/{project['id']}", headers=headers
            ).json()
            # An emptied chapter with saved history is still an existing version:
            # the model's text waits as a candidate instead of overwriting it.
            assert refreshed["outline"]["chapters"][0]["content"] == ""
            candidates = client.get(
                f"/writing/projects/{project['id']}/section-candidates",
                headers=headers,
            ).json()
            sections = [item for item in candidates if item["kind"] == "section"]
            assert len(sections) == 1
            assert sections[0]["status"] == "pending"

            previews = client.get(
                f"/writing/operations/{operation['id']}/previews", headers=headers
            ).json()
            preview = previews["items"][0]
            assert preview["status"] == "candidate"
            assert preview["candidate_id"] == sections[0]["id"]
            assert preview["revision"] is None
            assert preview["markdown"] == "模型重写正文。"
            assert sections[0]["payload"]["base_revision"] == preview["base_revision"]
            status = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            assert status["status"] == "completed"
            assert status["result"]["auto_published"] is False
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_operation_previews_report_failure_after_a_contract_error(
    tmp_path: Path, monkeypatch
):
    from types import SimpleNamespace

    from app.services.pi_local import LocalPiAgentClient

    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-document-preview-failure.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", False)
    monkeypatch.setattr(
        "app.services.pi_local.resolve_local_pi_endpoint",
        lambda *args, **kwargs: SimpleNamespace(max_context=128_000),
    )
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid_chunk",
            "evidence": [],
            "knowledge_gaps": [],
            "retrieval": {"injection_flags": []},
        },
    )
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project, chapter_ids = _document_preview_fixture(
                client, headers, titles=["经营摘要"]
            )
            chapter_id = chapter_ids[0]
            payload = {
                "sections": [
                    {
                        "chapter_id": chapter_id,
                        "title": "经营摘要",
                        "markdown": "无依据的正文 ^[writing-evidence:ver_absent:0-9]",
                        "citations": ["writing-evidence:ver_absent:0-9"],
                        "verification": {"passed": True, "findings": []},
                    }
                ],
                "verification": {"passed": True, "findings": []},
            }
            monkeypatch.setattr(
                LocalPiAgentClient,
                "chat",
                _streaming_chat(
                    turns=[
                        {
                            "events": _text_delta_events(
                                json.dumps(payload, ensure_ascii=False), size=11
                            ),
                            "payload": payload,
                            "during": None,
                        }
                    ]
                ),
            )
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "pi-document-preview-failure"},
            ).json()
            WritingDispatcher(
                worker_id="pi-preview-failure-worker",
                max_attempts=1,
                pi_provider=PiWritingProvider(
                    LocalPiAgentClient(capability="writing")
                ),
            ).run_once()

            status = client.get(
                f"/writing/operations/{operation['id']}", headers=headers
            ).json()
            assert status["status"] == "failed"
            candidates = client.get(
                f"/writing/projects/{project['id']}/section-candidates",
                headers=headers,
            ).json()
            assert candidates == []

            previews = client.get(
                f"/writing/operations/{operation['id']}/previews", headers=headers
            ).json()
            preview = previews["items"][0]
            # The attempt died after the model answered: no chapter was saved,
            # and the preview must not keep claiming it is still validating.
            assert preview["status"] == "failed"
            assert preview["error_message"]
            assert preview["revision"] is None
            refreshed = client.get(
                f"/writing/projects/{project['id']}", headers=headers
            ).json()
            assert refreshed["outline"]["chapters"][0]["content"] == ""
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_pi_document_preview_recovers_a_lost_terminal_snapshot(
    tmp_path: Path, monkeypatch
):
    from types import SimpleNamespace

    from app.services import pi_writing_provider as provider_module
    from app.services.pi_local import LocalPiAgentClient

    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-document-preview-lost-terminal.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", False)
    monkeypatch.setattr(
        "app.services.pi_local.resolve_local_pi_endpoint",
        lambda *args, **kwargs: SimpleNamespace(max_context=128_000),
    )
    monkeypatch.setattr(
        "app.services.pi_writing_provider.writing_evidence.search",
        lambda *args, **kwargs: {
            "provider": "hybrid_chunk",
            "evidence": [],
            "knowledge_gaps": [],
            "retrieval": {"injection_flags": []},
        },
    )
    persisted = provider_module._persist_chapter_preview_with_retry

    def failing(*, operation_id: str, stream, force: bool) -> None:
        if stream.status in {"saved", "candidate"}:
            raise RuntimeError("snapshot store unavailable")
        persisted(operation_id=operation_id, stream=stream, force=force)

    monkeypatch.setattr(
        provider_module, "_persist_chapter_preview_with_retry", failing
    )
    markdown = "本季度收入320万元。"
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            project, chapter_ids = _document_preview_fixture(
                client, headers, titles=["经营摘要"]
            )
            chapter_id = chapter_ids[0]
            payload = {
                "sections": [
                    {
                        "chapter_id": chapter_id,
                        "title": "经营摘要",
                        "markdown": markdown,
                        "citations": [],
                        "verification": {"passed": True, "findings": []},
                    }
                ],
                "verification": {"passed": True, "findings": []},
            }
            monkeypatch.setattr(
                LocalPiAgentClient,
                "chat",
                _streaming_chat(
                    turns=[
                        {
                            "events": _text_delta_events(
                                json.dumps(payload, ensure_ascii=False), size=7
                            ),
                            "payload": payload,
                            "during": None,
                        }
                    ]
                ),
            )
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "pi-document-preview-lost"},
            ).json()
            WritingDispatcher(
                worker_id="pi-preview-lost-worker",
                pi_provider=PiWritingProvider(
                    LocalPiAgentClient(capability="writing")
                ),
            ).run_once()

            refreshed = client.get(
                f"/writing/projects/{project['id']}", headers=headers
            ).json()
            chapter = refreshed["outline"]["chapters"][0]
            assert chapter["content"] == markdown
            assert chapter["revision"] == 2

            # The chapter is genuinely saved; its preview must say so even
            # though the terminal snapshot write never landed.
            stored = _preview_snapshots(operation["id"])
            assert stored
            assert stored[0]["status"] not in {"saved", "candidate"}
            previews = client.get(
                f"/writing/operations/{operation['id']}/previews", headers=headers
            ).json()
            preview = next(
                item for item in previews["items"] if item["chapter_id"] == chapter_id
            )
            assert preview["status"] == "saved"
            assert preview["markdown"] == markdown
            assert preview["revision"] == 2
            assert preview["candidate_id"]
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


@pytest.fixture
def quality_document(tmp_path: Path, monkeypatch):
    """使用真实授权的冻结来源，不替换事实检查或 ACL。"""
    from app.models import Document, DocumentVersion
    from app.services.evidence_snapshots import freeze_text, make_citation_id

    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "pi-quality.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "writing_provider", "pi_chat")
    monkeypatch.setattr(settings, "writing_pi_auto_publish", False)
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            space = client.post(
                "/knowledge-spaces", headers=headers,
                json={"name": "核验资料", "description": ""},
            ).json()
            project = client.post(
                "/writing/projects", headers=headers,
                json={"title": "核验报告", "space_ids": [space["id"]]},
            ).json()
            project = client.put(
                f"/writing/projects/{project['id']}/outline", headers=headers,
                json={"chapters": [{"title": "收入"}]},
            ).json()
            chapter_id = project["outline"]["chapters"][0]["id"]
            client.post(f"/writing/projects/{project['id']}/outline:confirm", headers=headers)
            text = "本季度收入320万元。"
            text_path = tmp_path / "income.txt"
            text_path.write_text(text, encoding="utf-8")
            with transaction() as db:
                stored = db.get(WritingProject, project["id"])
                stamp = writing_repo.now()
                document = Document(
                    id="doc_quality", space_id=space["id"], name="收入说明",
                    status="active", created_by_user_id=stored.owner_user_id,
                    created_at=stamp, updated_at=stamp,
                )
                version = DocumentVersion(
                    id="ver_quality", document_id=document.id, version_number=1,
                    file_path=str(text_path), processing_stage="ready", queryable=True,
                    is_current_published=True, created_at=stamp, updated_at=stamp,
                )
                db.add_all([document, version])
            digest = freeze_text("ver_quality", text)
            citation_id = make_citation_id("ver_quality", digest, 0, len(text))
            monkeypatch.setattr(
                "app.services.pi_writing_provider.writing_evidence.search",
                lambda *args, **kwargs: {
                    "provider": "hybrid", "knowledge_gaps": [],
                    "evidence": [{
                        "id": citation_id, "document_id": "doc_quality",
                        "document_version_id": "ver_quality", "text_sha256": digest,
                        "char_start": 0, "char_end": len(text), "excerpt": text,
                    }],
                    "retrieval": {"injection_flags": []},
                },
            )
            operation = client.post(
                f"/writing/projects/{project['id']}/document-runs",
                headers={**headers, "Idempotency-Key": "quality-run"},
            ).json()
            yield client, headers, project, chapter_id, operation, citation_id, text
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def _quality_section(chapter_id: str, markdown: str) -> dict:
    return {"sections": [{
        "chapter_id": chapter_id, "markdown": markdown, "citations": [],
        "verification": {"passed": True, "findings": []},
    }]}


def test_pi_document_quality_repairs_binding_and_accounts_both_calls(quality_document):
    client, headers, project, chapter_id, operation, citation_id, text = quality_document
    repaired = f"{text}^[{citation_id}]"
    fake = FakePiClient(payloads=[
        _quality_section(chapter_id, text), _quality_section(chapter_id, repaired),
    ])
    assert WritingDispatcher(
        worker_id="quality-worker", pi_provider=PiWritingProvider(fake),
    ).run_once()
    projected = client.get(f"/writing/operations/{operation['id']}", headers=headers).json()
    chapter = client.get(
        f"/writing/projects/{project['id']}", headers=headers,
    ).json()["outline"]["chapters"][0]
    quality = projected["result"]["quality_by_chapter"][chapter_id]
    assert projected["status"] == "completed"
    assert chapter["content"] == repaired
    assert quality["status"] == "checked"
    assert quality["semantic_support"] == "unverified"
    assert quality["issues"] == []
    assert quality["repair_attempted"] is True
    assert quality["revision"] == chapter["revision"]
    assert projected["usage"]["total_tokens"] == 240
    assert len(fake.prompts) == 2
    assert fake.idempotency_keys[0] != fake.idempotency_keys[1]
    candidates = client.get(
        f"/writing/projects/{project['id']}/section-candidates", headers=headers,
    ).json()
    assert candidates[0]["payload"]["quality_check"] == quality


@pytest.mark.parametrize("repair_outcome", [
    PiAgentError("limited", code="rate_limited"), TimeoutError("timed out"), {},
    PiAgentError("provider credential expired", code="auth_invalid"),
])
def test_pi_document_failed_quality_repair_keeps_initial_draft(
    quality_document, repair_outcome,
):
    client, headers, project, chapter_id, operation, _, text = quality_document
    fake = FakePiClient(payloads=[_quality_section(chapter_id, text), repair_outcome])
    assert WritingDispatcher(
        worker_id="quality-worker", pi_provider=PiWritingProvider(fake),
    ).run_once()
    projected = client.get(f"/writing/operations/{operation['id']}", headers=headers).json()
    chapter = client.get(
        f"/writing/projects/{project['id']}", headers=headers,
    ).json()["outline"]["chapters"][0]
    quality = projected["result"]["quality_by_chapter"][chapter_id]
    assert projected["status"] == "completed"
    assert chapter["content"] == text
    assert quality["status"] == "needs_attention"
    assert quality["repair_attempted"] is True
    assert any(issue["reason"] == "missing_inline_binding" and issue["blocking"] for issue in quality["issues"])
    assert any(issue["reason"] == "repair_failed" and not issue["blocking"] for issue in quality["issues"])
    assert len(fake.prompts) == 2


def test_pi_document_quality_cancellation_keeps_draft_preview_without_adoption(quality_document):
    client, headers, project, chapter_id, operation, _, text = quality_document
    fake = FakePiClient(payloads=[
        _quality_section(chapter_id, text), PiAgentCancelledError("cancelled"),
    ])
    assert WritingDispatcher(
        worker_id="quality-worker", pi_provider=PiWritingProvider(fake),
    ).run_once()
    projected = client.get(f"/writing/operations/{operation['id']}", headers=headers).json()
    chapter = client.get(
        f"/writing/projects/{project['id']}", headers=headers,
    ).json()["outline"]["chapters"][0]
    assert projected["status"] == "cancelled"
    assert chapter["content"] == ""
    preview = _preview_snapshots(operation["id"])[0]
    assert preview["markdown"] == text
    assert preview["status"] == "cancelled"
    assert len(fake.prompts) == 2


def test_pi_document_quality_repair_does_not_overwrite_concurrent_edit(quality_document):
    client, headers, project, chapter_id, operation, citation_id, text = quality_document

    class ConcurrentClient(FakePiClient):
        def chat(self, message, **kwargs):
            if self.prompts:
                with transaction() as db:
                    stored = db.get(WritingProject, project["id"])
                    chapter = writing_repo.chapter_for_project(db, project["id"], chapter_id)
                    writing_repo._advance_chapter(stored, chapter, "人工已确认正文", editor_user_id=None)
            return super().chat(message, **kwargs)

    fake = ConcurrentClient(payloads=[
        _quality_section(chapter_id, text),
        _quality_section(chapter_id, f"{text}^[{citation_id}]"),
    ])
    assert WritingDispatcher(
        worker_id="quality-worker", pi_provider=PiWritingProvider(fake),
    ).run_once()
    projected = client.get(f"/writing/operations/{operation['id']}", headers=headers).json()
    chapter = client.get(
        f"/writing/projects/{project['id']}", headers=headers,
    ).json()["outline"]["chapters"][0]
    assert projected["status"] == "completed"
    assert chapter["content"] == "人工已确认正文"
    assert projected["result"]["auto_published"] is False
    assert projected["result"]["quality_by_chapter"][chapter_id]["revision"] is None
    candidate = client.get(
        f"/writing/projects/{project['id']}/section-candidates", headers=headers,
    ).json()[0]
    adopted = client.post(
        f"/writing/projects/{project['id']}/section-candidates/{candidate['id']}:publish",
        headers=headers,
    )
    assert adopted.status_code == 409


@pytest.mark.parametrize("changed_text", ["本季度签约320万元。", "收入情况待确认。"])
def test_pi_document_quality_does_not_auto_adopt_content_changes(quality_document, changed_text):
    client, headers, project, chapter_id, operation, citation_id, text = quality_document
    fake = FakePiClient(payloads=[
        _quality_section(chapter_id, text),
        _quality_section(chapter_id, f"{changed_text}^[{citation_id}]"),
    ])
    assert WritingDispatcher(
        worker_id="quality-worker", pi_provider=PiWritingProvider(fake),
    ).run_once()
    projected = client.get(f"/writing/operations/{operation['id']}", headers=headers).json()
    chapter = client.get(
        f"/writing/projects/{project['id']}", headers=headers,
    ).json()["outline"]["chapters"][0]
    quality = projected["result"]["quality_by_chapter"][chapter_id]
    assert projected["status"] == "completed"
    assert chapter["content"] == text
    assert quality["status"] == "needs_attention"
    assert any(issue["reason"] == "missing_inline_binding" and issue["blocking"] for issue in quality["issues"])
    assert any(issue["reason"] == "repair_content_changed" and not issue["blocking"] for issue in quality["issues"])


@pytest.mark.parametrize("receipt_saved", [False, True])
def test_pi_document_quality_repair_reservation_survives_worker_restart(
    quality_document, monkeypatch, receipt_saved,
):
    client, headers, project, chapter_id, operation, citation_id, text = quality_document
    repaired = f"{text}^[{citation_id}]"
    fake = FakePiClient(payloads=[
        _quality_section(chapter_id, text), _quality_section(chapter_id, repaired),
    ])
    provider = PiWritingProvider(fake)
    original_chat = provider._chat_with_receipt
    interrupted = False

    def crash_once(**kwargs):
        nonlocal interrupted
        if kwargs["scope"].startswith("document-repair:") and not interrupted:
            interrupted = True
            if receipt_saved:
                original_chat(**kwargs)
            raise RuntimeError("worker interrupted")
        return original_chat(**kwargs)

    monkeypatch.setattr(provider, "_chat_with_receipt", crash_once)
    dispatcher = WritingDispatcher(worker_id="quality-worker", pi_provider=provider)
    assert dispatcher.run_once()
    with transaction() as db:
        outbox = writing_repo.dispatch_for_operation(db, operation["id"])
        outbox.status = "claimed"
        outbox.lease_owner = "quality-worker"
        outbox.lease_expires_at = writing_repo.now()
        outbox.next_attempt_at = writing_repo.now()
    assert dispatcher.run_once()
    projected = client.get(f"/writing/operations/{operation['id']}", headers=headers).json()
    chapter = client.get(
        f"/writing/projects/{project['id']}", headers=headers,
    ).json()["outline"]["chapters"][0]
    quality = projected["result"]["quality_by_chapter"][chapter_id]
    assert projected["status"] == "completed"
    assert quality["repair_attempted"] is True
    assert chapter["content"] == (repaired if receipt_saved else text)
    assert quality["status"] == ("checked" if receipt_saved else "needs_attention")
    assert len(fake.prompts) == (2 if receipt_saved else 1)
    assert projected["usage"]["total_tokens"] == (240 if receipt_saved else 120)
