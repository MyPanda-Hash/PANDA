from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core import database
from app.core.config import settings
from app.core.database import transaction
from app.core.security import new_token, now
from app.main import app
from app.models.auth import User
from app.models.chat import ChatSession
from app.models.documents import Document, DocumentVersion
from app.models.knowledge_graph import KnowledgeEntity, KnowledgeRelation
from app.models.writing import WritingProject, WritingProjectMember
from app.services.agent_capability import create_capability_token
from app.services.pi_runtime.config import PI_RUNTIME_AGENT_ID
from tests._auth import bootstrap_admin_response


def _admin_login(client: TestClient) -> tuple[dict[str, str], str]:
    response = bootstrap_admin_response(client)
    assert response.status_code == 200
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


def _admin_headers(client: TestClient) -> dict[str, str]:
    return _admin_login(client)[0]


def test_capability_grant_lists_only_scoped_spaces(tmp_path: Path, monkeypatch) -> None:
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "agent-capabilities.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(
        settings, "agent_capability_secret", "capability-secret-for-tests-32bytes"
    )
    monkeypatch.setattr(settings, "environment", "test")
    try:
        with TestClient(app) as client:
            headers, admin_user_id = _admin_login(client)
            first = client.post(
                "/knowledge-spaces",
                headers=headers,
                json={"name": "授权空间", "description": "visible"},
            )
            second = client.post(
                "/knowledge-spaces",
                headers=headers,
                json={"name": "未授权空间", "description": "hidden"},
            )
            assert first.status_code == 201, first.text
            assert second.status_code == 201, second.text
            first_id = first.json()["id"]
            second_id = second.json()["id"]
            token = create_capability_token(
                user_id=admin_user_id,
                agent_id=PI_RUNTIME_AGENT_ID,
                space_ids=[first_id],
                space_mode="selected",
                allowed_tools=["list_spaces", "list_documents", "search_evidence"],
            )
            capability_headers = {
                "Authorization": f"Bearer {token}",
                "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
            }

            spaces = client.post(
                "/internal/v1/agent-capabilities/list_spaces",
                headers=capability_headers,
                json={"arguments": {}},
            )
            assert spaces.status_code == 200, spaces.text
            assert [item["id"] for item in spaces.json()["inline"]["items"]] == [
                first_id
            ]

            documents = client.post(
                "/internal/v1/agent-capabilities/list_documents",
                headers=capability_headers,
                json={"arguments": {"space_id": first_id}},
            )
            assert documents.status_code == 200, documents.text
            assert documents.json()["inline"]["items"] == []

            denied = client.post(
                "/internal/v1/agent-capabilities/list_documents",
                headers=capability_headers,
                json={"arguments": {"space_id": second_id}},
            )
            assert denied.status_code == 403, denied.text

            wrong_agent = client.post(
                "/internal/v1/agent-capabilities/list_spaces",
                headers={**capability_headers, "X-Nexus-Agent-ID": "another-agent"},
                json={"arguments": {}},
            )
            assert wrong_agent.status_code == 403, wrong_agent.text
    finally:
        database.reset_database(path)
        settings.db_path, settings.database_url = old_path, old_url


@pytest.fixture
def project_context_scope(tmp_path: Path, monkeypatch):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "project-context-scope.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(
        settings, "agent_capability_secret", "capability-secret-for-tests-32bytes"
    )
    monkeypatch.setattr(settings, "environment", "test")
    try:
        with TestClient(app) as client:
            headers, owner_id = _admin_login(client)
            space = client.post(
                "/knowledge-spaces", headers=headers, json={"name": "项目证据空间"}
            )
            assert space.status_code == 201, space.text
            space_id = space.json()["id"]
            editor = client.post(
                "/admin/users",
                headers=headers,
                json={
                    "username": "context-editor",
                    "email": "context-editor@example.com",
                    "display_name": "项目编辑者",
                    "password": "Editor-password-123",
                },
            )
            assert editor.status_code == 201, editor.text
            editor_id = editor.json()["user_id"]
            member = client.post(
                f"/knowledge-spaces/{space_id}/members",
                headers=headers,
                json={"user_id": editor_id, "role": "reader"},
            )
            assert member.status_code == 201, member.text
            with transaction() as db:
                stamp = now()
                db.add(Document(
                    id="context-document", space_id=space_id, name="项目资料",
                    status="active", created_by_user_id=editor_id,
                    created_at=stamp, updated_at=stamp,
                ))
                db.add(WritingProject(
                    id="context-project", owner_user_id=owner_id, title="项目上下文",
                    selected_space_ids=json.dumps([space_id]),
                    selected_document_ids='["context-document"]',
                    created_at=stamp, updated_at=stamp,
                ))
                db.flush()
                db.add(WritingProjectMember(
                    id="context-editor-membership", project_id="context-project",
                    user_id=editor_id, role="editor", invited_by_user_id=owner_id,
                    status="active", created_at=stamp, updated_at=stamp,
                ))
            yield client, owner_id, editor_id, space_id
    finally:
        database.reset_database(path)
        settings.db_path, settings.database_url = old_path, old_url


@pytest.mark.parametrize(
    "invalid_scope",
    ["archived", "expired", "missing", "outside_project", "revoked_acl", "signed_exclusion"],
)
def test_capability_project_context_rechecks_selected_documents(
    project_context_scope, invalid_scope: str,
) -> None:
    client, owner_id, editor_id, space_id = project_context_scope
    token_args = {
        "user_id": owner_id if invalid_scope == "signed_exclusion" else editor_id,
        "agent_id": PI_RUNTIME_AGENT_ID,
        "space_ids": [space_id], "document_ids": ["context-document"],
        "project_ids": ["context-project"], "allowed_tools": ["get_project_context"],
    }
    headers = {
        "Authorization": "Bearer " + create_capability_token(**token_args),
        "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
    }
    endpoint = "/internal/v1/agent-capabilities/get_project_context"
    payload = {"arguments": {"project_id": "context-project"}}
    valid = client.post(endpoint, headers=headers, json=payload)
    assert valid.status_code == 200, valid.text
    assert valid.json()["inline"]["document_ids"] == ["context-document"]
    assert valid.json()["inline"]["space_ids"] == [space_id]

    with transaction() as db:
        document = db.get(Document, "context-document")
        project = db.get(WritingProject, "context-project")
        assert document is not None and project is not None
        if invalid_scope == "archived":
            document.status = "archived"
        elif invalid_scope == "expired":
            document.expires_at = now() - timedelta(seconds=1)
        elif invalid_scope == "missing":
            project.selected_document_ids = '["missing-document"]'
            token_args["document_ids"] = ["missing-document"]
        elif invalid_scope == "outside_project":
            project.selected_space_ids = "[]"
        elif invalid_scope == "revoked_acl":
            db.execute(
                text("DELETE FROM space_members WHERE space_id=:space_id AND user_id=:user_id"),
                {"space_id": space_id, "user_id": editor_id},
            )
        else:
            token_args["document_ids"] = []
    headers["Authorization"] = "Bearer " + create_capability_token(**token_args)
    denied = client.post(endpoint, headers=headers, json=payload)
    assert denied.status_code == 403, denied.text
    assert "inline" not in denied.json()


@pytest.mark.parametrize(
    "binding",
    ["valid", "wrong_owner", "wrong_session", "missing_source_session", "unsigned"],
)
def test_capability_project_context_requires_signed_temporary_session_scope(
    project_context_scope, binding: str,
) -> None:
    client, owner_id, editor_id, _space_id = project_context_scope
    with transaction() as db:
        stamp = now()
        db.add(ChatSession(
            id="context-session", owner_user_id=editor_id, title="来源会话",
            created_at=stamp, updated_at=stamp,
        ))
        db.flush()
        document = db.get(Document, "context-document")
        project = db.get(WritingProject, "context-project")
        assert document is not None and project is not None
        document.space_id = None
        document.is_temporary = True
        document.chat_session_id = "context-session"
        document.expires_at = now() + timedelta(hours=1)
        if binding == "wrong_owner":
            document.created_by_user_id = owner_id
        project.selected_space_ids = "[]"
        project.source_chat_session_id = (
            None if binding == "missing_source_session" else "context-session"
        )
    token = create_capability_token(
        user_id=editor_id, agent_id=PI_RUNTIME_AGENT_ID, space_ids=[],
        document_ids=None if binding == "unsigned" else ["context-document"],
        project_ids=["context-project"], allowed_tools=["get_project_context"],
        conversation_id="other-session" if binding == "wrong_session" else "context-session",
    )
    response = client.post(
        "/internal/v1/agent-capabilities/get_project_context",
        headers={
            "Authorization": f"Bearer {token}", "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
        },
        json={"arguments": {"project_id": "context-project"}},
    )
    if binding == "valid":
        assert response.status_code == 200, response.text
        assert response.json()["inline"]["document_ids"] == ["context-document"]
        assert response.json()["inline"]["space_ids"] == []
    else:
        assert response.status_code == 403, response.text
        assert "inline" not in response.json()


def test_capability_project_context_preserves_empty_scope(project_context_scope) -> None:
    client, owner_id, _editor_id, _space_id = project_context_scope
    with transaction() as db:
        project = db.get(WritingProject, "context-project")
        assert project is not None
        project.selected_space_ids = "[]"
        project.selected_document_ids = "[]"
    token = create_capability_token(
        user_id=owner_id, agent_id=PI_RUNTIME_AGENT_ID,
        space_ids=["unavailable-space"], document_ids=[],
        project_ids=["context-project"], allowed_tools=["get_project_context"],
    )
    response = client.post(
        "/internal/v1/agent-capabilities/get_project_context",
        headers={
            "Authorization": f"Bearer {token}", "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
        },
        json={"arguments": {"project_id": "context-project"}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["inline"]["space_ids"] == []
    assert response.json()["inline"]["document_ids"] == []


def test_system_capabilities_are_admin_only_and_redact_secrets(
    tmp_path: Path, monkeypatch
) -> None:
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "system-capabilities.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(
        settings, "agent_capability_secret", "capability-secret-for-tests-32bytes"
    )
    monkeypatch.setattr(settings, "environment", "test")
    try:
        with TestClient(app) as client:
            admin_headers, admin_user_id = _admin_login(client)
            created = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "system-member",
                    "email": "system-member@example.com",
                    "display_name": "系统成员",
                    "password": "Member-password-123",
                },
            )
            assert created.status_code == 201, created.text
            member_id = created.json()["user_id"]

            tools = [
                "get_system_overview",
                "list_system_users",
                "get_user_usage",
            ]
            admin_token = create_capability_token(
                user_id=admin_user_id,
                agent_id=PI_RUNTIME_AGENT_ID,
                allowed_tools=tools,
            )
            capability_headers = {
                "Authorization": f"Bearer {admin_token}",
                "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
            }

            overview = client.post(
                "/internal/v1/agent-capabilities/get_system_overview",
                headers=capability_headers,
                json={"arguments": {}},
            )
            assert overview.status_code == 200, overview.text
            assert overview.json()["inline"]["users"]["total"] == 2

            users = client.post(
                "/internal/v1/agent-capabilities/list_system_users",
                headers=capability_headers,
                json={"arguments": {"query": "system-member"}},
            )
            assert users.status_code == 200, users.text
            items = users.json()["inline"]["items"]
            assert len(items) == 1
            assert items[0]["id"] == member_id
            assert items[0]["display_name"] == "系统成员"
            assert "api_key" not in items[0]
            assert "password_hash" not in items[0]

            usage = client.post(
                "/internal/v1/agent-capabilities/get_user_usage",
                headers=capability_headers,
                json={"arguments": {"user_id": member_id}},
            )
            assert usage.status_code == 200, usage.text
            assert usage.json()["inline"]["operation_count"] == 0

            member_login = client.post(
                "/auth/login",
                json={
                    "username": "system-member",
                    "password": "Member-password-123",
                },
            )
            assert member_login.status_code == 200, member_login.text
            admin_capabilities = client.get(
                "/chat/capabilities", headers=admin_headers
            )
            assert admin_capabilities.status_code == 200, admin_capabilities.text
            assert set(tools).issubset(set(admin_capabilities.json()["tools"]))
            member_headers = {
                "Authorization": f"Bearer {member_login.json()['access_token']}"
            }
            member_capabilities = client.get(
                "/chat/capabilities", headers=member_headers
            )
            assert member_capabilities.status_code == 200, member_capabilities.text
            assert not set(tools).intersection(member_capabilities.json()["tools"])
            member_token = create_capability_token(
                user_id=member_id,
                agent_id=PI_RUNTIME_AGENT_ID,
                allowed_tools=["get_system_overview"],
            )
            denied = client.post(
                "/internal/v1/agent-capabilities/get_system_overview",
                headers={
                    "Authorization": f"Bearer {member_token}",
                    "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
                },
                json={"arguments": {}},
            )
            assert denied.status_code == 403, denied.text
    finally:
        database.reset_database(path)
        settings.db_path, settings.database_url = old_path, old_url


def test_pi_client_forwards_opaque_capability_token() -> None:
    from app.services.pi_agent_client import PiAgentClient

    observed: dict[str, dict[str, Any]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "reply": "{}",
                "session_id": "pi-session",
                "token_usage": {},
            },
        )

    PiAgentClient(
        base_url="http://agent.test",
        api_key="agent-key",
        agent_id="pi-agent",
        transport=httpx.MockTransport(handler),
    ).chat("hello", capability_token="opaque-grant")
    assert observed["body"]["nexus_capability_token"] == "opaque-grant"


def test_capability_preview_expand_and_match_entities(
    tmp_path: Path, monkeypatch
) -> None:
    old_path, old_url = settings.db_path, settings.database_url
    old_upload = settings.upload_dir
    path = tmp_path / "agent-read-tools.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    settings.upload_dir = tmp_path / "uploads"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        settings, "agent_capability_secret", "capability-secret-for-tests-32bytes"
    )
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(
        "app.api.documents.run_knowledge_pipeline",
        lambda *args, **kwargs: {"chunk_count": 0, "summary": ""},
    )
    monkeypatch.setattr(
        "app.services.hybrid_retrieval._vector_branch",
        lambda *args, **kwargs: ({}, []),
    )
    try:
        with TestClient(app) as client:
            login = bootstrap_admin_response(client)
            assert login.status_code == 200
            admin_user_id = login.json()["user"]["id"]
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            first = client.post(
                "/knowledge-spaces",
                headers=headers,
                json={"name": "授权空间", "description": "visible"},
            )
            second = client.post(
                "/knowledge-spaces",
                headers=headers,
                json={"name": "未授权空间", "description": "hidden"},
            )
            assert first.status_code == 201, first.text
            assert second.status_code == 201, second.text
            first_id = first.json()["id"]
            second_id = second.json()["id"]
            reader = client.post(
                "/admin/users",
                headers=headers,
                json={
                    "username": "document-reader",
                    "email": "document-reader@example.com",
                    "display_name": "资料读者",
                    "password": "Reader-password-123",
                },
            )
            assert reader.status_code == 201, reader.text
            reader_id = reader.json()["user_id"]
            membership = client.post(
                f"/knowledge-spaces/{first_id}/members",
                headers=headers,
                json={"user_id": reader_id, "role": "reader"},
            )
            assert membership.status_code == 201, membership.text
            body = (
                "第一章 投标人资格条件。\n\n"
                "投标人须具备建筑工程施工总承包一级资质。"
                "项目经理应具有类似工程业绩，并提供近三年合同和验收文件作为证明材料。"
                "联合体投标时，牵头方须满足全部资格条件。\n\n"
                "第二章 技术要求。\n\n"
                "系统须支持知识检索与引用核验，并能按章节输出可追溯的投标响应。"
                "技术方案应覆盖实施计划、质量保障、培训交付和售后服务。\n\n"
            ) * 30
            created = client.post(
                f"/knowledge-spaces/{first_id}/documents",
                headers=headers,
                files={"file": ("bid.txt", body.encode("utf-8"), "text/plain")},
                data={"name": "招标文件"},
            )
            assert created.status_code == 201, created.text
            document = created.json()["document"]
            version_id = document["latest_version"]["id"]
            original_upload = datetime(2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
            later_version = datetime(2025, 6, 7, 8, 9, 10, tzinfo=timezone.utc)
            attribution = {
                "created_by_user_id": admin_user_id,
                "created_by_display_name": "原始上传人",
                "created_at": "2020-01-02T03:04:05+00:00",
            }
            with transaction() as db:
                uploader = db.get(User, admin_user_id)
                assert uploader is not None
                uploader.display_name = "原始上传人"
                uploader_username = uploader.username
                stored_document = db.get(Document, document["id"])
                stored_version = db.get(DocumentVersion, version_id)
                assert stored_document is not None and stored_version is not None
                stored_document.created_at = original_upload
                stored_document.updated_at = later_version
                stored_version.created_at = later_version
                stored_version.updated_at = later_version
                db.execute(
                    text(
                        "UPDATE document_versions SET processing_stage = 'ready', "
                        "queryable = 1, is_current_published = 1 WHERE id = :id"
                    ),
                    {"id": version_id},
                )
                stamp = now()
                qualification = KnowledgeEntity(
                    id=f"ke_{new_token(8)}",
                    space_id=first_id,
                    canonical_name="投标人资格",
                    entity_type="regulation",
                    description="招标文件中的资格条件",
                    aliases_json='["资质"]',
                    confidence=0.95,
                    status="active",
                    created_at=stamp,
                    updated_at=stamp,
                )
                org = KnowledgeEntity(
                    id=f"ke_{new_token(8)}",
                    space_id=first_id,
                    canonical_name="施工总承包",
                    entity_type="organization",
                    description="承包资质类别",
                    confidence=0.8,
                    status="active",
                    created_at=stamp,
                    updated_at=stamp,
                )
                manager = KnowledgeEntity(
                    id=f"ke_{new_token(8)}",
                    space_id=first_id,
                    canonical_name="项目经理",
                    entity_type="person",
                    description="投标项目负责人",
                    confidence=0.7,
                    status="active",
                    created_at=stamp,
                    updated_at=stamp,
                )
                hidden = KnowledgeEntity(
                    id=f"ke_{new_token(8)}",
                    space_id=second_id,
                    canonical_name="投标人资格",
                    entity_type="regulation",
                    description="不应被授权空间看到",
                    confidence=1.0,
                    status="active",
                    created_at=stamp,
                    updated_at=stamp,
                )
                db.add_all([qualification, org, manager, hidden])
                db.flush()
                db.add_all(
                    [
                        KnowledgeRelation(
                            id=f"kr_{new_token(8)}",
                            space_id=first_id,
                            subject_entity_id=qualification.id,
                            object_entity_id=org.id,
                            relation_type="related_to",
                            description="资格条件引用承包资质",
                            confidence=0.7,
                            created_at=stamp,
                        ),
                        KnowledgeRelation(
                            id=f"kr_{new_token(8)}",
                            space_id=first_id,
                            subject_entity_id=qualification.id,
                            object_entity_id=manager.id,
                            relation_type="related_to",
                            description="资格条件要求项目经理",
                            confidence=0.6,
                            created_at=stamp,
                        ),
                    ]
                )
            token = create_capability_token(
                user_id=reader_id,
                agent_id=PI_RUNTIME_AGENT_ID,
                space_ids=[first_id],
                space_mode="selected",
                allowed_tools=[
                    "list_documents",
                    "search_evidence",
                    "preview_document",
                    "expand_citation",
                    "match_entities",
                ],
            )
            capability_headers = {
                "Authorization": f"Bearer {token}",
                "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
            }

            listed = client.post(
                "/internal/v1/agent-capabilities/list_documents",
                headers=capability_headers,
                json={"arguments": {"space_id": first_id}},
            )
            assert listed.status_code == 200, listed.text
            listed_items = listed.json()["inline"]["items"]
            assert [item["id"] for item in listed_items] == [document["id"]]
            assert {key: listed_items[0][key] for key in attribution} == attribution

            searched = client.post(
                "/internal/v1/agent-capabilities/search_evidence",
                headers=capability_headers,
                json={"arguments": {"query": "招标文件", "space_ids": [first_id]}},
            )
            assert searched.status_code == 200, searched.text
            evidence = searched.json()["inline"]["evidence"]
            assert {item["document_id"] for item in evidence} == {document["id"]}
            for item in evidence:
                assert {key: item[key] for key in attribution} == attribution

            preview = client.post(
                "/internal/v1/agent-capabilities/preview_document",
                headers=capability_headers,
                json={"arguments": {"document_id": document["id"], "max_chars": 200}},
            )
            assert preview.status_code == 200, preview.text
            inline = preview.json()["inline"]
            assert {key: inline[key] for key in attribution} == attribution
            assert inline["truncated"] is True
            assert inline["untrusted"] is True
            assert inline["content"].startswith("第一章")
            continued = client.post(
                "/internal/v1/agent-capabilities/preview_document",
                headers=capability_headers,
                json={
                    "arguments": {
                        "document_id": document["id"],
                        "offset": inline["next_offset"],
                        "max_chars": 200,
                    }
                },
            )
            assert continued.status_code == 200, continued.text
            assert continued.json()["inline"]["content"]
            assert continued.json()["inline"]["content"] != inline["content"]

            span_start = body.find("建筑工程施工总承包一级资质")
            span_end = span_start + len("建筑工程施工总承包一级资质")
            expanded = client.post(
                "/internal/v1/agent-capabilities/expand_citation",
                headers=capability_headers,
                json={
                    "arguments": {
                        "citation_id": (
                            f"writing-evidence:{version_id}:{inline['text_sha256']}:"
                            f"{span_start}-{span_end}"
                        ),
                        "before": 20,
                        "after": 20,
                    }
                },
            )
            assert expanded.status_code == 200, expanded.text
            expanded_inline = expanded.json()["inline"]
            assert {key: expanded_inline[key] for key in attribution} == attribution
            for item in [*listed_items, *evidence, inline, expanded_inline]:
                assert not {"email", "username", "api_key", "password_hash", "users"} & item.keys()
            excerpt = expanded.json()["inline"]["excerpt"]
            assert "建筑工程施工总承包一级资质" in excerpt
            assert "投标人须具备" in excerpt

            matched = client.post(
                "/internal/v1/agent-capabilities/match_entities",
                headers=capability_headers,
                json={
                    "arguments": {
                        "space_id": first_id,
                        "query": "资质",
                        "max_hops": 1,
                    }
                },
            )
            assert matched.status_code == 200, matched.text
            graph = matched.json()["inline"]
            names = {item["canonical_name"] for item in graph["entities"]}
            assert "投标人资格" in names
            assert "施工总承包" in names
            related_names = {item["canonical_name"] for item in graph["related"]}
            assert "项目经理" in related_names
            assert graph["relations"]

            denied_space = client.post(
                "/internal/v1/agent-capabilities/match_entities",
                headers=capability_headers,
                json={"arguments": {"space_id": second_id, "query": "资格"}},
            )
            assert denied_space.status_code == 403

            hidden_doc = client.post(
                f"/knowledge-spaces/{second_id}/documents",
                headers=headers,
                files={"file": ("secret.txt", b"secret", "text/plain")},
                data={"name": "机密"},
            )
            assert hidden_doc.status_code == 201, hidden_doc.text
            hidden_document = hidden_doc.json()["document"]
            denied_arguments = {
                "list_documents": {"space_id": second_id},
                "search_evidence": {
                    "query": "secret",
                    "document_ids": [hidden_document["id"]],
                },
                "preview_document": {"document_id": hidden_document["id"]},
                "expand_citation": {
                    "document_version_id": hidden_document["latest_version"]["id"],
                },
            }
            for tool, arguments in denied_arguments.items():
                denied = client.post(
                    f"/internal/v1/agent-capabilities/{tool}",
                    headers=capability_headers,
                    json={"arguments": arguments},
                )
                assert denied.status_code == 403, denied.text
                assert "created_by_user_id" not in denied.text

            with transaction() as db:
                uploader = db.get(User, admin_user_id)
                assert uploader is not None
                uploader.display_name = ""
            fallback = client.post(
                "/internal/v1/agent-capabilities/preview_document",
                headers=capability_headers,
                json={"arguments": {"document_id": document["id"]}},
            )
            assert fallback.status_code == 200, fallback.text
            assert {
                key: fallback.json()["inline"][key] for key in attribution
            } == {**attribution, "created_by_display_name": uploader_username}

            # 定时任务只选文档时，补齐鉴权空间不能放宽到同空间其他文档。
            sibling = client.post(
                f"/knowledge-spaces/{first_id}/documents",
                headers=headers,
                files={"file": ("other.txt", "另一份资质证书：编号 OTHER-2".encode(), "text/plain")},
                data={"name": "另一份资质"},
            )
            assert sibling.status_code == 201, sibling.text
            sibling_document = sibling.json()["document"]
            sibling_version = sibling_document["latest_version"]["id"]
            from app.services.schedule_access import (
                schedule_scope_space_ids,
                validate_schedule_scope,
            )

            document_only_scope = {
                "space_ids": [],
                "document_ids": [document["id"]],
            }
            with transaction() as db:
                db.execute(
                    text("UPDATE document_versions SET processing_stage='ready', queryable=1, is_current_published=1 WHERE id=:id"),
                    {"id": sibling_version},
                )
                validate_schedule_scope(db, reader_id, document_only_scope)
                selected_space_ids = schedule_scope_space_ids(db, document_only_scope)
            restricted_headers = {
                **capability_headers,
                "Authorization": "Bearer " + create_capability_token(
                    user_id=reader_id, agent_id=PI_RUNTIME_AGENT_ID,
                    space_ids=selected_space_ids,
                    document_ids=document_only_scope["document_ids"],
                ),
            }
            listed_selected = client.post(
                "/internal/v1/agent-capabilities/list_documents",
                headers=restricted_headers, json={"arguments": {"space_id": first_id}},
            )
            assert [row["id"] for row in listed_selected.json()["inline"]["items"]] == [document["id"]]
            searched_selected = client.post(
                "/internal/v1/agent-capabilities/search_evidence",
                headers=restricted_headers,
                json={"arguments": {"query": "资质"}},
            )
            assert searched_selected.status_code == 200, searched_selected.text
            assert {
                row["document_id"]
                for row in searched_selected.json()["inline"]["evidence"]
            } == {document["id"]}
            for tool, arguments in {
                "search_evidence": {"query": "资质", "document_ids": [sibling_document["id"]]},
                "preview_document": {"document_id": sibling_document["id"]},
                "expand_citation": {"document_version_id": sibling_version},
            }.items():
                escaped = client.post(
                    f"/internal/v1/agent-capabilities/{tool}",
                    headers=restricted_headers, json={"arguments": arguments},
                )
                assert escaped.status_code == 403, escaped.text

            inventory = client.post(
                "/internal/v1/agent-capabilities/search_evidence",
                headers=capability_headers,
                json={"arguments": {"query": "列出所有资质文档完整清单", "top_k": 1}},
            )
            assert inventory.status_code == 200, inventory.text
            inventory_result = inventory.json()["inline"]
            assert {row["document_id"] for row in inventory_result["evidence"]} == {
                document["id"], sibling_document["id"],
            }
            assert inventory_result["coverage"]["expected"] == 2
            assert inventory_result["coverage"]["complete"] is True
            from app.services.evidence_inventory import inventory_evidence

            with transaction() as db:
                initial_inventory = inventory_evidence(
                    db, space_ids=[first_id], document_ids=None,
                    query="列出所有资质文档完整清单", chapter_id="ch-source",
                    max_chars=4000,
                )
            assert initial_inventory["next_cursor"]
            continuation = client.post(
                "/internal/v1/agent-capabilities/search_evidence",
                headers=capability_headers,
                json={"arguments": {
                    "query": "列出所有资质文档完整清单",
                    "chapter_id": "ch-source",
                    "cursor": initial_inventory["next_cursor"],
                }},
            )
            assert continuation.status_code == 200, continuation.text
            assert continuation.json()["inline"]["coverage"]["complete"] is True
            wrong_chapter = client.post(
                "/internal/v1/agent-capabilities/search_evidence",
                headers=capability_headers,
                json={"arguments": {
                    "query": "列出所有资质文档完整清单",
                    "chapter_id": "ch-other",
                    "cursor": initial_inventory["next_cursor"],
                }},
            )
            assert wrong_chapter.status_code == 403, wrong_chapter.text
            invalid_cursor = client.post(
                "/internal/v1/agent-capabilities/search_evidence",
                headers=capability_headers,
                json={"arguments": {"query": "列出所有资质文档完整清单", "cursor": "not-a-issued-cursor"}},
            )
            assert invalid_cursor.status_code == 403, invalid_cursor.text
            exhausted_headers = {
                **capability_headers,
                "Authorization": "Bearer " + create_capability_token(
                    user_id=reader_id, agent_id=PI_RUNTIME_AGENT_ID,
                    space_ids=[first_id], evidence_max_bytes=0,
                ),
            }
            exhausted = client.post(
                "/internal/v1/agent-capabilities/search_evidence",
                headers=exhausted_headers,
                json={"arguments": {"query": "列出所有资质文档完整清单", "max_bytes": 1_000_000}},
            )
            assert exhausted.status_code == 403, exhausted.text
            ordinary = client.post(
                "/internal/v1/agent-capabilities/list_documents",
                headers=exhausted_headers, json={"arguments": {"space_id": first_id}},
            )
            assert ordinary.status_code == 200, ordinary.text
            assert {row["id"] for row in ordinary.json()["inline"]["items"]} == {
                document["id"], sibling_document["id"],
            }
            accessible_exhausted_headers = {
                **capability_headers,
                "Authorization": "Bearer " + create_capability_token(
                    user_id=reader_id, agent_id=PI_RUNTIME_AGENT_ID,
                    space_mode="accessible", evidence_max_bytes=0,
                ),
            }
            accessible_ordinary = client.post(
                "/internal/v1/agent-capabilities/list_spaces",
                headers=accessible_exhausted_headers, json={"arguments": {}},
            )
            assert accessible_ordinary.status_code == 200, accessible_ordinary.text
            assert {row["id"] for row in accessible_ordinary.json()["inline"]["items"]} == {first_id}
            accessible_inventory = client.post(
                "/internal/v1/agent-capabilities/search_evidence",
                headers=accessible_exhausted_headers,
                json={"arguments": {"query": "列出所有资质文档完整清单"}},
            )
            assert accessible_inventory.status_code == 403, accessible_inventory.text
            with pytest.raises(ValueError):
                create_capability_token(
                    user_id=reader_id, agent_id=PI_RUNTIME_AGENT_ID,
                    evidence_max_bytes=-1,
                )
            for selected_ids in ([], [document["id"]]):
                scoped_headers = {
                    **capability_headers,
                    "Authorization": "Bearer " + create_capability_token(
                        user_id=reader_id, agent_id=PI_RUNTIME_AGENT_ID,
                        space_ids=[first_id], document_ids=selected_ids,
                    ),
                }
                scoped_inventory = client.post(
                    "/internal/v1/agent-capabilities/search_evidence",
                    headers=scoped_headers,
                    json={"arguments": {"query": "列出所有资质文档完整清单"}},
                )
                assert scoped_inventory.status_code == 200, scoped_inventory.text
                assert {row["document_id"] for row in scoped_inventory.json()["inline"]["evidence"]} == set(selected_ids)
                if not selected_ids:
                    empty_list = client.post(
                        "/internal/v1/agent-capabilities/list_documents",
                        headers=scoped_headers, json={"arguments": {"space_id": first_id}},
                    )
                    assert empty_list.json()["inline"]["items"] == []
                    empty_read = client.post(
                        "/internal/v1/agent-capabilities/preview_document",
                        headers=scoped_headers, json={"arguments": {"document_id": document["id"]}},
                    )
                    assert empty_read.status_code == 403

            # Citation expansion uses the saved exact text, not a later extraction.
            monkeypatch.setattr(
                "app.services.agent_capability._resolve_version_text",
                lambda _version: "替换后的提取文本，不再包含原始资质",
            )
            pinned = client.post(
                "/internal/v1/agent-capabilities/expand_citation",
                headers=restricted_headers,
                json={"arguments": {"citation_id": expanded_inline["citation_id"], "before": 0, "after": 0}},
            )
            assert pinned.status_code == 200, pinned.text
            assert pinned.json()["inline"]["excerpt"] == body[span_start:span_end]
            legacy = client.post(
                "/internal/v1/agent-capabilities/expand_citation",
                headers=restricted_headers,
                json={"arguments": {"citation_id": f"writing-evidence:{version_id}:{span_start}-{span_end}"}},
            )
            assert legacy.status_code == 200, legacy.text
            assert legacy.json()["inline"]["source_state"] == "stale"
            assert legacy.json()["inline"]["excerpt"] == ""

            with transaction() as db:
                db.execute(
                    text("DELETE FROM space_members WHERE space_id=:space_id AND user_id=:user_id"),
                    {"space_id": first_id, "user_id": reader_id},
                )
            for tool, arguments in {
                "list_documents": {"space_id": first_id},
                "search_evidence": {"query": "完整清单"},
                "preview_document": {"document_id": document["id"]},
                "expand_citation": {"citation_id": expanded_inline["citation_id"]},
            }.items():
                revoked = client.post(
                    f"/internal/v1/agent-capabilities/{tool}",
                    headers=restricted_headers, json={"arguments": arguments},
                )
                assert revoked.status_code in {403, 404}, revoked.text
                assert "inline" not in revoked.json()
    finally:
        database.reset_database(path)
        settings.db_path, settings.database_url = old_path, old_url
        settings.upload_dir = old_upload


def test_capability_rename_chat_session_is_owner_scoped(
    tmp_path: Path, monkeypatch
) -> None:
    """A chat grant renames only the caller's own titles.

    The grant here is minted without an explicit tool list, so this also pins
    that the default chat grant carries the tool at all: a skill that cannot
    call it cannot normalise anything.
    """
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "agent-capabilities-rename.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(
        settings, "agent_capability_secret", "capability-secret-for-tests-32bytes"
    )
    monkeypatch.setattr(settings, "environment", "test")
    try:
        with TestClient(app) as client:
            headers, admin_user_id = _admin_login(client)
            created = client.post(
                "/chat/sessions", headers=headers, json={"title": "旧标题"}
            )
            assert created.status_code == 201, created.text
            session_id = created.json()["id"]
            second_created = client.post(
                "/chat/sessions", headers=headers, json={"title": "第二旧标题"}
            )
            assert second_created.status_code == 201, second_created.text
            second_session_id = second_created.json()["id"]
            intruder = client.post(
                "/admin/users",
                headers=headers,
                json={
                    "username": "rename-intruder",
                    "email": "rename-intruder@example.com",
                    "display_name": "其他用户",
                    "password": "Intruder-password-123",
                },
            )
            assert intruder.status_code == 201, intruder.text
            intruder_id = intruder.json()["user_id"]
            stamp = now()
            with transaction() as db:
                db.add(
                    ChatSession(
                        id="foreign-session",
                        owner_user_id=intruder_id,
                        title="别人的会话",
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
            token = create_capability_token(
                user_id=admin_user_id,
                agent_id=PI_RUNTIME_AGENT_ID,
                conversation_id=session_id,
            )
            capability_headers = {
                "Authorization": f"Bearer {token}",
                "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
            }
            call = "/internal/v1/agent-capabilities/rename_chat_session"

            # No session_id: the bound conversation is the default target.
            renamed = client.post(
                call,
                headers=capability_headers,
                json={"arguments": {"title": "20260916-证据-引用"}},
            )
            assert renamed.status_code == 200, renamed.text
            inline = renamed.json()["inline"]
            assert inline["id"] == session_id
            assert inline["title"] == "20260916-证据-引用"
            assert inline["previous_title"] == "旧标题"
            detail = client.get(f"/chat/sessions/{session_id}", headers=headers)
            assert detail.json()["title"] == "20260916-证据-引用"

            # An explicit id can rename a different session owned by the same user.
            renamed_second = client.post(
                call,
                headers=capability_headers,
                json={
                    "arguments": {
                        "session_id": second_session_id,
                        "title": "20260916-证据-第二会话",
                    }
                },
            )
            assert renamed_second.status_code == 200, renamed_second.text
            assert renamed_second.json()["inline"] == {
                "id": second_session_id,
                "title": "20260916-证据-第二会话",
                "previous_title": "第二旧标题",
            }
            second_detail = client.get(
                f"/chat/sessions/{second_session_id}", headers=headers
            )
            assert second_detail.json()["title"] == "20260916-证据-第二会话"

            # External channel grants bind a runtime id, so omission is explicit-only.
            external_token = create_capability_token(
                user_id=admin_user_id,
                agent_id=PI_RUNTIME_AGENT_ID,
                conversation_id=f"pi-open-{admin_user_id}-external",
            )
            external_headers = {
                "Authorization": f"Bearer {external_token}",
                "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
            }
            external_default = client.post(
                call,
                headers=external_headers,
                json={"arguments": {"title": "不应默认改名"}},
            )
            assert external_default.status_code == 403, external_default.text
            assert "session_id is required" in external_default.text
            external_explicit = client.post(
                call,
                headers=external_headers,
                json={
                    "arguments": {
                        "session_id": second_session_id,
                        "title": "20260916-证据-外部显式",
                    }
                },
            )
            assert external_explicit.status_code == 200, external_explicit.text

            # Another user's session stays untouched even when named explicitly.
            foreign = client.post(
                call,
                headers=capability_headers,
                json={
                    "arguments": {
                        "session_id": "foreign-session",
                        "title": "劫持标题",
                    }
                },
            )
            assert foreign.status_code == 403, foreign.text
            with transaction() as db:
                row = db.get(ChatSession, "foreign-session")
                assert row is not None and row.title == "别人的会话"

            for arguments in (
                {"title": "   "},
                {"title": "标" * 256},
                {"session_id": "missing-session", "title": "无主会话"},
            ):
                rejected = client.post(
                    call, headers=capability_headers, json={"arguments": arguments}
                )
                assert rejected.status_code == 403, (arguments, rejected.text)
            with transaction() as db:
                kept = db.get(ChatSession, session_id)
                kept_second = db.get(ChatSession, second_session_id)
                assert kept is not None
                assert kept.title == "20260916-证据-引用"
                assert kept.title_finalized is True
                assert kept_second is not None
                assert kept_second.title == "20260916-证据-外部显式"
                assert kept_second.title_finalized is True
    finally:
        settings.db_path, settings.database_url = old_path, old_url


@pytest.mark.parametrize(
    ("filename", "warning", "expected_state"),
    [
        ("source.txt", None, "uncertain"),
        ("source.pdf", None, "uncertain"),
        ("source.jpg", "image_vision_uncertain", "supported"),
        ("source.jpg", "image_vision_unclear", "uncertain"),
    ],
)
def test_vector_citations_require_exact_version_and_full_source_text(
    tmp_path: Path, monkeypatch, filename, warning, expected_state,
) -> None:
    from types import SimpleNamespace

    from app.services.agent_capability import _vector_evidence_items
    from app.services.evidence_snapshots import parse_citation_id, read_snapshot

    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    header = "视觉模型解析，未经核验"
    source = header + "\n\n【可见文字】\n原文备注：视觉模型解析，未经核验\n" + "正式内容" * 600 + "\n"
    version = SimpleNamespace(
        id="dv_vector_snapshot", version_number=1, source_relative_path=filename,
        extraction_method="vision", extraction_warning_code=warning,
    )
    document = SimpleNamespace(id="doc_vector_snapshot", name="资质", space_id="space")
    monkeypatch.setattr(
        "app.services.agent_capability._resolve_version_text", lambda _version: source
    )
    bundle = SimpleNamespace(
        documents=[(document, version)],
        vector_hits=[
            {
                "id": "stale-text", "document_id": document.id,
                "document_version_id": version.id,
                "document": source[:2200] + "已经改变的后半段",
            },
            {
                "id": "wrong-version", "document_id": document.id,
                "document_version_id": "dv_other", "document": source,
            },
            {
                "id": "exact", "document_id": document.id,
                "document_version_id": version.id, "document": source,
            },
        ],
    )
    evidence = _vector_evidence_items(bundle, limit=8)
    assert len(evidence) == 1
    item = evidence[0]
    expected = (
        " " * len(header) + source[len(header):2200]
        if filename.endswith(".jpg") else source[:2200]
    )
    assert item["excerpt"] == expected
    assert item["support_state"] == expected_state
    assert "原文备注：视觉模型解析，未经核验" in item["excerpt"]
    parsed = parse_citation_id(item["citation_id"])
    assert parsed is not None
    version_id, digest, start, end = parsed
    assert digest is not None
    frozen = read_snapshot(version_id, digest)
    assert frozen is not None
    assert frozen == source
    assert (start, end) == (0, 2200)


def test_auto_history_recall_is_bound_to_own_committed_turn(
    tmp_path: Path, monkeypatch
) -> None:
    from app.models.chat import ChatMessage

    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "history-recall.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "agent_capability_secret", "capability-secret-for-tests-32bytes")
    monkeypatch.setattr(settings, "qa_vector_enabled", False)
    try:
        with TestClient(app) as client:
            _, owner_id = _admin_login(client)
            stamp = datetime.now(timezone.utc)
            with transaction() as db:
                db.add_all([
                    ChatSession(id="history-old", owner_user_id=owner_id, title="JEV",
                                created_at=stamp, updated_at=stamp),
                    ChatSession(id="history-new", owner_user_id=owner_id, title="新问题",
                                created_at=stamp, updated_at=stamp),
                ])
                db.flush()
                db.add_all([
                    ChatMessage(id="history-jev", session_id="history-old",
                                owner_user_id=owner_id, role="user",
                                content="JEV 指日本脑炎病毒", created_at=stamp),
                    ChatMessage(id="history-current", session_id="history-new",
                                owner_user_id=owner_id, role="user",
                                content="JEV 是什么？", created_at=stamp),
                ])
            endpoint = "/internal/v1/agent-capabilities/history/recall"
            token = create_capability_token(
                user_id=owner_id, agent_id=PI_RUNTIME_AGENT_ID,
                conversation_id="history-new", history_turn_message_id="history-current",
                personal_data_scope="own", allowed_tools=["search_my_chat_history"],
            )
            headers = {
                "Authorization": f"Bearer {token}",
                "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
            }
            response = client.post(endpoint, headers=headers)
            assert response.status_code == 200, response.text
            assert [item["message_id"] for item in response.json()["items"]] == ["history-jev"]
            assert client.post(endpoint, headers={**headers, "X-Nexus-Agent-ID": "other"}).status_code == 403
            missing_turn = create_capability_token(
                user_id=owner_id, agent_id=PI_RUNTIME_AGENT_ID,
                conversation_id="history-new", personal_data_scope="own",
                allowed_tools=["search_my_chat_history"],
            )
            assert client.post(endpoint, headers={
                **headers, "Authorization": f"Bearer {missing_turn}",
            }).status_code == 403
            wrong_session = create_capability_token(
                user_id=owner_id, agent_id=PI_RUNTIME_AGENT_ID,
                conversation_id="history-old", history_turn_message_id="history-current",
                personal_data_scope="own", allowed_tools=["search_my_chat_history"],
            )
            assert client.post(endpoint, headers={
                **headers, "Authorization": f"Bearer {wrong_session}",
            }).json()["items"] == []
    finally:
        database.reset_database(path)
        settings.db_path, settings.database_url = old_path, old_url


def test_history_search_result_can_be_read_with_own_summary_scope(
    tmp_path: Path, monkeypatch
) -> None:
    from app.models.chat import ChatMessage

    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "history-summary.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "agent_capability_secret", "capability-secret-for-tests-32bytes")
    monkeypatch.setattr(settings, "qa_vector_enabled", False)
    try:
        with TestClient(app) as client:
            _, owner_id = _admin_login(client)
            stamp = now()
            with transaction() as db:
                db.add(User(
                    id="summary-other", username="summary-other", display_name="其他用户",
                    password_hash="unused", created_at=stamp,
                ))
                db.flush()
                db.add_all([
                    ChatSession(id="summary-current", owner_user_id=owner_id, title="当前",
                                created_at=stamp, updated_at=stamp),
                    ChatSession(id="summary-old", owner_user_id=owner_id, title="咖啡",
                                created_at=stamp, updated_at=stamp),
                    ChatSession(id="summary-private", owner_user_id="summary-other", title="私密",
                                created_at=stamp, updated_at=stamp),
                ])
                db.flush()
                db.add_all([
                    ChatMessage(id="summary-question", session_id="summary-old",
                                owner_user_id=owner_id, role="user",
                                content="拿铁和低密度脂蛋白有什么关系？", created_at=stamp),
                    ChatMessage(id="summary-answer", session_id="summary-old",
                                owner_user_id=owner_id, role="assistant",
                                content="需要结合牛奶和咖啡制作方式分析。",
                                created_at=stamp + timedelta(seconds=1)),
                ])

            def headers(scope="own", *, chat_history=False, tools=None):
                token = create_capability_token(
                    user_id=owner_id, agent_id=PI_RUNTIME_AGENT_ID,
                    conversation_id="summary-current", personal_data_scope=scope,
                    chat_history=chat_history,
                    allowed_tools=tools if tools is not None else (
                        ["search_my_chat_history", "get_chat_session_summary"]
                        if scope == "own" else ["get_chat_session_summary"]
                    ),
                )
                return {"Authorization": f"Bearer {token}", "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID}

            base = "/internal/v1/agent-capabilities/"
            own_headers = headers()
            search = client.post(base + "search_my_chat_history", headers=own_headers,
                                 json={"arguments": {"query": "拿铁"}})
            assert search.status_code == 200, search.text
            assert [item["session_id"] for item in search.json()["inline"]["items"]] == ["summary-old"]
            target = search.json()["inline"]["items"][0]["session_id"]
            summary = client.post(base + "get_chat_session_summary", headers=own_headers,
                                  json={"arguments": {"session_id": target}})
            assert summary.status_code == 200, summary.text
            assert [item["content"] for item in summary.json()["inline"]["messages"]] == [
                "拿铁和低密度脂蛋白有什么关系？", "需要结合牛奶和咖啡制作方式分析。",
            ]
            endpoint = base + "get_chat_session_summary"
            # 即使本人是管理员，也不能通过个人工具读取他人会话。
            assert client.post(endpoint, headers=own_headers, json={
                "arguments": {"session_id": "summary-private"},
            }).status_code == 403
            assert client.post(endpoint, headers=headers("none"), json={
                "arguments": {"session_id": target},
            }).status_code == 403
            current = client.post(endpoint, headers=headers("none"), json={"arguments": {}})
            assert current.status_code == 200, current.text
            assert current.json()["inline"]["id"] == "summary-current"
            assert client.post(endpoint, headers=headers(tools=["search_my_chat_history"]), json={
                "arguments": {"session_id": target},
            }).status_code == 403
            legacy = client.post(endpoint, headers=headers("none", chat_history=True), json={
                "arguments": {"session_id": target},
            })
            assert legacy.status_code == 200, legacy.text
    finally:
        database.reset_database(path)
        settings.db_path, settings.database_url = old_path, old_url


@pytest.fixture
def presentation_scope(project_context_scope, tmp_path, monkeypatch):
    from io import BytesIO
    from pptx import Presentation

    client, owner_id, editor_id, space_id = project_context_scope
    upload = tmp_path / "uploads"
    upload.mkdir()
    monkeypatch.setattr(settings, "upload_dir", upload)
    original = upload / "original.pptx"
    stream = BytesIO()
    Presentation().save(stream)
    original.write_bytes(stream.getvalue())
    with transaction() as db:
        stamp = now()
        db.add(ChatSession(
            id="presentation-session", owner_user_id=owner_id, title="PPT",
            created_at=stamp, updated_at=stamp,
        ))
        db.flush()
        db.add(Document(
            id="presentation-document", name="可编辑无后缀的标题", status="active",
            is_temporary=True, chat_session_id="presentation-session",
            created_by_user_id=owner_id, expires_at=stamp + timedelta(days=1),
            created_at=stamp, updated_at=stamp,
        ))
        db.flush()
        db.add(DocumentVersion(
            id="presentation-version", document_id="presentation-document",
            version_number=1, processing_stage="failed", queryable=False,
            source_relative_path="用户原件.pptx", file_path=str(original),
            created_at=stamp, updated_at=stamp,
        ))
    token_args = {
        "user_id": owner_id, "agent_id": PI_RUNTIME_AGENT_ID,
        "conversation_id": "presentation-session",
        "space_ids": [space_id], "document_ids": ["presentation-document"],
        "allowed_tools": ["read_presentation"],
    }
    yield client, token_args, original, editor_id, space_id


def _read_presentation(client, token_args, **arguments):
    return client.post(
        "/internal/v1/agent-capabilities/read_presentation",
        headers={
            "Authorization": "Bearer " + create_capability_token(**token_args),
            "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
        },
        json={"arguments": {"document_id": "presentation-document", **arguments}},
    )


def test_read_presentation_delivers_exact_unindexed_original_without_template(presentation_scope):
    import hashlib

    client, token_args, original, _, _ = presentation_scope
    with transaction() as db:
        before = db.execute(text("SELECT count(*) FROM writing_doc_templates")).scalar()
    response = _read_presentation(client, token_args)
    assert response.status_code == 200, response.text
    assert response.content == original.read_bytes()
    assert response.headers["X-Nexus-SHA256"] == hashlib.sha256(original.read_bytes()).hexdigest()
    assert response.headers["X-Nexus-Document-ID"] == "presentation-document"
    assert response.headers["X-Nexus-Version-ID"] == "presentation-version"
    assert response.headers["X-Nexus-Source-Format"] == "pptx"
    assert str(original) not in str(response.headers)
    with transaction() as db:
        assert db.execute(text("SELECT count(*) FROM writing_doc_templates")).scalar() == before
        assert db.get(DocumentVersion, "presentation-version").processing_stage == "failed"


@pytest.mark.parametrize("case", [
    "unselected", "unbounded", "owner", "session", "session_owner", "missing_session",
    "deleted", "expired", "cleanup", "wrong_version", "missing_version", "missing_source",
    "unsupported", "revoked_acl", "wrong_agent", "user_disabled",
])
def test_read_presentation_rechecks_selection_and_permissions(presentation_scope, case):
    client, token_args, original, editor_id, space_id = presentation_scope
    arguments = {}
    with transaction() as db:
        document = db.get(Document, "presentation-document")
        version = db.get(DocumentVersion, "presentation-version")
        if case == "unselected":
            token_args["document_ids"] = []
        elif case == "unbounded":
            token_args["document_ids"] = None
        elif case == "owner":
            document.created_by_user_id = editor_id
        elif case == "session":
            document.chat_session_id = None
        elif case == "session_owner":
            db.get(ChatSession, "presentation-session").owner_user_id = editor_id
        elif case == "missing_session":
            token_args["conversation_id"] = None
        elif case == "deleted":
            document.status = "deleted"
        elif case == "expired":
            document.expires_at = now() - timedelta(seconds=1)
        elif case == "cleanup":
            document.temporary_cleanup_at = now()
        elif case == "wrong_version":
            version.document_id = "context-document"
            arguments["version_id"] = version.id
        elif case == "missing_version":
            arguments["version_id"] = "missing"
        elif case == "missing_source":
            version.file_path = None
        elif case == "unsupported":
            version.source_relative_path = "original.pdf"
        elif case == "revoked_acl":
            document.is_temporary = False
            document.space_id = space_id
            token_args["user_id"] = editor_id
            db.execute(text(
                "DELETE FROM space_members WHERE space_id=:space AND user_id=:user"
            ), {"space": space_id, "user": editor_id})
        elif case == "wrong_agent":
            token_args["agent_id"] = "another-agent"
        elif case == "user_disabled":
            db.get(User, token_args["user_id"]).status = "disabled"
    response = _read_presentation(client, token_args, **arguments)
    assert response.status_code in (403, 404), response.text
    assert "X-Nexus-SHA256" not in response.headers
    assert str(original) not in response.text


@pytest.mark.parametrize("case", ["outside", "symlink", "parent_symlink", "traversal", "empty", "oversize"])
def test_presentation_original_local_boundaries(tmp_path, monkeypatch, case):
    from app.services import presentation_original as service

    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(settings, "upload_dir", root)
    original = root / "original.pptx"
    original.write_bytes(b"original")
    outside = tmp_path / "outside.pptx"
    outside.write_bytes(b"private")
    path = original
    if case == "outside":
        path = outside
    elif case == "symlink":
        path = root / "link.pptx"
        path.symlink_to(outside)
    elif case == "parent_symlink":
        (root / "linked").symlink_to(tmp_path, target_is_directory=True)
        path = root / "linked" / "outside.pptx"
    elif case == "traversal":
        path = root / ".." / "outside.pptx"
    elif case == "empty":
        original.write_bytes(b"")
    elif case == "oversize":
        monkeypatch.setattr(service, "MAX_SOURCE_BYTES", 2)
    with pytest.raises(service.PresentationOriginalError):
        with service.copy_original(str(path)):
            pytest.fail("invalid original was delivered")
    assert outside.read_bytes() == b"private"


@pytest.mark.parametrize("case", ["valid", "wrong_bucket", "truncated", "changed", "read_failure"])
def test_presentation_original_minio_snapshot_and_cleanup(monkeypatch, case):
    from io import BytesIO
    from types import SimpleNamespace
    from app.services import presentation_original as service

    stream = BytesIO(b"exact original")
    released = []
    stream.release_conn = lambda: released.append(True)
    info = SimpleNamespace(size=len(stream.getvalue()), etag="first", version_id="v1")
    calls = []
    def stat_object(**kwargs):
        calls.append(kwargs)
        if case == "changed" and len(calls) > 1:
            return SimpleNamespace(size=info.size, etag="second", version_id="v2")
        return info
    monkeypatch.setattr(service.minio_storage, "load_minio_settings", lambda: SimpleNamespace(
        bucket="documents", temp_bucket="temporary",
    ))
    monkeypatch.setattr(service.minio_storage, "stat_object", stat_object)
    monkeypatch.setattr(service.minio_storage, "open_object", lambda **_: stream)
    if case == "truncated":
        info.size += 1
    elif case == "read_failure":
        def fail(_size):
            raise OSError("private connection detail")
        stream.read = fail
    uri = f"minio://{'other' if case == 'wrong_bucket' else 'temporary'}/original.pptx"
    if case == "valid":
        with service.copy_original(uri) as (copied, size, _digest):
            assert copied.read() == b"exact original"
            assert size == len(b"exact original")
        assert copied.closed
    else:
        with pytest.raises(service.PresentationOriginalError):
            with service.copy_original(uri):
                pytest.fail("invalid original was delivered")
    if case == "wrong_bucket":
        assert calls == []
    else:
        assert stream.closed
        assert released == [True]


@pytest.mark.parametrize("change", ["cleanup", "version", "path"])
def test_read_presentation_rechecks_after_snapshot(presentation_scope, monkeypatch, change):
    from contextlib import contextmanager
    from app.api import agent_capabilities as api

    client, token_args, _original, _, _ = presentation_scope
    real_copy = api.copy_original
    copies = []

    @contextmanager
    def changed_copy(file_path):
        with real_copy(file_path) as copied:
            copies.append(copied[0])
            with transaction() as db:
                if change == "cleanup":
                    db.get(Document, "presentation-document").temporary_cleanup_at = now()
                elif change == "version":
                    old = db.get(DocumentVersion, "presentation-version")
                    db.add(DocumentVersion(
                        id="presentation-new-version", document_id="presentation-document",
                        version_number=2, processing_stage="queued", queryable=False,
                        source_relative_path=old.source_relative_path, file_path=old.file_path,
                        created_at=now(), updated_at=now(),
                    ))
                else:
                    db.get(DocumentVersion, "presentation-version").file_path = file_path + ".changed"
            yield copied

    monkeypatch.setattr(api, "copy_original", changed_copy)
    response = _read_presentation(client, token_args)
    assert response.status_code == 403, response.text
    assert copies and all(source.closed for source in copies)
    assert "X-Nexus-SHA256" not in response.headers


@pytest.mark.parametrize("source_name, stored_name, expected", [
    ("原件.PPT", "opaque", "ppt"),
    ("参考.POTX", "opaque", "potx"),
    (None, "/uploads/original.pptx", "pptx"),
    ("original.pdf", "/uploads/fake.pptx", None),
])
def test_presentation_format_uses_original_not_display_name(source_name, stored_name, expected):
    from app.services.presentation_original import PresentationOriginalError, presentation_format

    if expected is None:
        with pytest.raises(PresentationOriginalError):
            presentation_format(source_name, stored_name)
    else:
        assert presentation_format(source_name, stored_name) == expected


def test_read_presentation_selects_latest_original_not_last_indexed_version(presentation_scope):
    client, token_args, _original, _, _ = presentation_scope
    with transaction() as db:
        old = db.get(DocumentVersion, "presentation-version")
        old.processing_stage = "ready"
        old.queryable = True
        old.is_current_published = True
        db.add(DocumentVersion(
            id="presentation-latest", document_id="presentation-document",
            version_number=2, processing_stage="queued", queryable=False,
            source_relative_path=old.source_relative_path, file_path=old.file_path,
            created_at=now(), updated_at=now(),
        ))
    latest = _read_presentation(client, token_args)
    assert latest.status_code == 200, latest.text
    assert latest.headers["X-Nexus-Version-ID"] == "presentation-latest"
    pinned = _read_presentation(client, token_args, version_id="presentation-version")
    assert pinned.status_code == 200, pinned.text
    assert pinned.headers["X-Nexus-Version-ID"] == "presentation-version"


@pytest.fixture
def separated_document_scope(project_context_scope, tmp_path, monkeypatch):
    client, owner_id, editor_id, space_id = project_context_scope
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    monkeypatch.setattr(
        "app.services.hybrid_retrieval._vector_branch", lambda *args, **kwargs: ({}, [])
    )
    with transaction() as db:
        stamp = now()
        db.add(ChatSession(
            id="attachment-session", owner_user_id=editor_id, title="附件范围",
            created_at=stamp, updated_at=stamp,
        ))
        db.flush()
        for document_id, temporary in (
            ("context-document", False), ("other-knowledge", False),
            ("scoped-attachment", True), ("unselected-attachment", True),
        ):
            path = tmp_path / f"{document_id}.txt"
            path.write_text(f"资质证明：{document_id}，资质有效期至2030年。", encoding="utf-8")
            if document_id != "context-document":
                db.add(Document(
                    id=document_id, space_id=None if temporary else space_id,
                    name=document_id, status="active", created_by_user_id=editor_id,
                    is_temporary=temporary,
                    chat_session_id="attachment-session" if temporary else None,
                    expires_at=stamp + timedelta(days=1) if temporary else None,
                    created_at=stamp, updated_at=stamp,
                ))
                db.flush()
            db.add(DocumentVersion(
                id=f"{document_id}-version", document_id=document_id, version_number=1,
                processing_stage="ready", queryable=True, is_current_published=True,
                file_path=str(path), created_at=stamp, updated_at=stamp,
            ))
    token_args = {
        "user_id": editor_id, "agent_id": PI_RUNTIME_AGENT_ID,
        "conversation_id": "attachment-session", "space_mode": "accessible",
        "document_ids": None, "attachment_document_ids": ["scoped-attachment"],
        "personal_data_scope": "own",
    }
    yield client, token_args, space_id, owner_id


def _scoped_call(client, token_args, tool, **arguments):
    return client.post(
        f"/internal/v1/agent-capabilities/{tool}",
        headers={
            "Authorization": "Bearer " + create_capability_token(**token_args),
            "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID,
        },
        json={"arguments": arguments},
    )


def test_image_capability_preview_and_expansion_keep_legacy_binding(
    separated_document_scope,
):
    from app.services.evidence_snapshots import freeze_text, read_snapshot
    from app.services.document_processor import write_extracted_sidecar

    client, token_args, _space_id, _owner_id = separated_document_scope
    header = "视觉模型解析，未经核验"
    raw = header + "\n\n【可见文字】\n编号 A-001"
    with transaction() as db:
        version = db.get(DocumentVersion, "context-document-version")
        version.source_relative_path = "photo.jpg"
        version.file_path = str(Path(version.file_path).with_suffix(".jpg"))
        version.extraction_method = "vision"
        version.extraction_warning_code = "image_vision_uncertain"
        write_extracted_sidecar(version.file_path, raw, version_id=version.id)
        digest = freeze_text(version.id, raw)
    preview = _scoped_call(
        client, token_args, "preview_document", document_id="context-document",
        text_sha256=digest, offset=3, max_chars=200,
    )
    assert preview.status_code == 200, preview.text
    payload = preview.json()["inline"]
    assert payload["content"] == " " * (len(header) - 3) + raw[len(header):]
    assert payload["offset"] == 3 and payload["char_count"] == len(raw)
    expanded = _scoped_call(
        client, token_args, "expand_citation", citation_id=payload["citation_id"],
        before=0, after=0,
    )
    assert expanded.status_code == 200, expanded.text
    evidence = expanded.json()["inline"]
    assert evidence["excerpt"] == payload["content"]
    assert evidence["support_state"] == "supported"
    assert evidence["text_sha256"] == digest
    assert (evidence["char_start"], evidence["char_end"]) == (3, len(raw))
    assert read_snapshot("context-document-version", digest) == raw
    for query in ("编号", "列出所有资料完整清单"):
        found = _scoped_call(client, token_args, "search_evidence", query=query)
        assert found.status_code == 200, found.text
        image = next(
            item for item in found.json()["inline"]["evidence"]
            if item["document_id"] == "context-document"
        )
        assert image["support_state"] == "supported"
        assert "视觉模型解析，未经核验" not in image["excerpt"]
        assert "编号 A-001" in image["excerpt"]


@pytest.mark.parametrize("query", ["资质", "列出所有资质文档完整清单"])
def test_separate_attachment_scope_keeps_accessible_knowledge(separated_document_scope, query):
    client, token_args, space_id, _owner_id = separated_document_scope
    response = _scoped_call(client, token_args, "search_evidence", query=query, top_k=8)
    assert response.status_code == 200, response.text
    result = response.json()["inline"]
    assert {item["document_id"] for item in result["evidence"]} == {
        "context-document", "other-knowledge", "scoped-attachment",
    }
    assert result["scope"]["space_ids"] == [space_id]
    assert result["scope"]["document_ids"] is None
    assert result["scope"]["attachment_document_ids"] == ["scoped-attachment"]
    preview = _scoped_call(
        client, token_args, "preview_document", document_id="scoped-attachment"
    )
    assert preview.status_code == 200, preview.text
    excerpt = preview.json()["inline"]
    citation = _scoped_call(
        client, token_args, "expand_citation", citation_id=excerpt["citation_id"],
        before=0, after=0,
    )
    assert citation.status_code == 200, citation.text
    assert citation.json()["inline"]["excerpt"] == excerpt["content"]
    uploads = _scoped_call(client, token_args, "list_my_uploads")
    assert uploads.status_code == 200, uploads.text
    assert {item["document_id"] for item in uploads.json()["inline"]["items"]} == {
        "context-document", "other-knowledge", "scoped-attachment",
    }


@pytest.mark.parametrize("selected", [None, [], ["context-document"]])
def test_separate_attachment_scope_preserves_selection_and_tool_narrowing(
    separated_document_scope, selected,
):
    client, token_args, space_id, _owner_id = separated_document_scope
    token_args.update(space_mode="selected", space_ids=[space_id], document_ids=selected)
    expected_knowledge = {"context-document", "other-knowledge"} if selected is None else set(selected)
    result = _scoped_call(client, token_args, "search_evidence", query="资质", top_k=8)
    assert result.status_code == 200, result.text
    assert {item["document_id"] for item in result.json()["inline"]["evidence"]} == (
        expected_knowledge | {"scoped-attachment"}
    )
    listed = _scoped_call(client, token_args, "list_documents", space_id=space_id)
    assert listed.status_code == 200, listed.text
    assert {item["id"] for item in listed.json()["inline"]["items"]} == expected_knowledge
    uploads = _scoped_call(client, token_args, "list_my_uploads", limit=1)
    seen = set()
    while True:
        assert uploads.status_code == 200, uploads.text
        page = uploads.json()["inline"]
        seen.update(item["document_id"] for item in page["items"])
        if page["next_offset"] is None:
            break
        uploads = _scoped_call(
            client, token_args, "list_my_uploads", limit=1, offset=page["next_offset"]
        )
    assert seen == expected_knowledge | {"scoped-attachment"}
    narrowed = _scoped_call(
        client, token_args, "search_evidence", query="资质", document_ids=["scoped-attachment"]
    )
    assert narrowed.status_code == 200, narrowed.text
    assert {item["document_id"] for item in narrowed.json()["inline"]["evidence"]} == {"scoped-attachment"}
    empty = _scoped_call(client, token_args, "search_evidence", query="资质", document_ids=[])
    assert empty.status_code == 200, empty.text
    assert empty.json()["inline"]["evidence"] == []
    assert empty.json()["inline"]["empty_reason"] == "empty_authorized_scope"
    denied_ids = ["unselected-attachment"] + ([] if selected is None else ["other-knowledge"])
    for document_id in denied_ids:
        for tool, arguments in (
            ("search_evidence", {"query": "资质", "document_ids": [document_id]}),
            ("preview_document", {"document_id": document_id}),
            ("expand_citation", {"document_version_id": f"{document_id}-version"}),
        ):
            denied = _scoped_call(client, token_args, tool, **arguments)
            assert denied.status_code == 403, denied.text


@pytest.mark.parametrize(
    "change", ["owner", "session", "session_owner", "unbound", "deleted", "expired", "cleanup", "promoted"],
)
def test_separate_attachment_scope_rechecks_live_authorization(separated_document_scope, change):
    client, token_args, space_id, owner_id = separated_document_scope
    preview = _scoped_call(client, token_args, "preview_document", document_id="scoped-attachment")
    assert preview.status_code == 200, preview.text
    citation_id = preview.json()["inline"]["citation_id"]
    with transaction() as db:
        document = db.get(Document, "scoped-attachment")
        if change == "owner":
            document.created_by_user_id = owner_id
        elif change == "session":
            document.chat_session_id = None
        elif change == "session_owner":
            db.get(ChatSession, "attachment-session").owner_user_id = owner_id
        elif change == "unbound":
            token_args["conversation_id"] = None
        elif change == "deleted":
            document.status = "deleted"
        elif change == "expired":
            document.expires_at = now() - timedelta(seconds=1)
        elif change == "cleanup":
            document.temporary_cleanup_at = now()
        else:
            document.space_id = space_id
            document.is_temporary = False
    for tool, arguments in (
        ("search_evidence", {"query": "资质"}),
        ("search_evidence", {"query": "列出所有资质文档完整清单"}),
        ("preview_document", {"document_id": "scoped-attachment"}),
        ("expand_citation", {"citation_id": citation_id}),
        ("list_documents", {"space_id": space_id}),
        ("list_my_uploads", {}),
    ):
        denied = _scoped_call(client, token_args, tool, **arguments)
        assert denied.status_code == 403, (tool, denied.text)
        assert "inline" not in denied.json()


@pytest.mark.parametrize("change", ["attachments", "knowledge_acl", "deleted", "expired"])
def test_separate_attachment_inventory_cursor_rechecks_scope(
    separated_document_scope, tmp_path, change,
):
    from app.services.evidence_inventory import inventory_evidence

    client, token_args, space_id, _owner_id = separated_document_scope
    (tmp_path / "context-document.txt").write_text("资质条款必须逐项核验。\n" * 1000, encoding="utf-8")
    query = "列出所有资质文档完整清单"
    with transaction() as db:
        first = inventory_evidence(
            db, space_ids=[space_id], document_ids=None,
            attachment_document_ids=["scoped-attachment"], query=query, max_chars=4000,
        )
    cursor = first["next_cursor"]
    assert cursor
    valid = _scoped_call(client, token_args, "search_evidence", query=query, cursor=cursor)
    assert valid.status_code == 200, valid.text
    if change == "attachments":
        token_args["attachment_document_ids"] = []
    elif change == "knowledge_acl":
        with transaction() as db:
            db.execute(
                text("DELETE FROM space_members WHERE space_id=:space AND user_id=:user"),
                {"space": space_id, "user": token_args["user_id"]},
            )
    else:
        with transaction() as db:
            document = db.get(Document, "scoped-attachment")
            if change == "deleted":
                document.status = "deleted"
            else:
                document.expires_at = now() - timedelta(seconds=1)
    denied = _scoped_call(client, token_args, "search_evidence", query=query, cursor=cursor)
    assert denied.status_code in (403, 404), denied.text


def test_separate_attachment_grant_hash_detects_changes(separated_document_scope):
    import jwt
    from app.services.agent_capability import decode_capability_token

    client, token_args, _space_id, _owner_id = separated_document_scope
    claims = decode_capability_token(create_capability_token(**token_args))
    for attachments in ([], ["unselected-attachment"], None, "scoped-attachment"):
        changed = {**claims, "attachment_document_ids": attachments}
        token = jwt.encode(changed, settings.agent_capability_secret, algorithm="HS256")
        response = client.post(
            "/internal/v1/agent-capabilities/search_evidence",
            headers={"Authorization": f"Bearer {token}", "X-Nexus-Agent-ID": PI_RUNTIME_AGENT_ID},
            json={"arguments": {"query": "资质"}},
        )
        assert response.status_code == 403, response.text


@pytest.mark.parametrize("mode", ["accessible", "selected"])
def test_separate_attachment_presentation_still_requires_explicit_selection(presentation_scope, mode):
    client, token_args, original, _editor_id, space_id = presentation_scope
    token_args.update(
        space_mode=mode, document_ids=None, attachment_document_ids=["presentation-document"],
    )
    allowed = _read_presentation(client, token_args)
    assert allowed.status_code == 200, allowed.text
    assert allowed.content == original.read_bytes()
    token_args["attachment_document_ids"] = []
    denied = _read_presentation(client, token_args)
    assert denied.status_code == 403, denied.text
    token_args["attachment_document_ids"] = ["presentation-document"]
    token_args["allowed_tools"] = ["preview_document", "search_evidence"]
    preview = _scoped_call(client, token_args, "preview_document", document_id="presentation-document")
    assert preview.status_code == 200, preview.text
    assert preview.json()["inline"]["content"] == ""
    assert preview.json()["inline"]["unavailable_reason"] == "text_unavailable"
    token_args["document_ids"] = []
    inventory = _scoped_call(
        client, token_args, "search_evidence", query="列出所有文档完整清单"
    )
    assert inventory.status_code == 200, inventory.text
    result = inventory.json()["inline"]
    assert result["evidence"] == []
    assert result["empty_reason"] == "text_unavailable"
    assert result["coverage"]["expected"] == 1
    assert result["coverage"]["complete"] is False
    with transaction() as db:
        document = db.get(Document, "presentation-document")
        document.space_id = space_id
        document.is_temporary = False
    token_args.update(
        document_ids=None, attachment_document_ids=[], allowed_tools=["read_presentation"],
    )
    knowledge_original = _read_presentation(client, token_args)
    assert knowledge_original.status_code == 403, knowledge_original.text
    token_args["document_ids"] = ["presentation-document"]
    explicitly_selected = _read_presentation(client, token_args)
    assert explicitly_selected.status_code == 200, explicitly_selected.text
    assert explicitly_selected.content == original.read_bytes()


def test_separate_attachment_project_context_keeps_empty_knowledge_scope(separated_document_scope):
    client, token_args, _space_id, _owner_id = separated_document_scope
    token_args.update(project_ids=["context-project"], document_ids=[])
    with transaction() as db:
        project = db.get(WritingProject, "context-project")
        project.selected_space_ids = "[]"
        project.selected_document_ids = "[]"
    response = _scoped_call(client, token_args, "get_project_context", project_id="context-project")
    assert response.status_code == 200, response.text
    assert response.json()["inline"]["space_ids"] == []
    assert response.json()["inline"]["document_ids"] == []


@pytest.mark.parametrize("case", ["allowed", "scope", "unselected", "version", "revoked", "revoked_after_copy"])
def test_image_material_original_is_version_bound_and_rechecks_acl(
    presentation_scope, monkeypatch, case,
):
    import hashlib
    from contextlib import contextmanager
    from PIL import Image
    from sqlalchemy import delete
    from app.api import agent_capabilities as api
    from app.models.access import SpaceMember

    client, token_args, original, editor_id, space_id = presentation_scope
    image_path = original.with_suffix(".png")
    Image.new("RGB", (30, 20), "blue").save(image_path)
    with transaction() as db:
        document = db.get(Document, "presentation-document")
        document.space_id = space_id
        document.is_temporary = False
        document.chat_session_id = None
        version = db.get(DocumentVersion, "presentation-version")
        version.file_path = str(image_path)
        version.source_relative_path = "产品素材.png"
    token_args.update(
        user_id=editor_id, document_ids=["presentation-document"], attachment_document_ids=[],
        allowed_tools=["read_image"], space_mode="selected",
    )
    version_id = "presentation-version"
    if case == "scope":
        token_args["space_ids"] = []
    elif case == "unselected":
        token_args["document_ids"] = None
    elif case == "version":
        version_id = "context-version"
    elif case == "revoked":
        with transaction() as db:
            db.execute(delete(SpaceMember).where(
                SpaceMember.space_id == space_id, SpaceMember.user_id == editor_id,
            ))
    elif case == "revoked_after_copy":
        copy = api.copy_original
        @contextmanager
        def revoked_copy(path):
            with copy(path) as snapshot:
                with transaction() as db:
                    db.execute(delete(SpaceMember).where(
                        SpaceMember.space_id == space_id, SpaceMember.user_id == editor_id,
                    ))
                yield snapshot
        monkeypatch.setattr(api, "copy_original", revoked_copy)
    result = _scoped_call(
        client, token_args, "read_image",
        document_id="presentation-document", version_id=version_id,
    )
    if case == "allowed":
        assert result.status_code == 200, result.text
        assert result.content == image_path.read_bytes()
        assert result.headers["X-Nexus-SHA256"] == hashlib.sha256(result.content).hexdigest()
        assert result.headers["X-Nexus-Version-ID"] == "presentation-version"
        assert result.headers["X-Nexus-Page-Count"] == "1"
    else:
        assert result.status_code == (404 if case.startswith("revoked") else 403)
        assert "X-Nexus-SHA256" not in result.headers
        assert str(image_path) not in result.text
