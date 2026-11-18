from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.api import documents as documents_api
from app.core.database import transaction
from app.models.access import KnowledgeSpace
from app.models.auth import AuditEvent
from app.models.chat import ChatMessage
from app.models.documents import Document, DocumentJob, DocumentVersion
from app.repositories import audit, chat
from app.services.document_processor import extract_docx_text
from tests.test_document_moves import move_client
from tests.test_documents import _create_space, _member_headers


def _message(client, headers, *, role="assistant", content="# 项目实施方案\n\n先梳理需求，再分阶段交付。"):
    owner_id = client.get("/auth/me", headers=headers).json()["id"]
    with transaction() as db:
        session = chat.create_session(db, owner_user_id=owner_id, title="会话标题")
        message = ChatMessage(
            id=f"msg_{uuid4().hex}", session_id=session.id,
            owner_user_id=owner_id, role=role, content=content,
            created_at=datetime.now(timezone.utc),
        )
        db.add(message)
        db.flush()
        return session.id, message.id


def _save(client, headers, message_id, space_id):
    return client.post(
        f"/chat/messages/{message_id}:save-as-document",
        headers=headers, json={"space_id": space_id},
    )


def test_save_reply_docx_and_reuse_existing_document(move_client, monkeypatch):
    client = move_client
    space, headers = _create_space(client, "保存资料")
    session_id, message_id = _message(client, headers)
    submissions = []
    original = documents_api._submit_and_schedule

    async def submit(*args, **kwargs):
        submissions.append(kwargs["filename"])
        return await original(*args, **kwargs)

    monkeypatch.setattr(documents_api, "_submit_and_schedule", submit)
    first = _save(client, headers, message_id, space["id"])
    assert first.status_code == 200, first.text
    saved = first.json()
    assert saved["reused"] is False
    document = saved["document"]
    assert document["space_id"] == space["id"]
    assert document["name"] == "项目实施方案"
    assert document["is_temporary"] is False
    assert document["latest_version"]["file_extension"] == ".docx"
    source = f"chat/{session_id}/{message_id}.docx"
    assert document["latest_version"]["source_relative_path"] == source
    with transaction() as db:
        version = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document["id"]))
        assert version is not None
        assert version.file_path is not None
        text = extract_docx_text(Path(version.file_path).read_bytes())
        assert "项目实施方案" in text
        assert "先梳理需求，再分阶段交付。" in text
        event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "chat_message_saved_as_document"))
        assert event is not None and event.target_id == document["id"]
        assert audit.EVENT_LABELS[event.event_type] == "聊天回复存为资料"
    second = _save(client, headers, message_id, space["id"])
    assert second.status_code == 200, second.text
    assert second.json()["reused"] is True
    assert second.json()["document"]["id"] == document["id"]
    assert submissions == [f"{message_id}.docx"]
    with transaction() as db:
        assert len(db.scalars(select(Document).where(Document.space_id == space["id"])).all()) == 1
        assert len(db.scalars(select(DocumentJob).where(DocumentJob.document_id == document["id"])).all()) == 1
    listed = client.get(f"/knowledge-spaces/{space['id']}/documents", headers=headers)
    assert document["id"] in {item["id"] for item in listed.json()["items"]}


@pytest.mark.parametrize("overlap_uploads", [False, True], ids=["late-render", "concurrent"])
def test_save_reply_race_reuses_winner(move_client, monkeypatch, overlap_uploads):
    client = move_client
    space, headers = _create_space(client, "并发保存")
    _, message_id = _message(client, headers)
    rendering = Event()
    release = Event()
    barrier = Barrier(2)
    render = documents_api.markdown_to_docx_bytes

    def paused_render(*args, **kwargs):
        if overlap_uploads:
            barrier.wait(timeout=10)
        elif not rendering.is_set():
            rendering.set()
            assert release.wait(timeout=10)
        return render(*args, **kwargs)

    monkeypatch.setattr(documents_api, "markdown_to_docx_bytes", paused_render)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_save, client, headers, message_id, space["id"])
        if not overlap_uploads:
            assert rendering.wait(timeout=10)
        second = pool.submit(_save, client, headers, message_id, space["id"])
        try:
            winner = second.result(timeout=20)
        finally:
            release.set()
        delayed = first.result(timeout=20)

    assert winner.status_code == delayed.status_code == 200
    results = [winner.json(), delayed.json()]
    assert sorted(result["reused"] for result in results) == [False, True]
    assert results[0]["document"]["id"] == results[1]["document"]["id"]
    with transaction() as db:
        assert len(db.scalars(select(Document).where(Document.space_id == space["id"])).all()) == 1
        assert len(db.scalars(select(DocumentJob).where(DocumentJob.space_id == space["id"])).all()) == 1
        assert len(db.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "chat_message_saved_as_document",
        )).all()) == 1


def test_save_reply_hides_other_users_messages_and_missing_messages(move_client):
    client = move_client
    space, headers = _create_space(client, "权限")
    other = _member_headers(client, space["id"], "other-save", "contributor")
    _, message_id = _message(client, other)
    assert _save(client, headers, message_id, space["id"]).status_code == 404
    assert _save(client, headers, "missing-message", space["id"]).status_code == 404
    # 即使消息拥有者字段被旧数据错误设置，也不能读取他人的会话。
    with transaction() as db:
        message = db.get(ChatMessage, message_id)
        assert message is not None
        message.owner_user_id = client.get("/auth/me", headers=headers).json()["id"]
    assert _save(client, headers, message_id, space["id"]).status_code == 404


def test_save_reply_requires_contributor_membership(move_client):
    client = move_client
    space, _ = _create_space(client, "只读")
    reader = _member_headers(client, space["id"], "reader-save", "reader")
    _, message_id = _message(client, reader)
    assert _save(client, reader, message_id, space["id"]).status_code == 404


@pytest.mark.parametrize("target_status", ["missing", "archived", "deleted"])
def test_save_reply_rejects_invalid_target(move_client, target_status):
    client = move_client
    space, headers = _create_space(client, "无效目标")
    _, message_id = _message(client, headers)
    target_id = space["id"]
    if target_status == "missing":
        target_id = "missing-space"
    else:
        with transaction() as db:
            space_row = db.get(KnowledgeSpace, target_id)
            assert space_row is not None
            space_row.status = target_status
    assert _save(client, headers, message_id, target_id).status_code == 422


@pytest.mark.parametrize("role,content", [("user", "用户正文"), ("assistant", " \n\t")])
def test_save_reply_rejects_invalid_message(move_client, role, content):
    client = move_client
    space, headers = _create_space(client, "无效消息")
    _, message_id = _message(client, headers, role=role, content=content)
    assert _save(client, headers, message_id, space["id"]).status_code == 422


def test_save_reply_uses_session_title_and_ignores_inactive_results(move_client):
    client = move_client
    space, headers = _create_space(client, "历史资料")
    _, message_id = _message(client, headers, content="普通正文，无标题。")
    first = _save(client, headers, message_id, space["id"])
    assert first.status_code == 200, first.text
    assert first.json()["document"]["name"] == "会话标题"
    old_id = first.json()["document"]["id"]
    with transaction() as db:
        document = db.get(Document, old_id)
        assert document is not None
        document.status = "archived"
    second = _save(client, headers, message_id, space["id"])
    assert second.status_code == 200, second.text
    assert second.json()["reused"] is False
    assert second.json()["document"]["id"] != old_id


def _generated_reply(client, headers, tmp_path, *, files=None):
    import json
    from app.services.harness.file_storage import register_workspace

    session_id, message_id = _message(client, headers, content="")
    owner_id = client.get("/auth/me", headers=headers).json()["id"]
    task_id = f"pi-chat-{owner_id}-{session_id}-turn-{uuid4().hex}"
    workspace = tmp_path / task_id
    workspace.mkdir()
    register_workspace(task_id, workspace)
    files = files or {"first/report.txt": b"first original", "second/report.txt": b"second original"}
    parts = []
    for relative, raw in files.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        parts.append({"type": "file", "name": path.name, "href": f"/api/files/{task_id}/{relative}"})
    with transaction() as db:
        message = db.get(ChatMessage, message_id)
        message.parts_json = json.dumps(parts)
        # 同一个规范链接的 Markdown/结构化表示只能列出一次。
        message.content = "\n".join(f"[{part['name']}]({part['href']}?download=true)" for part in parts)
    return session_id, message_id, parts


def _results(client, headers, session_id, **params):
    return client.get(f"/chat/sessions/{session_id}/saveable-results", headers=headers, params=params)


def _save_result(client, headers, message_id, space_id, result_id):
    return client.post(
        f"/chat/messages/{message_id}:save-as-document", headers=headers,
        json={"space_id": space_id, "result_id": result_id},
    )


def test_save_generated_bytes_distinct_paths_and_refresh(move_client, tmp_path):
    client = move_client
    target, headers = _create_space(client, "原件存入资料")
    other, _ = _create_space(client, "另一目标")
    session_id, message_id, _ = _generated_reply(client, headers, tmp_path)
    first_page = _results(client, headers, session_id, limit=1).json()
    assert first_page["total"] == 2 and first_page["next_offset"] == 1
    second_page = _results(client, headers, session_id, offset=1, limit=1).json()
    assert second_page["next_offset"] is None
    results = first_page["items"] + second_page["items"]
    assert results[0]["id"] != results[1]["id"]
    assert all(item["can_save"] and item["name"] == "report.txt" for item in results)
    documents_saved = []
    for result, expected in zip(results, [b"first original", b"second original"]):
        response = _save_result(client, headers, message_id, target["id"], result["id"])
        assert response.status_code == 200, response.text
        document = response.json()["document"]
        documents_saved.append(document["id"])
        with transaction() as db:
            version = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document["id"]))
            assert Path(version.file_path).read_bytes() == expected
            assert version.source_relative_path.endswith("/report.txt")
        again = _save_result(client, headers, message_id, target["id"], result["id"])
        assert again.status_code == 200 and again.json()["reused"] is True
        assert again.json()["document"]["id"] == document["id"]
    refreshed = _results(client, headers, session_id, message_id=message_id).json()["items"]
    assert [item["saved_documents"][0]["document_id"] for item in refreshed] == documents_saved
    cross = _save_result(client, headers, message_id, other["id"], results[0]["id"])
    assert cross.status_code == 200 and cross.json()["reused"] is False
    assert cross.json()["document"]["id"] not in documents_saved
    assert len(_results(client, headers, session_id).json()["items"][0]["saved_documents"]) == 2
    assert client.delete(f"/chat/sessions/{session_id}", headers=headers).status_code == 204
    with transaction() as db:
        assert all(db.get(Document, document_id).status == "active" for document_id in documents_saved)


def test_generated_results_reject_forged_foreign_urls_and_sidecars(move_client, tmp_path):
    import json

    client = move_client
    target, headers = _create_space(client, "成果边界")
    other_headers = _member_headers(client, target["id"], "foreign-results", "contributor")
    _, _, foreign = _generated_reply(client, other_headers, tmp_path)
    session_id, message_id, own = _generated_reply(
        client, headers, tmp_path,
        files={
            "report.txt": b"report", "preview.png": b"image", "clip.mp4": b"video",
            "bundle.zip": b"archive", "slides.deck.json": b"{}",
        },
    )
    with transaction() as db:
        message = db.get(ChatMessage, message_id)
        message.parts_json = json.dumps([
            *own, *foreign,
            {"type": "file", "name": "external.txt", "href": "https://evil.example" + own[0]["href"]},
            {"type": "file", "name": "traversal.txt", "href": own[0]["href"].replace("report.txt", "%2e%2e/report.txt")},
        ])
    response = _results(client, headers, session_id)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert {item["name"] for item in items} == {"report.txt", "preview.png", "clip.mp4", "bundle.zip"}
    for unsupported in (item for item in items if item["name"] in {"clip.mp4", "bundle.zip"}):
        assert unsupported["can_save"] is False and "不支持" in unsupported["reason"]
        assert _save_result(client, headers, message_id, target["id"], unsupported["id"]).status_code == 422
    image = next(item for item in items if item["name"] == "preview.png")
    assert image["can_save"] is True
    # 元数据允许图片，但保存时仍须拒绝伪造图片字节。
    assert _save_result(client, headers, message_id, target["id"], image["id"]).status_code == 422
    assert _save_result(client, headers, message_id, target["id"], "missing-result").status_code == 404
    assert _results(client, other_headers, session_id).status_code == 404


def test_generated_result_concurrent_save_reuses_one_document(move_client, tmp_path, monkeypatch):
    from app.services import chat_saveable_results

    client = move_client
    target, headers = _create_space(client, "成果并发")
    session_id, message_id, _ = _generated_reply(client, headers, tmp_path, files={"report.txt": b"original"})
    result = _results(client, headers, session_id).json()["items"][0]
    barrier = Barrier(2)
    read = chat_saveable_results.read_result_bytes

    def simultaneous_read(*args, **kwargs):
        raw = read(*args, **kwargs)
        barrier.wait(timeout=10)
        return raw

    monkeypatch.setattr(chat_saveable_results, "read_result_bytes", simultaneous_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_save_result, client, headers, message_id, target["id"], result["id"])
            for _ in range(2)
        ]
        responses = [future.result(timeout=20) for future in futures]
    assert all(response.status_code == 200 for response in responses)
    assert sorted(response.json()["reused"] for response in responses) == [False, True]
    assert responses[0].json()["document"]["id"] == responses[1].json()["document"]["id"]


@pytest.mark.parametrize("change", ["archive-target", "remove-member", "delete-session"])
def test_generated_result_rechecks_access_after_read(move_client, tmp_path, monkeypatch, change):
    from app.models.access import SpaceMember
    from app.models.chat import ChatSession
    from app.services import chat_saveable_results

    client = move_client
    target, _ = _create_space(client, "成果竞态")
    headers = _member_headers(client, target["id"], "result-race-user", "contributor")
    owner_id = client.get("/auth/me", headers=headers).json()["id"]
    session_id, message_id, _ = _generated_reply(client, headers, tmp_path, files={"report.txt": b"original"})
    result = _results(client, headers, session_id).json()["items"][0]
    read = chat_saveable_results.read_result_bytes

    def changed_after_read(*args, **kwargs):
        raw = read(*args, **kwargs)
        with transaction() as db:
            if change == "archive-target":
                db.get(KnowledgeSpace, target["id"]).status = "archived"
            elif change == "remove-member":
                db.delete(db.scalar(select(SpaceMember).where(
                    SpaceMember.space_id == target["id"], SpaceMember.user_id == owner_id,
                )))
            else:
                db.delete(db.get(ChatSession, session_id))
        return raw

    monkeypatch.setattr(chat_saveable_results, "read_result_bytes", changed_after_read)
    response = _save_result(client, headers, message_id, target["id"], result["id"])
    assert response.status_code in {404, 422}, response.text
    with transaction() as db:
        assert db.scalar(select(Document).where(Document.space_id == target["id"])) is None


def test_archived_chat_and_legacy_workspace_results_remain_saveable(move_client, tmp_path):
    import json
    from app.models.chat import ChatSession
    from app.services.harness.file_storage import register_workspace

    client = move_client
    target, headers = _create_space(client, "历史成果")
    session_id, message_id = _message(client, headers, content="")
    owner_id = client.get("/auth/me", headers=headers).json()["id"]
    task_id = f"pi-chat-{owner_id}-{session_id}"
    workspace = tmp_path / "legacy"
    workspace.mkdir()
    source = workspace / "report.txt"
    source.write_bytes(b"old original")
    register_workspace(task_id, workspace)
    with transaction() as db:
        db.get(ChatSession, session_id).archived = True
        message = db.get(ChatMessage, message_id)
        message.parts_json = json.dumps([{"type": "file", "href": f"/api/files/{task_id}/report.txt"}])
    first = _results(client, headers, session_id).json()["items"][0]
    saved = _save_result(client, headers, message_id, target["id"], first["id"])
    assert saved.status_code == 200, saved.text
    source.write_bytes(b"new original is independent")
    second = _results(client, headers, session_id).json()["items"][0]
    assert second["id"] != first["id"] and second["saved_documents"] == []
    assert _save_result(client, headers, message_id, target["id"], first["id"]).status_code == 404
    newer = _save_result(client, headers, message_id, target["id"], second["id"])
    assert newer.status_code == 200 and newer.json()["reused"] is False
    with transaction() as db:
        old_version = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == saved.json()["document"]["id"]))
        assert Path(old_version.file_path).read_bytes() == b"old original"


def test_saved_results_hide_targets_after_membership_removed(move_client, tmp_path):
    from app.models.access import SpaceMember

    client = move_client
    target, _ = _create_space(client, "不可泄露的目标文件夹")
    headers = _member_headers(client, target["id"], "save-result-reader", "contributor")
    owner_id = client.get("/auth/me", headers=headers).json()["id"]
    session_id, message_id, _ = _generated_reply(client, headers, tmp_path, files={"report.txt": b"original"})
    result = _results(client, headers, session_id).json()["items"][0]
    assert _save_result(client, headers, message_id, target["id"], result["id"]).status_code == 200
    with transaction() as db:
        member = db.scalar(select(SpaceMember).where(SpaceMember.space_id == target["id"], SpaceMember.user_id == owner_id))
        member.role = "reader"
    assert len(_results(client, headers, session_id).json()["items"][0]["saved_documents"]) == 1
    with transaction() as db:
        db.delete(db.scalar(select(SpaceMember).where(SpaceMember.space_id == target["id"], SpaceMember.user_id == owner_id)))
    refreshed = _results(client, headers, session_id)
    assert refreshed.json()["items"][0]["saved_documents"] == []
    assert "不可泄露的目标文件夹" not in refreshed.text
    assert _save_result(client, headers, message_id, target["id"], result["id"]).status_code == 404


def test_full_history_results_include_unloaded_messages_without_reading_files(move_client, tmp_path, monkeypatch):
    from app.services import chat_saveable_results

    client = move_client
    _, headers = _create_space(client, "完整历史")
    session_id, message_id, _ = _generated_reply(client, headers, tmp_path, files={"report.txt": b"original"})
    with transaction() as db:
        original = db.get(ChatMessage, message_id)
        for index in range(60):
            db.add(ChatMessage(
                id=f"history_{index}_{uuid4().hex}", session_id=session_id,
                owner_user_id=original.owner_user_id, role="assistant", content=f"后来的回复 {index}",
                created_at=datetime.now(timezone.utc),
            ))
    monkeypatch.setattr(chat_saveable_results, "read_generated_file_bytes", lambda *args, **kwargs: pytest.fail("list downloaded file"))
    response = _results(client, headers, session_id)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["message_id"] == message_id


def _database_result(client, headers, *, kind):
    import hashlib
    import json
    from io import BytesIO
    from pptx import Presentation
    from app.models.artifacts import Artifact
    from app.models.chat_deck_render import ChatDeckRender
    from app.models.writing import WritingDocTemplate, WritingDocTemplateRevision, WritingOperation, WritingProject

    session_id, message_id = _message(client, headers, content="")
    owner_id = client.get("/auth/me", headers=headers).json()["id"]
    stamp = datetime.now(timezone.utc)
    source_id = f"result_{uuid4().hex}"
    with transaction() as db:
        if kind == "artifacts":
            project = WritingProject(
                id=f"project_{uuid4().hex}", owner_user_id=owner_id, title="成果",
                selected_space_ids="[]", created_at=stamp, updated_at=stamp,
            )
            db.add(project)
            db.flush()
            operation = WritingOperation(
                id=f"op_{uuid4().hex}", project_id=project.id, kind="export",
                status="completed", usage="{}", result="{}", created_at=stamp,
            )
            db.add(operation)
            db.flush()
            raw = documents_api.markdown_to_docx_bytes("# 原件\n\n保持内容", title="原件")
            row = Artifact(
                id=source_id, owner_user_id=owner_id, project_id=project.id,
                operation_id=operation.id, name="report.docx", status="ready", storage_key="database",
                content=raw, checksum_sha256=hashlib.sha256(raw).hexdigest(),
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                content_size=len(raw), created_at=stamp, updated_at=stamp,
            )
        else:
            template = WritingDocTemplate(
                id=f"template_{uuid4().hex}", name="测试模板", created_by_user_id=owner_id,
                create_idempotency_key=uuid4().hex, create_request_sha256="a" * 64,
                created_at=stamp, updated_at=stamp,
            )
            db.add(template)
            db.flush()
            revision = WritingDocTemplateRevision(
                id=f"revision_{uuid4().hex}", template_id=template.id, revision_no=1,
                created_by_user_id=owner_id, request_sha256="b" * 64, source_format="pptx",
                state="published", created_at=stamp, updated_at=stamp,
            )
            db.add(revision)
            db.flush()
            buffer = BytesIO()
            presentation = Presentation()
            presentation.slides.add_slide(presentation.slide_layouts[0])
            presentation.save(buffer)
            raw = buffer.getvalue()
            row = ChatDeckRender(
                id=source_id, owner_user_id=owner_id, session_id=session_id, message_id=message_id,
                template_revision_id=revision.id, source_task_id="source", source_file_path="source.deck.json",
                source_sha256="c" * 64, status="ready", output_name="report.pptx", output_size=len(raw),
                output_sha256=hashlib.sha256(raw).hexdigest(), output_content=raw,
                output_mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                created_at=stamp, updated_at=stamp,
            )
        db.add(row)
        message = db.get(ChatMessage, message_id)
        filename = "report.docx" if kind == "artifacts" else "report.pptx"
        message.content = f"[成果](/api/files/{kind}/{source_id}/{filename})"
        message.parts_json = json.dumps([{"type": "file", "href": f"/api/files/{kind}/{source_id}/cosmetic-name.txt"}])
    return session_id, message_id, source_id, raw


@pytest.mark.parametrize("kind", ["artifacts", "chat-deck-renders"])
def test_database_results_keep_original_bytes_and_reject_unready(move_client, kind):
    from app.models.artifacts import Artifact
    from app.models.chat_deck_render import ChatDeckRender

    client = move_client
    target, headers = _create_space(client, "数据库成果")
    session_id, message_id, source_id, raw = _database_result(client, headers, kind=kind)
    response = _results(client, headers, session_id)
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    result = response.json()["items"][0]
    assert result["can_save"]
    saved = _save_result(client, headers, message_id, target["id"], result["id"])
    assert saved.status_code == 200, saved.text
    with transaction() as db:
        version = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == saved.json()["document"]["id"]))
        assert Path(version.file_path).read_bytes() == raw
        if kind == "artifacts":
            db.get(Artifact, source_id).status = "expired"
        else:
            db.get(ChatDeckRender, source_id).status = "failed"
    unavailable = _results(client, headers, session_id).json()["items"][0]
    assert unavailable["can_save"] is False and unavailable["reason"]
    assert _save_result(client, headers, message_id, target["id"], result["id"]).status_code == 422


@pytest.mark.parametrize("kind", ["artifacts", "chat-deck-renders"])
def test_foreign_database_result_link_does_not_grant_access_even_to_admin(move_client, kind):
    client = move_client
    target, admin_headers = _create_space(client, "无管理员旁路")
    other_headers = _member_headers(client, target["id"], f"foreign-{kind}", "contributor")
    _, foreign_message_id, _, _ = _database_result(client, other_headers, kind=kind)
    session_id, own_message_id = _message(client, admin_headers)
    with transaction() as db:
        foreign = db.get(ChatMessage, foreign_message_id)
        own = db.get(ChatMessage, own_message_id)
        own.content, own.parts_json = foreign.content, foreign.parts_json
    assert _results(client, admin_headers, session_id).json()["items"] == []


@pytest.mark.parametrize("kind", ["artifacts", "chat-deck-renders"])
def test_database_result_list_does_not_fetch_blob(move_client, kind):
    import re
    from sqlalchemy import event

    client = move_client
    _, headers = _create_space(client, "轻量成果列表")
    session_id, _, _, _ = _database_result(client, headers, kind=kind)
    with transaction() as db:
        engine = db.get_bind()
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = _results(client, headers, session_id)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200 and response.json()["total"] == 1
    column = "artifacts.content" if kind == "artifacts" else "chat_deck_renders.output_content"
    assert not any(re.search(re.escape(column) + r"(?:,| AS)", statement) for statement in statements)


def test_artifact_deletion_after_read_rolls_back_save(move_client, monkeypatch):
    from app.models.artifacts import Artifact
    from app.services import chat_saveable_results

    client = move_client
    target, headers = _create_space(client, "成果删除竞态")
    session_id, message_id, artifact_id, _ = _database_result(client, headers, kind="artifacts")
    result = _results(client, headers, session_id).json()["items"][0]
    read = chat_saveable_results.read_result_bytes

    def deleted_after_read(*args, **kwargs):
        raw = read(*args, **kwargs)
        with transaction() as db:
            db.get(Artifact, artifact_id).deleted_at = datetime.now(timezone.utc)
        return raw

    monkeypatch.setattr(chat_saveable_results, "read_result_bytes", deleted_after_read)
    response = _save_result(client, headers, message_id, target["id"], result["id"])
    assert response.status_code == 404, response.text
    with transaction() as db:
        assert db.scalar(select(Document).where(Document.space_id == target["id"])) is None


def test_snapshot_source_change_during_read_never_saves_newer_bytes_under_old_result(move_client, tmp_path, monkeypatch):
    from app.services import chat_saveable_results
    from app.services.harness.file_storage import resolve_workspace

    client = move_client
    target, headers = _create_space(client, "原件变更竞态")
    session_id, message_id, parts = _generated_reply(client, headers, tmp_path, files={"report.txt": b"original"})
    result = _results(client, headers, session_id).json()["items"][0]
    read = chat_saveable_results.read_result_bytes

    def changed_after_read(*args, **kwargs):
        raw = read(*args, **kwargs)
        task_id = parts[0]["href"].split("/")[3]
        (resolve_workspace(task_id) / "report.txt").write_bytes(b"different original")
        return raw

    monkeypatch.setattr(chat_saveable_results, "read_result_bytes", changed_after_read)
    assert _save_result(client, headers, message_id, target["id"], result["id"]).status_code == 409
    with transaction() as db:
        assert db.scalar(select(Document).where(Document.space_id == target["id"])) is None


def test_artifact_knowledge_source_acl_has_no_admin_bypass(move_client):
    import json
    from app.models.artifacts import Artifact
    from tests.test_documents import _create_document

    client = move_client
    source, headers = _create_space(client, "成果来源")
    target, _ = _create_space(client, "保存目标")
    uploaded = _create_document(client, source["id"], headers, "来源资料", b"source")["document"]
    session_id, message_id, artifact_id, _ = _database_result(client, headers, kind="artifacts")
    with transaction() as db:
        version = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == uploaded["id"]))
        db.get(Artifact, artifact_id).source_version_ids = json.dumps([version.id])
    result = _results(client, headers, session_id).json()["items"][0]
    with transaction() as db:
        db.get(KnowledgeSpace, source["id"]).status = "archived"
    assert _results(client, headers, session_id).json()["items"] == []
    assert _save_result(client, headers, message_id, target["id"], result["id"]).status_code == 404


def test_snapshot_identity_is_independent_of_storage_backend(move_client, tmp_path, monkeypatch):
    from app.services import chat_saveable_results

    client = move_client
    _, headers = _create_space(client, "快照身份")
    session_id, _, _ = _generated_reply(client, headers, tmp_path, files={"report.txt": b"original"})
    first = _results(client, headers, session_id).json()["items"][0]
    monkeypatch.setattr(chat_saveable_results, "_generated_metadata", lambda _task, path: (path, 8, "object:etag:8"))
    second = _results(client, headers, session_id).json()["items"][0]
    assert first["id"] == second["id"]
