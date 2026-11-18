"""HTTP contract tests for ``POST /access-requests:resolve``.

The endpoint's safety depends on ordering, so the tests check ordering too: a
forged citation must be refused *before* any ``DocumentVersion`` / ``Document``
row is opened, which is asserted by spying on the single lookup seam.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.core import database
from app.core.config import settings
from app.main import app
from app.models.chat import ChatSession, ChatSessionPublication
from app.models.documents import Document, DocumentVersion
from app.models.writing import (
    AgentRunBinding,
    WritingOperation,
    WritingProjectPublication,
    WritingToolReceipt,
)
from tests._auth import bootstrap_admin_response

RESOLVE = "/access-requests:resolve"
STAMP = datetime(2026, 9, 15, 3, 0, tzinfo=timezone.utc)


def _admin_headers(client: TestClient) -> dict[str, str]:
    response = bootstrap_admin_response(client)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _member_login(client: TestClient, username: str) -> dict[str, str]:
    admin = _admin_headers(client)
    invited = client.post(
        "/admin/users",
        headers=admin,
        json={
            "username": username,
            "email": f"{username}@example.com",
            "display_name": username,
        },
    )
    assert invited.status_code == 201
    accepted = client.post(
        f"/auth/invitations/{invited.json()['invite_token']}/accept",
        json={
            "password": "Member-password-123",
            "password_confirmation": "Member-password-123",
        },
    )
    assert accepted.status_code == 200
    login = client.post(
        "/auth/login", json={"username": username, "password": "Member-password-123"}
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _user_id(client: TestClient, headers: dict[str, str]) -> str:
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 200
    return response.json()["id"]


def _space(client: TestClient, admin: dict[str, str], name: str) -> str:
    created = client.post(
        "/knowledge-spaces", headers=admin, json={"name": name, "description": ""}
    )
    assert created.status_code == 201
    return created.json()["id"]


def _grant(client: TestClient, admin: dict[str, str], space_id: str, user_id: str) -> None:
    granted = client.post(
        f"/knowledge-spaces/{space_id}/members",
        headers=admin,
        json={"user_id": user_id, "role": "reader"},
    )
    assert granted.status_code == 201


def _revoke(client: TestClient, admin: dict[str, str], space_id: str, user_id: str) -> None:
    removed = client.delete(
        f"/knowledge-spaces/{space_id}/members/{user_id}", headers=admin
    )
    assert removed.status_code == 204


def _seed_document(
    *, space_id: str, document_id: str, version_id: str, owner_user_id: str
) -> None:
    with database.SessionLocal() as db:
        db.add(
            Document(
                id=document_id,
                space_id=space_id,
                category_id=None,
                name="报价单",
                status="active",
                is_temporary=False,
                chat_session_id=None,
                expires_at=None,
                temporary_cleanup_at=None,
                created_by_user_id=owner_user_id,
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        db.flush()
        db.add(
            DocumentVersion(
                id=version_id,
                document_id=document_id,
                version_number=1,
                processing_stage="ready",
                queryable=True,
                retryable=False,
                retry_of_version_id=None,
                failure_code=None,
                failure_message=None,
                source_relative_path=None,
                file_path=None,
                ext_task_id=None,
                chunk_count=0,
                summary="",
                extraction_method=None,
                page_count=0,
                ocr_page_count=0,
                vision_page_count=0,
                vision_usage_json="{}",
                extraction_warning_code=None,
                is_current_published=True,
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        db.commit()


def _record_retrieved_citation(
    *, project_id: str, run_id: str, citation_ids: list[str]
) -> None:
    with database.SessionLocal() as db:
        db.add(
            WritingOperation(
                id=f"wop_{run_id}",
                project_id=project_id,
                kind="section",
                status="completed",
                idempotency_key=None,
                input_revision=None,
                input_payload="{}",
                error_code=None,
                error_message_safe=None,
                last_event_cursor=0,
                usage="{}",
                result="{}",
                created_at=STAMP,
                started_at=STAMP,
                completed_at=STAMP,
            )
        )
        db.flush()
        db.add(
            AgentRunBinding(
                id=f"arb_{run_id}",
                writing_operation_id=f"wop_{run_id}",
                platform_run_id=run_id,
                team_id="team",
                team_version=1,
                status="completed",
                last_event_cursor=0,
                manifest_sha256=None,
                usage_summary="{}",
                created_at=STAMP,
                finished_at=STAMP,
            )
        )
        db.add(
            WritingToolReceipt(
                id=f"wtr_{run_id}",
                platform_run_id=run_id,
                tool_call_id=f"call_{run_id}",
                action_key=f"search_evidence:{run_id}",
                tool_name="search_evidence",
                request_sha256="0" * 64,
                status="succeeded",
                result_ref=None,
                result_sha256=None,
                result_json=json.dumps(
                    {"inline": {"evidence": [{"id": cid} for cid in citation_ids]}},
                    ensure_ascii=False,
                ),
                attempt_count=1,
                error_code=None,
                created_at=STAMP,
                completed_at=STAMP,
            )
        )
        db.commit()


def _project_with_citation(
    client: TestClient,
    owner: dict[str, str],
    *,
    space_ids: list[str],
    chapter_content: str,
) -> str:
    created = client.post(
        "/writing/projects",
        headers=owner,
        json={"title": "投标方案", "space_ids": space_ids},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    outlined = client.put(
        f"/writing/projects/{project_id}/outline",
        headers=owner,
        json={"chapters": [{"title": "正文"}]},
    )
    assert outlined.status_code == 200
    chapter_id = outlined.json()["outline"]["chapters"][0]["id"]
    saved = client.put(
        f"/writing/projects/{project_id}/chapters/{chapter_id}",
        headers=owner,
        json={"content": chapter_content},
    )
    assert saved.status_code == 201
    return project_id


def test_resolve_flags_documents_moved_out_of_the_project_scope(tmp_path: Path):
    """A listed document whose space left the project scope still blocks.

    This is the case the space loop cannot see: the cited space (the target) is
    fine, but the project also lists a document that now lives elsewhere, so
    approval alone will not restore the citation.
    """
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "resolve-out-of-scope-document.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            admin = _admin_headers(client)
            owner = _member_login(client, "resolve-scope-owner")
            owner_id = _user_id(client, owner)
            space_a = _space(client, admin, "引用空间")
            space_b = _space(client, admin, "已被移出的空间")
            for space_id in (space_a, space_b):
                _grant(client, admin, space_id, owner_id)
            _seed_document(
                space_id=space_a,
                document_id="doc_cited",
                version_id="ver_cited",
                owner_user_id=owner_id,
            )
            _seed_document(
                space_id=space_b,
                document_id="doc_moved",
                version_id="ver_moved",
                owner_user_id=owner_id,
            )
            citation = "writing-evidence:ver_cited:1-9"
            project_id = _project_with_citation(
                client,
                owner,
                space_ids=[space_a],
                chapter_content="正文 ^[writing-evidence:ver_cited:1-9]",
            )
            _record_retrieved_citation(
                project_id=project_id, run_id="prun_scope", citation_ids=[citation]
            )
            _revoke(client, admin, space_a, owner_id)

            baseline = client.post(
                RESOLVE,
                headers=owner,
                json={
                    "source": {"kind": "writing", "project_id": project_id},
                    "citation_id": citation,
                },
            )
            assert baseline.status_code == 200, baseline.text
            assert baseline.json()["other_scope_blocked"] is False

            # The project now also lists a document sitting in space B, which is
            # not part of the project's spaces. The cited document stays listed
            # too, otherwise the citation itself would be out of scope and there
            # would be no target to report.
            with database.SessionLocal() as db:
                from app.models.writing import WritingProject

                project = db.get(WritingProject, project_id)
                assert project is not None
                project.selected_document_ids = json.dumps(["doc_cited", "doc_moved"])
                db.commit()

            blocked = client.post(
                RESOLVE,
                headers=owner,
                json={
                    "source": {"kind": "writing", "project_id": project_id},
                    "citation_id": citation,
                },
            )
            assert blocked.status_code == 200, blocked.text
            assert blocked.json()["other_scope_blocked"] is True
            assert [t["target_name"] for t in blocked.json()["targets"]] == ["引用空间"]
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_resolve_reports_missing_access_for_the_cited_space(tmp_path: Path):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "resolve-missing-access.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            admin = _admin_headers(client)
            owner = _member_login(client, "resolve-owner")
            owner_id = _user_id(client, owner)
            space_a = _space(client, admin, "商务部资料空间")
            space_b = _space(client, admin, "无关空间")
            for space_id in (space_a, space_b):
                _grant(client, admin, space_id, owner_id)
            _seed_document(
                space_id=space_a,
                document_id="doc_price",
                version_id="ver_price",
                owner_user_id=owner_id,
            )
            citation = "writing-evidence:ver_price:10-20"
            project_id = _project_with_citation(
                client,
                owner,
                space_ids=[space_a, space_b],
                chapter_content=f"报价 ^[writing-evidence:ver_price:10-20] 结束",
            )
            _record_retrieved_citation(
                project_id=project_id, run_id="prun_resolve_1", citation_ids=[citation]
            )
            # The owner loses access to the cited space only; scope has no
            # other failure, so nothing "other" is blocked.
            _revoke(client, admin, space_a, owner_id)

            response = client.post(
                RESOLVE,
                headers=owner,
                json={
                    "source": {"kind": "writing", "project_id": project_id},
                    "citation_id": citation,
                },
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["citation_state"] == "missing_access"
            assert [(t["target_type"], t["target_name"]) for t in body["targets"]] == [
                ("space", "商务部资料空间")
            ]
            assert body["targets"][0]["owner_display_name"]
            assert body["other_scope_blocked"] is False
            assert body["requestable"] is True

            # Losing the second space too makes it "apply and still blocked".
            _revoke(client, admin, space_b, owner_id)
            blocked = client.post(
                RESOLVE,
                headers=owner,
                json={
                    "source": {"kind": "writing", "project_id": project_id},
                    "citation_id": citation,
                },
            )
            assert blocked.status_code == 200, blocked.text
            assert blocked.json()["other_scope_blocked"] is True
            assert blocked.json()["targets"] == body["targets"]

            # Anchor-level entry (no citation_id) enumerates the same target
            # and reports no per-citation verdict.
            anchor_level = client.post(
                RESOLVE,
                headers=owner,
                json={"source": {"kind": "writing", "project_id": project_id}},
            )
            assert anchor_level.status_code == 200, anchor_level.text
            assert anchor_level.json()["citation_state"] is None
            assert anchor_level.json()["targets"] == body["targets"]
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_resolve_refuses_forged_marker_before_any_document_lookup(
    tmp_path: Path, monkeypatch
):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "resolve-forged.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            admin = _admin_headers(client)
            owner = _member_login(client, "resolve-forged-owner")
            owner_id = _user_id(client, owner)
            space_id = _space(client, admin, "伪造空间")
            _grant(client, admin, space_id, owner_id)
            # A real document exists, but it was never recorded as evidence for
            # this project — the marker was typed by hand.
            _seed_document(
                space_id=space_id,
                document_id="doc_real",
                version_id="ver_real",
                owner_user_id=owner_id,
            )
            forged = "writing-evidence:ver_real:0-9"
            project_id = _project_with_citation(
                client,
                owner,
                space_ids=[space_id],
                chapter_content=f"手写标记 ^[writing-evidence:ver_real:0-9] 结束",
            )

            calls: list[str] = []

            def _spy(_db, citation_id):
                calls.append(citation_id)
                raise AssertionError("forged citation must not open document rows")

            monkeypatch.setattr("app.api.access_requests._citation_document", _spy)

            forged_response = client.post(
                RESOLVE,
                headers=owner,
                json={
                    "source": {"kind": "writing", "project_id": project_id},
                    "citation_id": forged,
                },
            )
            unknown_response = client.post(
                RESOLVE,
                headers=owner,
                json={
                    "source": {"kind": "writing", "project_id": project_id},
                    "citation_id": "writing-evidence:ver_nope:0-9",
                },
            )

            assert forged_response.status_code == 404
            assert unknown_response.status_code == 404
            # Indistinguishable, and neither one reached a document lookup.
            assert forged_response.json() == unknown_response.json()
            assert calls == []
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_resolve_reports_already_allowed_without_offering_a_target(tmp_path: Path):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "resolve-already-allowed.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            admin = _admin_headers(client)
            owner = _member_login(client, "resolve-allowed-owner")
            owner_id = _user_id(client, owner)
            space_id = _space(client, admin, "已授权空间")
            _grant(client, admin, space_id, owner_id)
            _seed_document(
                space_id=space_id,
                document_id="doc_allowed",
                version_id="ver_allowed",
                owner_user_id=owner_id,
            )
            citation = "writing-evidence:ver_allowed:5-15"
            project_id = _project_with_citation(
                client,
                owner,
                space_ids=[space_id],
                chapter_content="正文 ^[writing-evidence:ver_allowed:5-15]",
            )
            _record_retrieved_citation(
                project_id=project_id, run_id="prun_allowed", citation_ids=[citation]
            )

            response = client.post(
                RESOLVE,
                headers=owner,
                json={
                    "source": {"kind": "writing", "project_id": project_id},
                    "citation_id": citation,
                },
            )
            assert response.status_code == 200, response.text
            assert response.json() == {
                "citation_state": "already_allowed",
                "targets": [],
                "other_scope_blocked": False,
                "requestable": False,
            }
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_resolve_writing_publication_reads_the_frozen_set(tmp_path: Path, monkeypatch, seed_writing_publication):
    from app.models.auth import User
    from app.services.chat_docx_delivery import deliver_chat_reply_docx
    from app.services.evidence_snapshots import freeze_text, make_citation_id

    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "resolve-publication.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            admin = _admin_headers(client)
            owner = _member_login(client, "resolve-pub-owner")
            outsider = _member_login(client, "resolve-pub-outsider")
            owner_id = _user_id(client, owner)
            space_id = _space(client, admin, "共享来源空间")
            _grant(client, admin, space_id, owner_id)
            _seed_document(
                space_id=space_id,
                document_id="doc_shared",
                version_id="ver_shared",
                owner_user_id=owner_id,
            )
            text = "企业知识协作资料。"
            digest = freeze_text("ver_shared", text)
            citation_id = make_citation_id("ver_shared", digest, 0, len(text))
            evidence = {
                "citation_id": citation_id, "document_id": "doc_shared",
                "document_version_id": "ver_shared", "text_sha256": digest,
                "char_start": 0, "char_end": len(text), "document_name": "报价单",
            }
            # Automatic chat drafts have a completed export source record, but
            # no tool-run binding/receipt. The inherited reference is still real.
            with database.SessionLocal() as db:
                acting_user = db.get(User, owner_id)
                assert acting_user is not None
                delivered = deliver_chat_reply_docx(
                    db=db, user=acting_user, reply=f"正文 ^[{citation_id}]",
                    space_ids=[space_id], document_ids=["doc_shared"], evidence=[evidence],
                )
                assert delivered is not None
                project_id = delivered["project_id"]
                db.commit()
            publication_id = seed_writing_publication(
                project_id, evidence_sources=[evidence],
            )
            _revoke(client, admin, space_id, owner_id)
            missing_access = client.post(
                RESOLVE, headers=owner,
                json={
                    "source": {"kind": "writing", "project_id": project_id},
                    "citation_id": citation_id,
                },
            )
            assert missing_access.status_code == 200, missing_access.text
            assert missing_access.json()["citation_state"] == "missing_access"

            # Model-declared evidence and a formerly rejected source cannot
            # authorize citation membership, even when the marker remains.
            with database.SessionLocal() as db:
                operation = db.query(WritingOperation).filter_by(
                    project_id=project_id, kind="export",
                ).one()
                result = json.loads(operation.result)
                result["evidence_sources"][0]["snapshot_status"] = "unavailable"
                operation.result = json.dumps(result)
                db.commit()
            unverified = client.post(
                RESOLVE, headers=owner,
                json={
                    "source": {"kind": "writing", "project_id": project_id},
                    "citation_id": citation_id,
                },
            )
            assert unverified.status_code == 404

            # A viewer who cannot read the source space still gets a target.
            listed = client.post(
                RESOLVE,
                headers=outsider,
                json={"source": {"kind": "writing_publication", "publication_id": publication_id}},
            )
            assert listed.status_code == 200, listed.text
            assert [t["target_name"] for t in listed.json()["targets"]] == [
                "共享来源空间"
            ]
            assert listed.json()["citation_state"] is None

            # Pre-existing rows have no frozen set: no citation-level entry.
            with database.SessionLocal() as db:
                row = db.get(WritingProjectPublication, publication_id)
                assert row is not None
                row.citation_refs_json = None
                db.commit()
            after = client.post(
                RESOLVE,
                headers=outsider,
                json={"source": {"kind": "writing_publication", "publication_id": publication_id}},
            )
            assert after.status_code == 404
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_resolve_chat_publication_uses_its_server_validated_space_set(tmp_path: Path):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "resolve-chat-publication.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            admin = _admin_headers(client)
            owner = _member_login(client, "resolve-chat-owner")
            viewer = _member_login(client, "resolve-chat-viewer")
            owner_id = _user_id(client, owner)
            space_id = _space(client, admin, "会话来源空间")
            _grant(client, admin, space_id, owner_id)

            def _publication(space_ids: list[str], publication_id: str) -> None:
                with database.SessionLocal() as db:
                    db.add(
                        ChatSession(
                            id=f"chs_{publication_id}",
                            owner_user_id=owner_id,
                            title="会话",
                            messages_revision=0,
                            title_finalized=True,
                            created_at=STAMP,
                            updated_at=STAMP,
                        )
                    )
                    db.flush()
                    db.add(
                        ChatSessionPublication(
                            id=publication_id,
                            source_session_id=f"chs_{publication_id}",
                            source_owner_user_id=owner_id,
                            shared_by_user_id=owner_id,
                            title="共享会话",
                            summary="",
                            version=1,
                            snapshot_json="[]",
                            content_hash="0" * 64,
                            share_scope="all_internal",
                            space_ids_json=json.dumps(space_ids),
                            allow_cite=True,
                            allow_remix=True,
                            status="active",
                            published_at=STAMP,
                            revoked_at=None,
                            created_at=STAMP,
                        )
                    )
                    db.commit()

            _publication([], "chp_empty_spaces")
            empty = client.post(
                RESOLVE,
                headers=viewer,
                json={"source": {"kind": "chat_publication", "publication_id": "chp_empty_spaces"}},
            )
            assert empty.status_code == 404

            _publication([space_id], "chp_with_space")
            listed = client.post(
                RESOLVE,
                headers=viewer,
                json={"source": {"kind": "chat_publication", "publication_id": "chp_with_space"}},
            )
            assert listed.status_code == 200, listed.text
            assert [t["target_name"] for t in listed.json()["targets"]] == [
                "会话来源空间"
            ]
            assert listed.json()["citation_state"] is None

            # ``citation_id`` is not expressible on this anchor at all.
            rejected = client.post(
                RESOLVE,
                headers=viewer,
                json={
                    "source": {"kind": "chat_publication", "publication_id": "chp_with_space"},
                    "citation_id": "[citation:0]",
                },
            )
            assert rejected.status_code == 422
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_resolve_marks_targets_with_an_in_flight_request(tmp_path: Path):
    """A pending row for the same target shows up as state=pending, not a fresh offer."""
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "resolve-pending.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            admin = _admin_headers(client)
            owner = _member_login(client, "resolve-pending-owner")
            owner_id = _user_id(client, owner)
            space_id = _space(client, admin, "待审批空间")
            _grant(client, admin, space_id, owner_id)
            _seed_document(
                space_id=space_id,
                document_id="doc_pending",
                version_id="ver_pending",
                owner_user_id=owner_id,
            )
            citation = "writing-evidence:ver_pending:1-9"
            project_id = _project_with_citation(
                client,
                owner,
                space_ids=[space_id],
                chapter_content="正文 ^[writing-evidence:ver_pending:1-9]",
            )
            _record_retrieved_citation(
                project_id=project_id, run_id="prun_pending", citation_ids=[citation]
            )
            _revoke(client, admin, space_id, owner_id)

            first = client.post(
                RESOLVE,
                headers=owner,
                json={
                    "source": {"kind": "writing", "project_id": project_id},
                    "citation_id": citation,
                },
            )
            assert first.status_code == 200, first.text
            assert first.json()["targets"][0]["state"] == "missing_access"

            from app.models.access import AccessRequest

            with database.SessionLocal() as db:
                db.add(
                    AccessRequest(
                        id="arq_pending",
                        target_type="space",
                        target_id=space_id,
                        granted_role="reader",
                        requester_user_id=owner_id,
                        reason="",
                        source_kind="writing",
                        source_id=project_id,
                        source_label="投标方案",
                        source_refs_json="",
                        citation_refs_json="",
                        open_locator_json="",
                        status="pending",
                        decided_by_user_id=None,
                        decided_at=None,
                        decision_note=None,
                        created_at=STAMP,
                        updated_at=STAMP,
                    )
                )
                db.commit()

            second = client.post(
                RESOLVE,
                headers=owner,
                json={
                    "source": {"kind": "writing", "project_id": project_id},
                    "citation_id": citation,
                },
            )
            assert second.status_code == 200, second.text
            assert second.json()["targets"][0]["state"] == "pending"
            assert second.json()["targets"][0]["target_name"] == "待审批空间"
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_resolve_rejects_unknown_discovery_handle(tmp_path: Path):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "resolve-discovery.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            viewer = _member_login(client, "resolve-discovery-viewer")
            response = client.post(
                RESOLVE,
                headers=viewer,
                json={"source": {"kind": "discovery", "discovery_id": "did_unknown"}},
            )
            assert response.status_code == 404
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)
