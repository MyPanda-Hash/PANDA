from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from pptx import Presentation

from app.core import database
from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models.auth import User
from app.models.writing import WritingProject
from app.repositories import writing as writing_repo
from app.services.presentation_deck import build_deck_spec, render_deck_spec
from app.services.pi_writing_provider import _search_evidence
from app.services.writing_preview import ChapterPreviewStream, PreviewStatus
from app.services.writing_scope import WritingScopeUnavailableError
from tests._auth import bootstrap_admin_response


def _admin_headers(client: TestClient) -> dict[str, str]:
    response = bootstrap_admin_response(client)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _member_login(client: TestClient, username: str) -> tuple[dict[str, str], dict]:
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
    return {"Authorization": f"Bearer {login.json()['access_token']}"}, login.json()[
        "user"
    ]


def _member_headers(client: TestClient, username: str) -> dict[str, str]:
    return _member_login(client, username)[0]


def _scoped_member(client: TestClient, username: str) -> tuple[dict[str, str], dict]:
    headers, user = _member_login(client, username)
    space = client.post(
        "/knowledge-spaces",
        headers=_admin_headers(client),
        json={"name": f"{username}-scope", "description": ""},
    )
    assert space.status_code == 201
    granted = client.post(
        f"/knowledge-spaces/{space.json()['id']}/members",
        headers=_admin_headers(client),
        json={"user_id": user["id"], "role": "reader"},
    )
    assert granted.status_code == 201
    return headers, {"id": space.json()["id"], "user_id": user["id"]}


def _client_database(tmp_path: Path, name: str):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / name
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    return old_path, old_url, path


def _restore_database(old_path: Path, old_url: str, path: Path) -> None:
    settings.db_path, settings.database_url = old_path, old_url
    database.reset_database(path)


def test_presentation_renderer_rejects_retired_template_plans():
    from app.services.presentation_deck import DeckSpecError

    with pytest.raises(DeckSpecError) as failure:
        render_deck_spec({
            "schema_version": 2,
            "template": {"revision_id": "historical-template"},
            "slides": [{"role": "cover", "title": "不可启动套版"}],
        })
    assert failure.value.code == "unsupported_schema_version"


def test_presentation_renderer_preserves_declared_elements_notes_and_citations():
    rendered = render_deck_spec(
        {
            "slides": [
                {
                    "role": "cover",
                    "title": "管理层汇报",
                    "takeaway": "结论先行",
                    "speaker_notes": "开场说明",
                    "citations": [],
                },
                {
                    "role": "section",
                    "title": "客户洞察",
                    "takeaway": "客户需要更稳定的交付",
                    "elements": [
                        {"type": "quote", "items": ["稳定性优先", "客户访谈"]}
                    ],
                    "citations": ["writing-evidence:version-1:10-30"],
                    "speaker_notes": "先讲客户原话，再解释影响。",
                },
                {
                    "role": "comparison",
                    "title": "方案比较",
                    "takeaway": "方案 A 风险最低",
                    "elements": [
                        {
                            "type": "comparison",
                            "items": ["方案 A：稳健", "方案 B：快速"],
                        }
                    ],
                    "citations": [],
                    "speaker_notes": "",
                },
                {
                    "role": "metric",
                    "title": "季度进展",
                    "takeaway": "完成率持续提升",
                    "elements": [
                        {"type": "chart", "items": ["Q1 30%", "Q2 65%", "Q3 90%"]}
                    ],
                    "citations": [],
                    "speaker_notes": "",
                },
                {"role": "closing", "title": "谢谢", "takeaway": "确认下一步"},
            ]
        }
    )

    presentation = Presentation(BytesIO(rendered))
    text_by_slide = [
        "\n".join(shape.text for shape in slide.shapes if hasattr(shape, "text"))
        for slide in presentation.slides
    ]
    assert "稳定性优先" in text_by_slide[1]
    # 交付文件不写来源：spec 里的引用 ID 只用于留痕，不渲染成页内文字或备注。
    assert "资料来源" not in text_by_slide[1]
    assert "writing-evidence:version-1:10-30" not in text_by_slide[1]
    assert "方案 A：稳健" in text_by_slide[2]
    assert "方案 B：快速" in text_by_slide[2]
    assert "Q1 30%" in text_by_slide[3]
    assert "Q3 90%" in text_by_slide[3]
    assert "确认下一步" in text_by_slide[4]
    assert "先讲客户原话" in presentation.slides[1].notes_slide.notes_text_frame.text


def test_deck_delivery_strips_citation_markers_and_keeps_ids_only_as_metadata():
    citation_id = f"writing-evidence:ver_a:{'0' * 64}:0-5"
    spec = build_deck_spec(
        "交付演示",
        [("正文", f"响应时间≤500ms ^[{citation_id}]，另有 [检测报告#6] 与 ^[报价文档]。")],
    )
    slide_text = "\n".join(
        str(item)
        for slide in spec["slides"]
        for element in slide.get("elements") or []
        for item in element.get("items") or []
    )
    assert "响应时间≤500ms" in slide_text
    assert citation_id not in slide_text
    assert "#6" not in slide_text
    assert "报价文档" not in slide_text
    # 引用 ID 仍随 spec 保留，供页面展示与留痕；备注里不再写来源。
    assert spec["slides"][1]["citations"] == [citation_id]
    assert all(slide.get("speaker_notes") == "" for slide in spec["slides"])


def test_presentation_renderer_falls_back_when_declared_element_is_empty():
    rendered = render_deck_spec(
        {
            "slides": [
                {"role": "cover", "title": "空元素容错"},
                {
                    "role": "section",
                    "title": "待完善页面",
                    "elements": [{"type": "quote", "items": []}],
                },
            ]
        }
    )

    presentation = Presentation(BytesIO(rendered))
    visible = "\n".join(
        shape.text for shape in presentation.slides[1].shapes if hasattr(shape, "text")
    )
    assert "待补充" in visible


def test_presentation_compiler_bounds_long_documents_without_losing_chapter_titles():
    spec = build_deck_spec(
        "长文稿",
        [(f"章节 {index}", f"第 {index} 章结论") for index in range(1, 21)],
    )

    assert len(spec["slides"]) <= 12
    visible = json.dumps(spec, ensure_ascii=False)
    assert "章节 1" in visible
    assert "章节 20" in visible


def _replace_outline(
    client: TestClient,
    project_id: str,
    headers: dict[str, str],
    chapters: list[dict[str, str]],
) -> dict:
    response = client.put(
        f"/writing/projects/{project_id}/outline",
        headers=headers,
        json={"chapters": chapters},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _completed_operation_with_events(
    client: TestClient,
    headers: dict[str, str],
    *,
    event_count: int,
) -> str:
    created = client.post(
        "/writing/projects",
        headers=headers,
        json={"title": "事件分页验证", "space_ids": []},
    )
    assert created.status_code == 201, created.text
    operation = client.post(
        f"/writing/projects/{created.json()['id']}/outline-runs",
        headers=headers,
    )
    assert operation.status_code == 201, operation.text
    operation_id = operation.json()["id"]
    with SessionLocal() as db:
        row = writing_repo.operation_by_id(db, operation_id)
        assert row is not None
        writing_repo.complete_operation(db, row, {})
        for event_id in range(1, event_count + 1):
            writing_repo.record_agent_event(
                db,
                platform_run_id=f"pi-intent:{operation_id}",
                event_id=event_id,
                event_type="message_update",
                payload_sha256=f"{event_id:064x}",
                payload_json=json.dumps({"sequence": event_id}),
            )
        row.last_event_cursor = event_count
        db.commit()
    return operation_id


def test_private_project_is_invisible_to_other_users_and_administrators(tmp_path: Path):
    old_path, old_url, path = _client_database(tmp_path, "writing-private.sqlite3")
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-owner")
            other = _member_headers(client, "writing-other")
            created = client.post(
                "/writing/projects",
                headers=owner,
                json={"title": "董事会材料", "space_ids": []},
            )
            assert created.status_code == 201
            project_id = created.json()["id"]

            assert (
                client.get(f"/writing/projects/{project_id}", headers=other).status_code
                == 404
            )
            assert (
                client.get(
                    f"/writing/projects/{project_id}", headers=_admin_headers(client)
                ).status_code
                == 404
            )
            assert [
                item["id"]
                for item in client.get("/writing/projects", headers=other).json()[
                    "items"
                ]
            ] == []
    finally:
        _restore_database(old_path, old_url, path)


LIST_SUMMARY_KEYS = {
    "id",
    "title",
    "space_ids",
    "supported_export_formats",
    "outline_status",
    "chapter_count",
    "version_count",
    "unsupported_claim_count",
    "completed_export_count",
    "updated_at",
}



def test_project_list_rejects_invalid_cursor(tmp_path: Path):
    old_path, old_url, path = _client_database(tmp_path, "writing-list-422.sqlite3")
    try:
        with TestClient(app) as client:
            h = _member_headers(client, "writing-list-422-owner")
            r = client.get("/writing/projects", headers=h, params={"cursor": "trash"})
            assert r.status_code == 422
            assert r.json()["detail"] == "invalid cursor"
    finally:
        _restore_database(old_path, old_url, path)


def test_project_list_pages_by_updated_at_cursor(tmp_path: Path):
    old_path, old_url, path = _client_database(tmp_path, "writing-list-cursor.sqlite3")
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-list-cursor-owner")
            titles = ["第一份文稿", "第二份文稿", "第三份文稿"]
            project_ids = []
            for title in titles:
                created = client.post(
                    "/writing/projects",
                    headers=owner,
                    json={"title": title, "space_ids": []},
                )
                assert created.status_code == 201
                project_ids.append(created.json()["id"])

            first = client.get(
                "/writing/projects", headers=owner, params={"limit": 2}
            ).json()
            assert [item["title"] for item in first["items"]] == titles[::-1][:2]
            assert first["next_cursor"]

            second = client.get(
                "/writing/projects",
                headers=owner,
                params={"limit": 2, "cursor": first["next_cursor"]},
            ).json()
            assert [item["title"] for item in second["items"]] == [titles[0]]
            assert second["next_cursor"] is None
            # 两页合起来不重不漏。
            assert {
                item["id"] for item in first["items"] + second["items"]
            } == set(project_ids)
    finally:
        _restore_database(old_path, old_url, path)


def test_project_list_filters_by_title_and_delivery(tmp_path: Path):
    old_path, old_url, path = _client_database(tmp_path, "writing-list-filter.sqlite3")
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-list-filter-owner")
            for title in ["季度复盘", "季度计划", "年度预算"]:
                created = client.post(
                    "/writing/projects",
                    headers=owner,
                    json={"title": title, "space_ids": []},
                )
                assert created.status_code == 201

            matched = client.get(
                "/writing/projects", headers=owner, params={"q": "季度"}
            ).json()
            assert sorted(item["title"] for item in matched["items"]) == [
                "季度复盘",
                "季度计划",
            ]
            assert matched["next_cursor"] is None

            # 没有交付的文稿都在“进行中”，交付筛选下为空。
            assert client.get(
                "/writing/projects", headers=owner, params={"delivery": "active"}
            ).json()["items"]
            assert (
                client.get(
                    "/writing/projects", headers=owner, params={"delivery": "delivered"}
                ).json()["items"]
                == []
            )
    finally:
        _restore_database(old_path, old_url, path)


def test_project_list_returns_summary_without_document_bodies(tmp_path: Path):
    old_path, old_url, path = _client_database(tmp_path, "writing-list-summary.sqlite3")
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-list-owner")
            created = client.post(
                "/writing/projects",
                headers=owner,
                json={"title": "列表瘦身验证", "space_ids": []},
            )
            assert created.status_code == 201
            project_id = created.json()["id"]
            outline = _replace_outline(client, project_id, owner, [{"title": "第一章"}])
            chapter_id = outline["outline"]["chapters"][0]["id"]
            body = "这一段正文只能出现在详情接口里。"
            saved = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=owner,
                json={"content": body},
            )
            assert saved.status_code == 201

            payload = client.get("/writing/projects", headers=owner).json()
            assert [item["id"] for item in payload["items"]] == [project_id]
            item = payload["items"][0]
            # 列表只回计数与状态：正文、历史版本和操作明细必须留在详情接口。
            assert set(item) == LIST_SUMMARY_KEYS
            assert item["chapter_count"] == 1
            assert item["version_count"] == 1
            assert item["outline_status"] == "draft"
            assert body not in json.dumps(payload, ensure_ascii=False)
    finally:
        _restore_database(old_path, old_url, path)


def test_operation_events_rest_paginates_without_skipping_receipts(tmp_path: Path):
    old_path, old_url, path = _client_database(tmp_path, "writing-events-rest.sqlite3")
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-events-rest-owner")
            operation_id = _completed_operation_with_events(
                client, owner, event_count=201
            )

            first_page = client.get(
                f"/writing/operations/{operation_id}/events?after_cursor=0",
                headers=owner,
            )
            assert first_page.status_code == 200, first_page.text
            assert len(first_page.json()["items"]) == 200
            assert first_page.json()["next_cursor"] == 200

            second_page = client.get(
                f"/writing/operations/{operation_id}/events?after_cursor=200",
                headers=owner,
            )
            assert second_page.status_code == 200, second_page.text
            assert [item["event_id"] for item in second_page.json()["items"]] == [201]
            assert second_page.json()["next_cursor"] == 201
    finally:
        _restore_database(old_path, old_url, path)


def test_operation_events_and_cancellation_are_owner_scoped(tmp_path: Path):
    old_path, old_url, path = _client_database(
        tmp_path, "writing-operation-owner-scope.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-operation-owner")
            other = _member_headers(client, "writing-operation-other")
            operation_id = _completed_operation_with_events(
                client, owner, event_count=1
            )

            assert (
                client.get(
                    f"/writing/operations/{operation_id}/events", headers=other
                ).status_code
                == 404
            )
            assert (
                client.get(
                    f"/writing/operations/{operation_id}/events/stream",
                    headers=other,
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/writing/operations/{operation_id}/cancel", headers=other
                ).status_code
                == 404
            )
    finally:
        _restore_database(old_path, old_url, path)


def test_operation_events_sse_drains_all_receipts_before_operation_settled(
    tmp_path: Path,
):
    old_path, old_url, path = _client_database(tmp_path, "writing-events-sse.sqlite3")
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-events-sse-owner")
            operation_id = _completed_operation_with_events(
                client, owner, event_count=201
            )

            response = client.get(
                f"/writing/operations/{operation_id}/events/stream?after_cursor=0",
                headers=owner,
            )
            assert response.status_code == 200, response.text
            payloads = [
                json.loads(line.removeprefix("data: "))
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            receipts = [item for item in payloads if "event_id" in item]
            assert [item["event_id"] for item in receipts] == list(range(1, 202))
            assert payloads[-1] == {
                "type": "operation_settled",
                "status": "completed",
                "next_cursor": 201,
            }
    finally:
        _restore_database(old_path, old_url, path)


def test_event_persistence_never_advances_cursor_past_a_failed_receipt(
    tmp_path: Path, monkeypatch
):
    from app.services.pi_writing_provider import _persist_pi_runtime_events

    old_path, old_url, path = _client_database(
        tmp_path, "writing-events-receipt-failure.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-events-failure-owner")
            operation_id = _completed_operation_with_events(
                client, owner, event_count=0
            )
            monkeypatch.setattr(
                "app.services.pi_writing_provider.writing.record_agent_event",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    ValueError("receipt conflict")
                ),
            )

            with pytest.raises(ValueError, match="receipt conflict"):
                _persist_pi_runtime_events(
                    operation_id=operation_id,
                    scope="outline",
                    events=[{"type": "agent_start"}],
                    agent_name="翠花",
                )

            with SessionLocal() as db:
                operation = writing_repo.operation_by_id(db, operation_id)
                assert operation is not None
                assert operation.last_event_cursor == 0
    finally:
        _restore_database(old_path, old_url, path)


def test_from_chat_allows_empty_space_ids(tmp_path: Path):
    old_path, old_url, path = _client_database(
        tmp_path, "writing-chat-no-space.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-chat-no-space")
            created = client.post(
                "/writing/projects/from-chat",
                headers=owner,
                json={
                    "title": "冒泡排序草稿",
                    "space_ids": [],
                    "seed_markdown": "这是没有引用资料的聊天初稿。",
                },
            )
            assert created.status_code == 201, created.text
            project = created.json()
            assert project["space_ids"] == []
            assert project["outline"]["chapters"][0]["content"] == (
                "这是没有引用资料的聊天初稿。"
            )
    finally:
        _restore_database(old_path, old_url, path)


def test_chat_seed_is_not_a_saved_version_and_survives_outline_candidate_publish(
    tmp_path: Path,
):
    old_path, old_url, path = _client_database(tmp_path, "writing-chat-seed.sqlite3")
    try:
        with TestClient(app) as client:
            owner, scope = _scoped_member(client, "writing-chat-seed-owner")
            created = client.post(
                "/writing/projects/from-chat",
                headers=owner,
                json={
                    "title": "聊天沉淀文稿",
                    "space_ids": [scope["id"]],
                    "seed_markdown": "这是聊天回复形成的可编辑初稿。",
                },
            )
            assert created.status_code == 201, created.text
            project = created.json()
            assert project["versions"] == []
            assert project["outline"]["chapters"][0]["content"] == (
                "这是聊天回复形成的可编辑初稿。"
            )

            with SessionLocal() as db:
                db_project = writing_repo.owned_project(
                    db, project["id"], scope["user_id"]
                )
                assert db_project is not None
                operation = writing_repo.create_operation(db, db_project, "outline")
                candidate = writing_repo.save_candidate(
                    db,
                    operation=operation,
                    platform_run_id="run-chat-seed-outline",
                    tool_call_id="tool-chat-seed-outline",
                    kind="outline",
                    chapter_id=None,
                    payload={"chapters": [{"title": "正式大纲"}]},
                )
                db.commit()
                candidate_id = candidate.id

            published = client.post(
                f"/writing/projects/{project['id']}/outline-candidates/{candidate_id}:publish",
                headers=owner,
            )
            assert published.status_code == 200, published.text
            result = published.json()
            assert result["versions"] == []
            assert result["outline"]["chapters"][0]["title"] == "正式大纲"
            assert result["outline"]["chapters"][0]["content"] == (
                "这是聊天回复形成的可编辑初稿。"
            )

            # 模拟修复前已创建正文版本的旧项目，采用大纲时仍应兼容保留正文。
            chapter_id = result["outline"]["chapters"][0]["id"]
            saved = client.put(
                f"/writing/projects/{project['id']}/chapters/{chapter_id}",
                headers=owner,
                json={"content": "这是聊天回复形成的可编辑初稿。"},
            )
            assert saved.status_code == 201, saved.text

            with SessionLocal() as db:
                db_project = writing_repo.owned_project(
                    db, project["id"], scope["user_id"]
                )
                assert db_project is not None
                operation = writing_repo.create_operation(db, db_project, "outline")
                candidate = writing_repo.save_candidate(
                    db,
                    operation=operation,
                    platform_run_id="run-chat-seed-outline-legacy",
                    tool_call_id="tool-chat-seed-outline-legacy",
                    kind="outline",
                    chapter_id=None,
                    payload={"chapters": [{"title": "兼容大纲"}]},
                )
                db.commit()
                candidate_id = candidate.id

            published = client.post(
                f"/writing/projects/{project['id']}/outline-candidates/{candidate_id}:publish",
                headers=owner,
            )
            assert published.status_code == 200, published.text
            result = published.json()
            assert len(result["versions"]) == 1
            assert result["outline"]["chapters"][0]["title"] == "兼容大纲"
            assert result["outline"]["chapters"][0]["content"] == (
                "这是聊天回复形成的可编辑初稿。"
            )
    finally:
        _restore_database(old_path, old_url, path)


def test_outline_must_be_confirmed_before_batch_generation(tmp_path: Path):
    old_path, old_url, path = _client_database(tmp_path, "writing-outline.sqlite3")
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-outline-owner")
            project = client.post(
                "/writing/projects",
                headers=owner,
                json={"title": "上市材料", "space_ids": []},
            ).json()
            project_id = project["id"]
            outline = _replace_outline(
                client,
                project_id,
                owner,
                [{"title": "市场机会"}, {"title": "产品定位"}],
            )
            assert outline["outline"]["status"] == "draft"
            assert (
                client.post(
                    f"/writing/projects/{project_id}/chapters:generate", headers=owner
                ).status_code
                == 409
            )

            confirmed = client.post(
                f"/writing/projects/{project_id}/outline:confirm", headers=owner
            )
            assert confirmed.status_code == 200
            operation = client.post(
                f"/writing/projects/{project_id}/chapters:generate", headers=owner
            )
            assert operation.status_code == 201
            assert operation.json()["status"] == "completed"
            assert len(operation.json()["result"]["version_ids"]) == 2
            reread = client.get(f"/writing/projects/{project_id}", headers=owner).json()
            assert {item["id"] for item in reread["versions"]} == set(
                operation.json()["result"]["version_ids"]
            )
            assert all(chapter["content"] for chapter in reread["outline"]["chapters"])
    finally:
        _restore_database(old_path, old_url, path)


def test_unsupported_claims_are_reported_by_preflight_and_export_marks_draft(tmp_path: Path):
    old_path, old_url, path = _client_database(tmp_path, "writing-export.sqlite3")
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-export-owner")
            project = client.post(
                "/writing/projects",
                headers=owner,
                json={"title": "投标书", "space_ids": []},
            ).json()
            project_id = project["id"]
            claim = client.post(
                f"/writing/projects/{project_id}/claims",
                headers=owner,
                json={"text": "产品支持全部监管要求", "support_state": "unsupported"},
            )
            assert claim.status_code == 201
            claim_id = claim.json()["id"]

            preflight = client.post(
                f"/writing/projects/{project_id}/exports:preflight", headers=owner
            )
            assert preflight.status_code == 200
            assert preflight.json()["blocking_claim_ids"] == [claim_id]
            # 导出改为提示：未支持的声明不再 409，但导出文件标记为未核验草稿。
            unverified = client.post(
                f"/writing/projects/{project_id}/exports", headers=owner
            )
            assert unverified.status_code == 201, unverified.text
            assert unverified.json()["result"]["document_status"] == "draft"
            persisted = client.get(
                f"/writing/projects/{project_id}", headers=owner
            ).json()
            assert persisted["doc_status"] == "draft"
            assert persisted["doc_number"] is None
    finally:
        _restore_database(old_path, old_url, path)


def test_saved_version_blocks_destructive_outline_replacement_and_remains_readable(
    tmp_path: Path,
):
    old_path, old_url, path = _client_database(
        tmp_path, "writing-version-immutable.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-version-owner")
            project = client.post(
                "/writing/projects",
                headers=owner,
                json={"title": "方案书", "space_ids": []},
            ).json()
            project_id = project["id"]
            outline = _replace_outline(
                client, project_id, owner, [{"title": "执行摘要"}]
            )
            chapter_id = outline["outline"]["chapters"][0]["id"]

            saved = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=owner,
                json={"content": "这是一份已保存的不可变正文。"},
            )
            assert saved.status_code == 201
            version_id = saved.json()["result"]["version_id"]
            rejected = client.put(
                f"/writing/projects/{project_id}/outline",
                headers=owner,
                json={"chapters": [{"title": "替换后的大纲"}]},
            )
            assert rejected.status_code == 409
            reread = client.get(f"/writing/projects/{project_id}", headers=owner).json()
            assert [(item["id"], item["content"]) for item in reread["versions"]] == [
                (version_id, "这是一份已保存的不可变正文。")
            ]
    finally:
        _restore_database(old_path, old_url, path)


def test_restore_version_preserves_unsaved_content_and_creates_a_restore_record(
    tmp_path: Path,
):
    old_path, old_url, path = _client_database(
        tmp_path, "writing-version-restore.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-version-restore-owner")
            project = client.post(
                "/writing/projects",
                headers=owner,
                json={"title": "可恢复方案书", "space_ids": []},
            ).json()
            project_id = project["id"]
            outline = _replace_outline(
                client, project_id, owner, [{"title": "执行摘要"}]
            )
            chapter_id = outline["outline"]["chapters"][0]["id"]

            original = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=owner,
                json={"content": "第一版正文"},
            ).json()
            client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=owner,
                json={"content": "当前已保存正文"},
            )

            restored = client.post(
                f"/writing/projects/{project_id}/chapters/{chapter_id}/versions/{original['result']['version_id']}:restore",
                headers=owner,
                json={"current_content": "当前未保存正文"},
            )
            assert restored.status_code == 201, restored.text
            result = restored.json()
            assert result["kind"] == "version_restore"
            assert result["status"] == "completed"
            assert result["result"]["backup_version_id"]

            reread = client.get(f"/writing/projects/{project_id}", headers=owner).json()
            assert reread["outline"]["chapters"][0]["content"] == "第一版正文"
            versions = {item["id"]: item for item in reread["versions"]}
            assert versions[original["result"]["version_id"]]["content"] == "第一版正文"
            backup = versions[result["result"]["backup_version_id"]]
            assert (backup["content"], backup["kind"]) == (
                "当前未保存正文",
                "restore_backup",
            )
            restore_record = versions[result["result"]["version_id"]]
            assert (restore_record["content"], restore_record["kind"]) == (
                "第一版正文",
                "version_restore",
            )
    finally:
        _restore_database(old_path, old_url, path)


def test_save_generation_and_export_stop_safely_when_project_scope_is_revoked(
    tmp_path: Path,
    monkeypatch,
):
    old_path, old_url, path = _client_database(
        tmp_path, "writing-revoked-scope.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner, scope = _scoped_member(client, "writing-revoked-owner")
            project = client.post(
                "/writing/projects",
                headers=owner,
                json={"title": "受限材料", "space_ids": [scope["id"]]},
            ).json()
            project_id = project["id"]
            outline = _replace_outline(client, project_id, owner, [{"title": "第一章"}])
            chapter_id = outline["outline"]["chapters"][0]["id"]
            assert (
                client.post(
                    f"/writing/projects/{project_id}/outline:confirm", headers=owner
                ).status_code
                == 200
            )
            assert (
                client.delete(
                    f"/knowledge-spaces/{scope['id']}/members/{scope['user_id']}",
                    headers=_admin_headers(client),
                ).status_code
                == 204
            )
            monkeypatch.setattr(settings, "writing_evidence_provider", "hybrid")
            monkeypatch.setattr(
                "app.services.pi_writing_provider.writing_evidence.search",
                lambda *_args, **_kwargs: pytest.fail(
                    "revoked scope must not reach evidence retrieval"
                ),
            )
            with SessionLocal() as db:
                stored_project = db.get(WritingProject, project_id)
                stored_user = db.get(User, scope["user_id"])
                assert stored_project is not None
                assert stored_user is not None
                operation = writing_repo.create_operation(
                    db,
                    stored_project,
                    "document",
                    input_payload={"actor_user_id": stored_user.id},
                )
                with pytest.raises(WritingScopeUnavailableError):
                    _search_evidence(
                        db,
                        stored_project,
                        operation,
                        chapter_id=chapter_id,
                        purpose="document",
                    )

            for response in (
                client.put(
                    f"/writing/projects/{project_id}/chapters/{chapter_id}",
                    headers=owner,
                    json={"content": "不得保存"},
                ),
                client.post(
                    f"/writing/projects/{project_id}/chapters:generate", headers=owner
                ),
                client.post(f"/writing/projects/{project_id}/exports", headers=owner),
            ):
                assert response.status_code == 201
                assert response.json()["status"] == "stopped"
                assert response.json()["usage"]["total_tokens"] == 0
                assert response.json()["result"] == {
                    "reason": "scope_no_longer_available"
                }
    finally:
        _restore_database(old_path, old_url, path)


def test_export_creates_an_immutable_snapshot_artifact(tmp_path: Path):
    old_path, old_url, path = _client_database(
        tmp_path, "writing-export-snapshot.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-export-snapshot-owner")
            project = client.post(
                "/writing/projects",
                headers=owner,
                json={"title": "交付稿", "space_ids": []},
            ).json()
            project_id = project["id"]
            outline = _replace_outline(client, project_id, owner, [{"title": "摘要"}])
            chapter_id = outline["outline"]["chapters"][0]["id"]
            assert (
                client.put(
                    f"/writing/projects/{project_id}/chapters/{chapter_id}",
                    headers=owner,
                    json={"content": ("用于导出的已保存正文。\n\n- 第一项\n- 第二项")},
                ).status_code
                == 201
            )

            exported = client.post(
                f"/writing/projects/{project_id}/exports", headers=owner
            )
            assert exported.status_code == 201
            assert exported.json()["status"] == "completed"
            assert exported.json()["result"]["artifact_id"].startswith("art_")
            assert exported.json()["result"]["format"] == "docx"
            download = client.post(
                f"/artifacts/{exported.json()['result']['artifact_id']}/download",
                headers=owner,
            )
            assert download.status_code == 200, download.text
            with ZipFile(BytesIO(download.content)) as archive:
                document = ElementTree.fromstring(archive.read("word/document.xml"))
                styles = archive.read("word/styles.xml").decode("utf-8")
            paragraph_text = "\n".join(
                item.text or ""
                for item in document.iter(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                )
            )
            assert "交付稿" in paragraph_text
            assert "摘要" in paragraph_text
            assert "用于导出的已保存正文" in paragraph_text
            assert "第一项" in paragraph_text
            assert 'w:eastAsia="Noto Sans SC"' in styles

            issued = client.get(f"/writing/projects/{project_id}", headers=owner).json()
            assert issued["doc_status"] == "issued"
            assert issued["doc_number"]
            assert issued["doc_number"].startswith("DOC-")
            assert issued["issued_at"]

            # Re-exporting keeps the identity; the number is allocated once.
            assert (
                client.post(
                    f"/writing/projects/{project_id}/exports", headers=owner
                ).status_code
                == 201
            )
            reissued = client.get(
                f"/writing/projects/{project_id}", headers=owner
            ).json()
            assert reissued["doc_number"] == issued["doc_number"]

            # Editing an issued document returns it to draft so the number never
            # describes content that changed after it was handed out.
            assert (
                client.put(
                    f"/writing/projects/{project_id}/chapters/{chapter_id}",
                    headers=owner,
                    json={"content": "定稿后又被修改的正文。"},
                ).status_code
                == 201
            )
            revised = client.get(
                f"/writing/projects/{project_id}", headers=owner
            ).json()
            assert revised["doc_status"] == "draft"
            assert revised["doc_number"] == issued["doc_number"]
    finally:
        _restore_database(old_path, old_url, path)


def test_unknown_writing_type_cannot_export_pptx(tmp_path: Path):
    old_path, old_url, path = _client_database(tmp_path, "writing-unknown-pptx.sqlite3")
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-unknown-pptx-owner")
            project = client.post(
                "/writing/projects",
                headers=owner,
                json={
                    "title": "未知类型",
                    "space_ids": [],
                    "brief": {"document_type": "custom"},
                },
            ).json()
            assert project["supported_export_formats"] == ["docx"]

            response = client.post(
                f"/writing/projects/{project['id']}/exports:preflight",
                headers=owner,
                json={"format": "pptx"},
            )

            assert response.status_code == 422
            assert "不支持PPTX" in response.json()["detail"]
    finally:
        _restore_database(old_path, old_url, path)


def test_owner_can_delete_writing_project_and_others_cannot(tmp_path: Path):
    old_path, old_url, path = _client_database(tmp_path, "writing-delete.sqlite3")
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-delete-owner")
            other = _member_headers(client, "writing-delete-other")
            created = client.post(
                "/writing/projects",
                headers=owner,
                json={"title": "待删除文稿", "space_ids": []},
            )
            assert created.status_code == 201
            project_id = created.json()["id"]

            assert (
                client.delete(
                    f"/writing/projects/{project_id}", headers=other
                ).status_code
                == 404
            )
            deleted = client.delete(f"/writing/projects/{project_id}", headers=owner)
            assert deleted.status_code == 204
            assert (
                client.get(f"/writing/projects/{project_id}", headers=owner).status_code
                == 404
            )
            assert [
                item["id"]
                for item in client.get("/writing/projects", headers=owner).json()[
                    "items"
                ]
            ] == []
            assert (
                client.delete(
                    f"/writing/projects/{project_id}", headers=owner
                ).status_code
                == 404
            )
    finally:
        _restore_database(old_path, old_url, path)


def test_publishing_section_candidate_preserves_unversioned_current_body(
    tmp_path: Path,
):
    old_path, old_url, path = _client_database(
        tmp_path, "writing-candidate-backup.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner, user = _member_login(client, "writing-candidate-backup-owner")
            project = client.post(
                "/writing/projects",
                headers=owner,
                json={"title": "自动保留原文", "space_ids": []},
            ).json()
            project_id = project["id"]
            outline = _replace_outline(
                client, project_id, owner, [{"title": "核心产品"}]
            )
            chapter_id = outline["outline"]["chapters"][0]["id"]
            assert (
                client.post(
                    f"/writing/projects/{project_id}/outline:confirm", headers=owner
                ).status_code
                == 200
            )

            with SessionLocal() as db:
                db_project = writing_repo.owned_project(db, project_id, user["id"])
                assert db_project is not None
                assert (
                    writing_repo.seed_chapter(
                        db, db_project, chapter_id, "尚无版本快照的原正文"
                    )
                    is not None
                )
                operation = writing_repo.create_operation(db, db_project, "document")
                candidate = writing_repo.save_candidate(
                    db,
                    operation=operation,
                    platform_run_id="run-regeneration",
                    tool_call_id="tool-regeneration",
                    kind="section",
                    chapter_id=chapter_id,
                    payload={"markdown": "重新生成后的正文"},
                )
                db.commit()
                candidate_id = candidate.id

            published = client.post(
                f"/writing/projects/{project_id}/section-candidates/{candidate_id}:publish",
                headers=owner,
            )
            assert published.status_code == 200, published.text
            assert published.json()["content"] == "重新生成后的正文"

            reread = client.get(f"/writing/projects/{project_id}", headers=owner).json()
            assert reread["outline"]["chapters"][0]["content"] == "重新生成后的正文"
            versions = {(item["kind"], item["content"]) for item in reread["versions"]}
            assert versions == {
                ("regeneration_backup", "尚无版本快照的原正文"),
                ("agent_candidate", "重新生成后的正文"),
            }
    finally:
        _restore_database(old_path, old_url, path)


def test_stale_section_candidate_is_flagged_and_requires_force_to_publish(
    tmp_path: Path,
):
    from datetime import timedelta

    from app.core.security import now

    old_path, old_url, path = _client_database(
        tmp_path, "writing-stale-candidate.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner, user = _member_login(client, "writing-stale-owner")
            project = client.post(
                "/writing/projects",
                headers=owner,
                json={"title": "过期候选", "space_ids": []},
            ).json()
            project_id = project["id"]
            outline = _replace_outline(
                client, project_id, owner, [{"title": "核心产品"}]
            )
            chapter_id = outline["outline"]["chapters"][0]["id"]
            assert (
                client.post(
                    f"/writing/projects/{project_id}/outline:confirm", headers=owner
                ).status_code
                == 200
            )

            with SessionLocal() as db:
                db_project = writing_repo.owned_project(db, project_id, user["id"])
                assert db_project is not None
                operation = writing_repo.create_operation(db, db_project, "document")
                candidate = writing_repo.save_candidate(
                    db,
                    operation=operation,
                    platform_run_id="run-stale",
                    tool_call_id="tool-stale",
                    kind="section",
                    chapter_id=chapter_id,
                    payload={"markdown": "候选正文"},
                )
                candidate.created_at = now() - timedelta(minutes=5)
                db.commit()
                candidate_id = candidate.id

            assert (
                client.put(
                    f"/writing/projects/{project_id}/chapters/{chapter_id}",
                    headers=owner,
                    json={"content": "用户后来改过的正文"},
                ).status_code
                == 201
            )

            listed = client.get(
                f"/writing/projects/{project_id}/section-candidates", headers=owner
            )
            assert listed.status_code == 200, listed.text
            item = next(row for row in listed.json() if row["id"] == candidate_id)
            assert item["stale"] is True

            blocked = client.post(
                f"/writing/projects/{project_id}/section-candidates/{candidate_id}:publish",
                headers=owner,
            )
            assert blocked.status_code == 409
            assert "覆盖当前正文" in blocked.json()["detail"]

            forced = client.post(
                f"/writing/projects/{project_id}/section-candidates/{candidate_id}:publish?force=true",
                headers=owner,
            )
            assert forced.status_code == 200, forced.text
            assert forced.json()["content"] == "候选正文"
            reread = client.get(f"/writing/projects/{project_id}", headers=owner).json()
            assert reread["outline"]["chapters"][0]["content"] == "候选正文"
    finally:
        _restore_database(old_path, old_url, path)


def test_pptx_export_creates_editable_deck_manifest_and_stream_closes(
    tmp_path: Path,
):
    old_path, old_url, path = _client_database(tmp_path, "writing-pptx-export.sqlite3")
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-pptx-owner")
            project = client.post(
                "/writing/projects",
                headers=owner,
                json={
                    "title": "项目方案汇报",
                    "space_ids": [],
                    "brief": {
                        "document_type": "proposal",
                        "audience": "管理层",
                    },
                },
            ).json()
            project_id = project["id"]
            outline = _replace_outline(
                client,
                project_id,
                owner,
                [
                    {"title": "现状与问题"},
                    {"title": "总体方案"},
                    {"title": "实施计划"},
                ],
            )
            for chapter, content in zip(
                outline["outline"]["chapters"],
                [
                    "当前有 3 个问题，需要统一流程。\\n- 成本上升 20%",
                    "采用分阶段实施方案。\\n- 建立统一平台\\n- 明确责任边界",
                    "第一阶段完成准备，第二阶段上线，第三阶段验收。",
                ],
                strict=True,
            ):
                saved = client.put(
                    f"/writing/projects/{project_id}/chapters/{chapter['id']}",
                    headers=owner,
                    json={"content": content},
                )
                assert saved.status_code == 201, saved.text

            exported = client.post(
                f"/writing/projects/{project_id}/exports",
                headers=owner,
                json={"format": "pptx"},
            )
            assert exported.status_code == 201, exported.text
            operation = exported.json()
            assert operation["result"]["format"] == "pptx"
            artifact_id = operation["result"]["artifact_id"]

            artifact = client.get(f"/artifacts/{artifact_id}", headers=owner)
            assert artifact.status_code == 200, artifact.text
            body = artifact.json()
            assert body["name"] == "项目方案汇报.pptx"
            assert body["content_type"].endswith("presentationml.presentation")
            assert body["manifest"]["format"] == "pptx"
            assert body["manifest"]["renderer_version"] == "presentation-deck/1"
            assert body["manifest"]["content_sha256"] == body["checksum_sha256"]
            assert len(body["manifest"]["source_sha256"]) == 64
            assert len(body["manifest"]["deck_spec"]["slides"]) == 5

            download = client.post(f"/artifacts/{artifact_id}/download", headers=owner)
            assert download.status_code == 200, download.text
            presentation = Presentation(BytesIO(download.content))
            assert len(presentation.slides) == 5
            slide_text = "\n".join(
                shape.text
                for slide in presentation.slides
                for shape in slide.shapes
                if hasattr(shape, "text")
            )
            assert "总体方案" in slide_text
            from app.services.pi_writing_provider import _persist_pi_runtime_events

            _persist_pi_runtime_events(
                operation_id=operation["id"],
                scope="document:chapter-1",
                events=[
                    {"type": "agent_start"},
                    {
                        "type": "tool_execution_start",
                        "toolName": "nexus_search_evidence",
                    },
                    {
                        "type": "message_update",
                        "assistantMessageEvent": {
                            "type": "text_delta",
                            "delta": "正在生成",
                        },
                    },
                ],
                agent_name="翠花",
            )
            listed_events = client.get(
                f"/writing/operations/{operation['id']}/events?after_cursor=0",
                headers=owner,
            )
            assert listed_events.status_code == 200, listed_events.text
            assert listed_events.json()["next_cursor"] == 3
            assert listed_events.json()["items"][1]["stage"] == "evidence"
            assert listed_events.json()["items"][1]["chapter_id"] == "chapter-1"
            assert listed_events.json()["items"][2]["payload"]["delta"] == "正在生成"

            events = client.get(
                f"/writing/operations/{operation['id']}/events/stream",
                headers=owner,
            )
            assert events.status_code == 200
            assert events.headers["content-type"].startswith("text/event-stream")
            assert '"type": "operation_settled"' in events.text
    finally:
        _restore_database(old_path, old_url, path)


@pytest.mark.parametrize(
    ("citation_id", "code"),
    [
        (f"writing-evidence:ver_unknown:{'a' * 64}:0-8", "unknown_citation"),
        ("writing-evidence:ver_historical:0-8", "historical_snapshot_unavailable"),
        ("citation:0", "malformed_citation"),
    ],
)
def test_invalid_source_binding_saves_draft_but_blocks_export_and_publication(
    tmp_path: Path,
    citation_id: str,
    code: str,
):
    old_path, old_url, path = _client_database(
        tmp_path, "invalid-source-export.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "invalid-source-owner")
            project = client.post(
                "/writing/projects/from-chat",
                headers=owner,
                json={
                    "title": "未核验草稿",
                    "seed_markdown": f"保留原文。[{citation_id}]",
                },
            ).json()
            assert project["doc_status"] == "draft"
            assert f"[{citation_id}]" in project["outline"]["chapters"][0]["content"]
            preflight = client.post(
                f"/writing/projects/{project['id']}/exports:preflight",
                headers=owner,
            ).json()
            assert preflight["can_export"] is False
            assert {"code": code, "citation_id": citation_id} in preflight[
                "evidence_blockers"
            ]
            # 未通过核验仍可导出 draft；只读快照发布入口已移除。
            unverified_export = client.post(
                f"/writing/projects/{project['id']}/exports",
                headers=owner,
            )
            assert unverified_export.status_code == 201, unverified_export.text
            assert unverified_export.json()["result"]["document_status"] == "draft"
            assert (
                client.post(
                    f"/writing/projects/{project['id']}/publish",
                    headers=owner,
                    json={},
                ).status_code
                == 404
            )
            persisted = client.get(
                f"/writing/projects/{project['id']}", headers=owner
            ).json()
            assert persisted["doc_status"] == "draft"
            assert persisted["doc_number"] is None
    finally:
        _restore_database(old_path, old_url, path)


def test_from_chat_reports_unbound_facts_at_save(tmp_path, monkeypatch):
    """旧标签不猜绑定；人工选择可信来源保存后可导出，刷新与撤权会重新核验。"""
    from app.core.security import now
    from app.models.documents import Document, DocumentVersion
    from app.repositories import chat as chat_repo
    from app.services.evidence_snapshots import freeze_text, make_citation_id

    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    old_path, old_url, path = _client_database(tmp_path, "chat-save-binding.sqlite3")
    try:
        with TestClient(app) as client:
            owner, scope = _scoped_member(client, "save-binding-owner")
            session_id = client.post(
                "/chat/sessions", headers=owner, json={"title": "指标问答"}
            ).json()["id"]
            seed = "响应时间≤500ms ^[报价文档]"
            original = "响应时间不超过500ms"
            digest = freeze_text("ver_save", original)
            citation_id = make_citation_id("ver_save", digest, 0, len(original))
            with SessionLocal() as db:
                stamp = now()
                db.add(Document(
                    id="doc_save", space_id=scope["id"], name="报价文档",
                    status="active", created_by_user_id=scope["user_id"],
                    created_at=stamp, updated_at=stamp,
                ))
                db.add(DocumentVersion(
                    id="ver_save", document_id="doc_save", version_number=1,
                    file_path=str(tmp_path / "quote.txt"), processing_stage="ready",
                    queryable=True, is_current_published=True, created_at=stamp, updated_at=stamp,
                ))
                session = chat_repo.get_owned_session(
                    db, session_id, scope["user_id"]
                )
                chat_repo.append_messages(db, session, [
                    {
                        "id": "bind-user",
                        "role": "user",
                        "content": "给指标",
                        "space_ids": [scope["id"]],
                    },
                    {
                        "id": "bind-assistant",
                        "role": "assistant",
                        "content": seed,
                        # 检索记录存在，但正文里只有展示标签，没有真实 citation ID。
                        "references": [{
                            "id": citation_id,
                            "document_id": "doc_save",
                            "document_version_id": "ver_save",
                            "document_name": "不能信任的标题",
                            "text_sha256": digest,
                            "char_start": 0,
                            "char_end": len(original),
                            "excerpt": "不能信任的摘录",
                        }],
                    },
                ])
                db.commit()

            created = client.post(
                "/writing/projects/from-chat",
                headers=owner,
                json={
                    "title": "未绑定草稿",
                    "seed_markdown": seed,
                    "source_chat_session_id": session_id,
                    "source_chat_assistant_message_id": "bind-assistant",
                },
            )
            assert created.status_code == 201, created.text
            project = created.json()
            # 草稿保存成功，但创建响应立即说明事实未绑定，不必等到正式导出才发现。
            binding = project["citation_binding"]
            assert binding["blocking_reasons"] == ["missing_inline_bindings"]
            assert binding["issue_count"] == 1
            assert project["outline"]["chapters"][0]["content"] == seed

            project_url = f"/writing/projects/{project['id']}"
            # 正式闸门保持原样：同源检查仍然阻断导出。
            preflight = client.post(
                f"{project_url}/exports:preflight", headers=owner
            ).json()
            assert preflight["can_export"] is False
            assert "missing_inline_bindings" in preflight["blocking_reasons"]
            assert client.get(project_url, headers=owner).json()["citation_binding"] is None

            chapter = project["outline"]["chapters"][0]
            chapter_url = f"{project_url}/chapters/{chapter['id']}"
            bound = f"{seed} ^[{citation_id}]"
            saved = client.put(chapter_url, headers=owner, json={
                "content": bound, "base_revision": chapter["revision"],
            })
            assert saved.status_code == 201, saved.text
            current = client.get(project_url, headers=owner).json()
            assert current["outline"]["chapters"][0]["content"] == bound
            assert client.post(f"{project_url}/exports:preflight", headers=owner).json()["can_export"] is True
            assert client.put(chapter_url, headers=owner, json={
                "content": seed, "base_revision": chapter["revision"],
            }).status_code == 409

            # 来源失效后正式导出仍被阻断，且不会保留旧候选。
            with SessionLocal() as db:
                db.get(Document, "doc_save").status = "deleted"
                db.commit()
            revoked = client.post(f"{project_url}/exports:preflight", headers=owner).json()
            assert revoked["can_export"] is False
            assert revoked["blocking_reasons"]
            unverified_export = client.post(f"{project_url}/exports", headers=owner)
            assert unverified_export.status_code == 201, unverified_export.text
            assert unverified_export.json()["result"]["document_status"] == "draft"

            # 有资料范围、正文纯叙述：状态已计算且为空，区别于“不适用”的 null。
            clean = client.post(
                "/writing/projects/from-chat",
                headers=owner,
                json={
                    "title": "纯叙述草稿",
                    "space_ids": [scope["id"]],
                    "seed_markdown": "这是一段不含数字与日期的叙述。",
                },
            ).json()
            assert clean["citation_binding"]["blocking_reasons"] == []

            # 无资料范围也无聊天来源：正式预检会跳过证据核验，保存摘要不适用。
            skipped = client.post(
                "/writing/projects/from-chat",
                headers=owner,
                json={"title": "无范围草稿", "seed_markdown": "响应时间500ms。"},
            ).json()
            assert skipped["citation_binding"] is None

    finally:
        _restore_database(old_path, old_url, path)


def test_chat_import_export_requires_bound_facts_and_authorized_source_titles(tmp_path, monkeypatch):
    from app.core.security import now
    from app.models.documents import Document, DocumentVersion
    from app.repositories import chat as chat_repo
    from app.services.evidence_snapshots import freeze_text, make_citation_id
    from sqlalchemy import select

    from app.models.chat import ChatMessage
    from app.models.writing import WritingOperation

    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    old_path, old_url, path = _client_database(tmp_path, "chat-quote-binding.sqlite3")
    try:
        with TestClient(app) as client:
            owner, scope = _scoped_member(client, "quote-binding-owner")
            original = "慧云科技于2026年发布86寸设备：1-5台每台152,000元；年费12,000元。网络2.4G。"
            digest = freeze_text("ver_quote_binding", original)
            citation_id = make_citation_id("ver_quote_binding", digest, 0, len(original))
            title = "20260122 企业数字员工标准产品报价"
            source = {
                "id": citation_id, "document_id": "doc_quote_binding",
                "document_version_id": "ver_quote_binding",
                "document_name": "伪造名称999999",
                "verified_document_name": "伪造名称999999",
                "text_sha256": digest, "char_start": 0, "char_end": len(original),
                "excerpt": original,
            }
            with SessionLocal() as db:
                stamp = now()
                db.add(Document(
                    id="doc_quote_binding", space_id=scope["id"], name=title,
                    status="active", created_by_user_id=scope["user_id"],
                    created_at=stamp, updated_at=stamp,
                ))
                db.add(DocumentVersion(
                    id="ver_quote_binding", document_id="doc_quote_binding", version_number=1,
                    file_path=str(tmp_path / "quote.txt"), processing_stage="ready",
                    queryable=True, is_current_published=True, created_at=stamp, updated_at=stamp,
                ))
                db.commit()
            session_id = client.post(
                "/chat/sessions", headers=owner, json={"title": "86寸报价"},
            ).json()["id"]
            body = (
                "| 数量 | 单价 |\n| --- | --- |\n| 1-5台 | 152,000元 |\n\n"
                f"^[{citation_id}]"
            )
            with SessionLocal() as db:
                session = chat_repo.get_owned_session(db, session_id, scope["user_id"])
                chat_repo.append_messages(db, session, [
                    {"id": "quote-user", "role": "user", "content": "查报价", "space_ids": [scope["id"]]},
                    {"id": "quote-assistant", "role": "assistant", "content": body, "references": [source]},
                ])
                db.commit()
            imported = client.post("/writing/projects/from-chat", headers=owner, json={
                "title": "报价核验", "seed_markdown": body,
                "source_chat_session_id": session_id, "source_chat_assistant_message_id": "quote-assistant",
            })
            assert imported.status_code == 201, imported.text
            project = imported.json()
            chapter = project["outline"]["chapters"][0]
            assert chapter["content"] == body
            assert citation_id in chapter["content"]
            project_url = f"/writing/projects/{project['id']}"
            chapter_url = f"{project_url}/chapters/{chapter['id']}"
            persisted = client.get(project_url, headers=owner).json()
            assert persisted["outline"]["chapters"][0]["content"] == body
            with SessionLocal() as db:
                manual_saves = list(
                    db.scalars(
                        select(WritingOperation).where(
                            WritingOperation.project_id == project["id"],
                            WritingOperation.kind == "manual_save",
                            WritingOperation.status == "completed",
                        )
                    )
                )
                assert len(manual_saves) == 1
                operation_result = json.loads(manual_saves[0].result or "{}")
                assert operation_result["evidence_payload"]["evidence"][0]["id"] == citation_id
                saved_source = operation_result["evidence_sources"][0]
                assert saved_source["citation_id"] == citation_id
                assert saved_source["snapshot_status"] == "verified"
                assert saved_source["source_text"] == original
                stored_message = db.get(ChatMessage, "quote-assistant")
                assert stored_message is not None
                stored_message.references_json = "[]"
                db.commit()

            blocked = client.post(f"{project_url}/exports:preflight", headers=owner).json()
            assert blocked["can_export"] is False
            assert "unsupported_content" in blocked["blocking_reasons"]
            assert "evidence_invalid" not in blocked["blocking_reasons"]
            blocker = next(item for item in blocked["evidence_blockers"] if item["code"] == "unsupported_content")
            assert any(
                issue["line_number"] == 3 and "152,000" in issue["text"]
                and issue["reason"] == "missing_inline_binding"
                for issue in blocker["support_issues"]
            )
            unverified_export = client.post(f"{project_url}/exports", headers=owner)
            assert unverified_export.status_code == 201, unverified_export.text
            assert unverified_export.json()["result"]["document_status"] == "draft"

            references_only = "| 数量 | 单价 |\n| --- | --- |\n| 1-5台 | 152,000元 |"
            assert client.put(
                chapter_url, headers=owner, json={"content": references_only}
            ).status_code == 201
            references_only_check = client.post(
                f"{project_url}/exports:preflight", headers=owner
            ).json()
            assert references_only_check["can_export"] is False
            assert "missing_inline_bindings" in references_only_check["blocking_reasons"]

            cross_paragraph = (
                "慧云科技于2026年发布86寸设备。\n\n"
                f"年费12,000元。^[{citation_id}]"
            )
            assert client.put(
                chapter_url, headers=owner, json={"content": cross_paragraph}
            ).status_code == 201
            cross_paragraph_check = client.post(
                f"{project_url}/exports:preflight", headers=owner
            ).json()
            assert cross_paragraph_check["can_export"] is False
            assert any(
                issue["line_number"] == 1
                and issue["reason"] == "missing_inline_binding"
                for item in cross_paragraph_check["evidence_blockers"]
                for issue in item.get("support_issues", [])
            )

            cross_row = (
                "| 项目 | 数值 |\n| --- | --- |\n| 设备 | 86寸 |\n"
                f"| 年费 | 12,000元 ^[{citation_id}] |"
            )
            assert client.put(
                chapter_url, headers=owner, json={"content": cross_row}
            ).status_code == 201
            cross_row_check = client.post(
                f"{project_url}/exports:preflight", headers=owner
            ).json()
            assert cross_row_check["can_export"] is False
            assert any(
                issue["line_number"] == 3
                and issue["reason"] == "missing_inline_binding"
                for item in cross_row_check["evidence_blockers"]
                for issue in item.get("support_issues", [])
            )

            bound = (
                f"慧云科技于2026年发布86寸设备。^[{citation_id}]\n\n"
                "| 数量 | 单价 |\n| --- | --- |\n"
                f"| 1-5台 | 152,000元 ^[{citation_id}] |\n\n"
                f"依据：《{title}》。^[{citation_id}]"
            )
            assert client.put(
                chapter_url, headers=owner, json={"content": bound}
            ).status_code == 201
            saved = client.get(project_url, headers=owner).json()
            assert saved["outline"]["chapters"][0]["content"] == bound
            ready = client.post(f"{project_url}/exports:preflight", headers=owner).json()
            assert ready["can_export"] is True, ready
            with SessionLocal() as db:
                assert citation_id in writing_repo.recorded_evidence_ids(
                    db, project["id"]
                )
            exported = client.post(f"{project_url}/exports", headers=owner)
            assert exported.status_code == 201, exported.text
            assert exported.json()["result"]["document_status"] == "issued"

            # 已授权标题例外只作用于标题本身，不能让同段的错误报价通过。
            wrong = f"依据：《{title}》，单价999999元。^[{citation_id}]"
            assert client.put(chapter_url, headers=owner, json={"content": wrong}).status_code == 201
            failed = client.post(f"{project_url}/exports:preflight", headers=owner).json()
            assert failed["can_export"] is False
            assert any(
                "999999" in item.get("unsupported_tokens", [])
                for item in failed["evidence_blockers"] if item["code"] == "unsupported_content"
            )
            forged = f"依据：《伪造名称999999》。^[{citation_id}]"
            assert client.put(chapter_url, headers=owner, json={"content": forged}).status_code == 201
            assert client.post(f"{project_url}/exports:preflight", headers=owner).json()["can_export"] is False
            assert client.post(f"{project_url}/exports", headers=owner).status_code == 201

            assert client.put(chapter_url, headers=owner, json={"content": bound}).status_code == 201
            with SessionLocal() as db:
                db.get(Document, "doc_quote_binding").status = "deleted"
                db.commit()
            assert client.post(f"{project_url}/exports:preflight", headers=owner).json()["can_export"] is False
            unverified_export = client.post(f"{project_url}/exports", headers=owner)
            assert unverified_export.status_code == 201, unverified_export.text
            assert unverified_export.json()["result"]["document_status"] == "draft"
    finally:
        _restore_database(old_path, old_url, path)


def test_export_and_publication_use_original_generation_snapshot_after_source_changes(
    tmp_path: Path,
    monkeypatch,
    seed_writing_publication,
):
    from app.core.security import now
    from app.models.documents import Document, DocumentVersion
    from app.services.evidence_snapshots import freeze_text, make_citation_id
    from app.services.chat_docx_delivery import deliver_chat_reply_docx

    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    old_path, old_url, path = _client_database(tmp_path, "frozen-source-export.sqlite3")
    try:
        with TestClient(app) as client:
            owner, scope = _scoped_member(client, "frozen-source-owner")
            original = (
                "软件名称：知识协作平台\n\n平台专注企业知识协作，原始证书保持有效。"
            )
            sidecar = tmp_path / "source.txt"
            sidecar.write_text(original, encoding="utf-8")
            digest = freeze_text("ver_export_source", original)
            citation_id = make_citation_id(
                "ver_export_source", digest, 0, len(original)
            )
            source = {
                "id": citation_id,
                "document_id": "doc_export_source",
                "document_version_id": "ver_export_source",
                "document_name": "原始资质证书",
                "version_number": 1,
                "text_sha256": digest,
                "char_start": 0,
                "char_end": len(original),
                "excerpt": original,
                "support_state": "supported",
            }
            content = f"平台专注企业知识协作。^[{citation_id}]"
            with SessionLocal() as db:
                stamp = now()
                db.add(
                    Document(
                        id="doc_export_source",
                        space_id=scope["id"],
                        name="原始资质证书",
                        status="active",
                        created_by_user_id=scope["user_id"],
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
                db.add(
                    DocumentVersion(
                        id="ver_export_source",
                        document_id="doc_export_source",
                        version_number=1,
                        file_path=str(sidecar),
                        processing_stage="ready",
                        queryable=True,
                        is_current_published=True,
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
                db.commit()
            created = client.post(
                "/writing/projects/from-chat",
                headers=owner,
                json={
                    "title": "快照证据报告",
                    "space_ids": [scope["id"]],
                    "seed_markdown": content,
                    "brief": {"document_type": "proposal"},
                },
            ).json()
            with SessionLocal() as db:
                project = writing_repo.owned_project(
                    db, created["id"], scope["user_id"]
                )
                operation = writing_repo.create_operation(db, project, "document")
                writing_repo.save_candidate(
                    db,
                    operation=operation,
                    platform_run_id="frozen-run",
                    tool_call_id="frozen-tool",
                    kind="section",
                    chapter_id=created["outline"]["chapters"][0]["id"],
                    payload={
                        "markdown": content,
                        "evidence_payload": {
                            "evidence": [
                                source,
                                {"id": "writing-evidence:irrelevant:0-3"},
                            ]
                        },
                    },
                )
                db.get(Document, "doc_export_source").name = "已改名资料"
                db.commit()
            sidecar.write_text(
                "现在的抽取内容已经变化，不能替换原证据。", encoding="utf-8"
            )
            preflight = client.post(
                f"/writing/projects/{created['id']}/exports:preflight",
                headers=owner,
            ).json()
            assert preflight["can_export"] is True, preflight
            exported = client.post(
                f"/writing/projects/{created['id']}/exports",
                headers=owner,
            )
            assert exported.status_code == 201, exported.text
            artifact_id = exported.json()["result"]["artifact_id"]
            artifact = client.get(f"/artifacts/{artifact_id}", headers=owner).json()
            assert artifact["manifest"]["evidence_sources"][0]["text_sha256"] == digest
            assert (
                artifact["manifest"]["evidence_sources"][0]["source_text"] == original
            )
            downloaded = client.post(
                f"/artifacts/{artifact_id}/download", headers=owner
            )
            assert downloaded.status_code == 200, downloaded.text
            with ZipFile(BytesIO(downloaded.content)) as archive:
                document_xml = archive.read("word/document.xml").decode("utf-8")
            # 交付正文不写来源：文档名与快照正文都不得出现在 DOCX 里，只保留引用过的正文。
            assert "平台专注企业知识协作" in document_xml
            assert "原始资质证书" not in document_xml
            assert original not in document_xml
            assert "writing-evidence:" not in document_xml
            assert "现在的抽取内容" not in document_xml
            deck_export = client.post(
                f"/writing/projects/{created['id']}/exports",
                headers=owner,
                json={"format": "pptx"},
            )
            assert deck_export.status_code == 201, deck_export.text
            deck_id = deck_export.json()["result"]["artifact_id"]
            deck_bytes = client.post(
                f"/artifacts/{deck_id}/download", headers=owner
            ).content
            deck = Presentation(BytesIO(deck_bytes))
            deck_text = "\n".join(
                shape.text
                for slide in deck.slides
                for shape in slide.shapes
                if hasattr(shape, "text")
            )
            # PPTX 页内与备注同样不写来源：正文保留，文档名与快照原文剔除。
            assert "平台专注企业知识协作" in deck_text
            assert "原始资质证书" not in deck_text
            assert "writing-evidence:" not in deck_text
            assert digest not in deck_text
            publication_id = seed_writing_publication(
                created["id"], evidence_sources=artifact["manifest"]["evidence_sources"],
            )
            with SessionLocal() as db:
                publication = writing_repo.get_publication(db, publication_id)
                snapshot = writing_repo.publication_snapshot(publication)
                assert snapshot["evidence_sources"][0]["source_text"] == original
                assert snapshot["evidence_sources"][0]["id"] == citation_id
                user = db.get(User, scope["user_id"])
                from app.services.evidence_export import validate_evidence_sources

                empty_grant = validate_evidence_sources(
                    db,
                    user=user,
                    texts=[content],
                    evidence=[source],
                    space_ids=[scope["id"]],
                    document_ids=[],
                )
                assert empty_grant.blockers == [
                    {
                        "code": "source_no_longer_authorized",
                        "citation_id": citation_id,
                    }
                ]
                delivered = deliver_chat_reply_docx(
                    db=db,
                    user=user,
                    reply=content,
                    title_hint="聊天来源报告",
                    evidence=[source],
                    space_ids=[scope["id"]],
                )
                db.commit()
            chat_download = client.post(
                f"/artifacts/{delivered['artifact_id']}/download",
                headers=owner,
            )
            with ZipFile(BytesIO(chat_download.content)) as archive:
                chat_xml = archive.read("word/document.xml").decode("utf-8")
            assert "平台专注企业知识协作" in chat_xml
            assert original not in chat_xml
            assert "原始资质证书" not in chat_xml
            assert "writing-evidence:" not in chat_xml
            chat_project = client.get(
                f"/writing/projects/{delivered['project_id']}",
                headers=owner,
            ).json()
            assert chat_project["doc_status"] == "draft"
            assert chat_project["doc_number"] is None
            from app.repositories import chat as chat_repo
            from app.models.chat import ChatMessage

            session_id = client.post(
                "/chat/sessions",
                headers=owner,
                json={"title": "来源会话"},
            ).json()["id"]
            with SessionLocal() as db:
                session = chat_repo.get_owned_session(db, session_id, scope["user_id"])
                chat_repo.append_messages(
                    db,
                    session,
                    [
                        {
                            "id": "source-user",
                            "role": "user",
                            "content": "请依据资料写报告",
                            "space_ids": [scope["id"]],
                            "document_ids": [],
                        },
                        {
                            "id": "source-assistant",
                            "role": "assistant",
                            "content": content,
                            "references": [source],
                        },
                    ],
                )
                db.commit()
            imported = client.post(
                "/writing/projects/from-chat",
                headers=owner,
                json={
                    "title": "编辑后沉淀",
                    "seed_markdown": f"编辑后的正文。^[{citation_id}]",
                    "source_chat_session_id": session_id,
                    "source_chat_assistant_message_id": "source-assistant",
                },
            )
            assert imported.status_code == 201, imported.text
            assert imported.json()["space_ids"] == [scope["id"]]
            assert imported.json()["outline"]["chapters"][0]["content"].startswith(
                "编辑后的正文"
            )
            with SessionLocal() as db:
                session = chat_repo.get_owned_session(db, session_id, scope["user_id"])
                chat_repo.append_messages(
                    db,
                    session,
                    [
                        {
                            "id": "accessible-user",
                            "role": "user",
                            "content": "请依据可读资料写报告",
                        },
                        {
                            "id": "accessible-assistant",
                            "role": "assistant",
                            "content": content,
                            "references": [source],
                            "space_ids": [scope["id"]],
                            "document_ids": [source["document_id"]],
                        },
                    ],
                )
                db.commit()
            accessible_import = client.post(
                "/writing/projects/from-chat",
                headers=owner,
                json={
                    "title": "自动可读范围来源",
                    "seed_markdown": content,
                    "source_chat_session_id": session_id,
                    "source_chat_assistant_message_id": "accessible-assistant",
                },
            )
            assert accessible_import.status_code == 201, accessible_import.text
            assert accessible_import.json()["document_ids"] == [source["document_id"]]
            assert (
                client.post(
                    f"/writing/projects/{accessible_import.json()['id']}/exports:preflight",
                    headers=owner,
                ).json()["can_export"]
                is True
            )
            with SessionLocal() as db:
                session = chat_repo.get_owned_session(db, session_id, scope["user_id"])
                chat_repo.append_messages(
                    db,
                    session,
                    [
                        {
                            "id": "empty-scope-user",
                            "role": "user",
                            "content": "请写报告",
                            "writing_context": {
                                "project_id": "wpr_explicit_empty_scope"
                            },
                        },
                        {
                            "id": "empty-scope-assistant",
                            "role": "assistant",
                            "content": content,
                            "references": [source],
                            "space_ids": [scope["id"]],
                            "document_ids": [source["document_id"]],
                        },
                    ],
                )
                db.commit()
            empty_project_import = client.post(
                "/writing/projects/from-chat",
                headers=owner,
                json={
                    "title": "显式空项目范围",
                    "seed_markdown": content,
                    "source_chat_session_id": session_id,
                    "source_chat_assistant_message_id": "empty-scope-assistant",
                },
            )
            assert empty_project_import.status_code == 201, empty_project_import.text
            assert empty_project_import.json()["space_ids"] == []
            assert empty_project_import.json()["document_ids"] == []
            assert (
                client.post(
                    f"/writing/projects/{empty_project_import.json()['id']}/exports:preflight",
                    headers=owner,
                ).json()["can_export"]
                is False
            )
            with SessionLocal() as db:
                db.get(ChatMessage, "source-assistant").references_json = "[]"
                db.commit()
            imported_check = client.post(
                f"/writing/projects/{imported.json()['id']}/exports:preflight",
                headers=owner,
            ).json()
            assert imported_check["can_export"] is True, imported_check
            other = _member_headers(client, "source-import-other")
            denied = client.post(
                "/writing/projects/from-chat",
                headers=other,
                json={
                    "title": "不可导入他人消息",
                    "seed_markdown": content,
                    "source_chat_session_id": session_id,
                    "source_chat_assistant_message_id": "source-assistant",
                },
            )
            assert denied.status_code == 404
            chapter_url = f"/writing/projects/{created['id']}/chapters/{created['outline']['chapters'][0]['id']}"
            assert (
                client.put(
                    chapter_url,
                    headers=owner,
                    json={"content": f"软件名称：伪造软件平台 ^[{citation_id}]"},
                ).status_code
                == 201
            )
            contradicted = client.post(
                f"/writing/projects/{created['id']}/exports:preflight",
                headers=owner,
            ).json()
            assert contradicted["can_export"] is False
            assert any(
                blocker.get("unsupported_fields")
                for blocker in contradicted["evidence_blockers"]
            )
            unverified_export = client.post(
                f"/writing/projects/{created['id']}/exports",
                headers=owner,
            )
            assert unverified_export.status_code == 201, unverified_export.text
            assert unverified_export.json()["result"]["document_status"] == "draft"
            assert (
                client.put(
                    chapter_url, headers=owner, json={"content": content}
                ).status_code
                == 201
            )
            snapshot_path = (
                settings.upload_dir
                / "_evidence_snapshots"
                / "ver_export_source"
                / f"{digest}.txt"
            )
            snapshot_path.write_text("被破坏的快照", encoding="utf-8")
            blocked = client.post(
                f"/writing/projects/{created['id']}/exports:preflight",
                headers=owner,
            ).json()
            assert blocked["can_export"] is False
            assert {
                "code": "historical_snapshot_unavailable",
                "citation_id": citation_id,
            } in blocked["evidence_blockers"]
            unverified_export = client.post(
                f"/writing/projects/{created['id']}/exports",
                headers=owner,
            )
            assert unverified_export.status_code == 201, unverified_export.text
            assert unverified_export.json()["result"]["document_status"] == "draft"
    finally:
        _restore_database(old_path, old_url, path)


def test_manual_edit_cannot_drop_applied_inventory_record_coverage(
    tmp_path: Path,
    monkeypatch,
):
    from app.core.security import now
    from app.models.documents import Document, DocumentVersion
    from app.services.evidence_snapshots import freeze_text, make_citation_id
    from app.services.writing_fact_check import inventory_records

    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    old_path, old_url, path = _client_database(
        tmp_path, "inventory-export-edits.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner, scope = _scoped_member(client, "inventory-export-owner")
            original = "软件登记号：2026SR000001\n软件登记号：2026SR000002"
            digest = freeze_text("ver_inventory_export", original)
            citation_id = make_citation_id(
                "ver_inventory_export", digest, 0, len(original)
            )
            source = {
                "id": citation_id,
                "document_id": "doc_inventory_export",
                "document_version_id": "ver_inventory_export",
                "document_name": "软件登记汇总",
                "text_sha256": digest,
                "char_start": 0,
                "char_end": len(original),
                "excerpt": original,
            }
            evidence_payload = {
                "provider": "document_inventory",
                "evidence": [source],
                "coverage": {
                    "complete": True,
                    "expected": 1,
                    "processed": 1,
                    "documents": [
                        {"document_id": source["document_id"], "status": "processed"}
                    ],
                },
            }
            records = inventory_records(evidence_payload)
            full_body = (
                f"2026SR000001 ^[{citation_id}]\n\n2026SR000002 ^[{citation_id}]"
            )
            project = client.post(
                "/writing/projects/from-chat",
                headers=owner,
                json={
                    "title": "登记清单",
                    "space_ids": [scope["id"]],
                    "seed_markdown": full_body,
                },
            ).json()
            chapter_id = project["outline"]["chapters"][0]["id"]
            with SessionLocal() as db:
                stamp = now()
                db.add(
                    Document(
                        id=source["document_id"],
                        name="软件登记汇总",
                        space_id=scope["id"],
                        status="active",
                        created_by_user_id=scope["user_id"],
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
                db.add(
                    DocumentVersion(
                        id=source["document_version_id"],
                        document_id=source["document_id"],
                        version_number=1,
                        processing_stage="ready",
                        queryable=True,
                        is_current_published=True,
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
                db_project = writing_repo.owned_project(
                    db, project["id"], scope["user_id"]
                )
                operation = writing_repo.create_operation(db, db_project, "document")
                candidate = writing_repo.save_candidate(
                    db,
                    operation=operation,
                    platform_run_id="inventory-export-run",
                    tool_call_id="inventory-export-tool",
                    kind="section",
                    chapter_id=chapter_id,
                    payload={
                        "markdown": full_body,
                        "evidence_payload": evidence_payload,
                        "inventory_reconciliation": {
                            "documents": [
                                {
                                    "document_id": source["document_id"],
                                    "disposition": "relevant",
                                    "reason": "属于本次软件登记清单",
                                    "evidence_ids": [citation_id],
                                    "records": [
                                        {
                                            "record_ref": row["record_ref"],
                                            "disposition": "relevant",
                                            "field_evidence": {
                                                "identifier": [citation_id]
                                            },
                                        }
                                        for row in records
                                    ],
                                }
                            ],
                            "emitted_record_refs": [
                                row["record_ref"] for row in records
                            ],
                        },
                    },
                )
                candidate.status = "published"
                candidate.published_at = stamp
                db.commit()
            preflight_url = f"/writing/projects/{project['id']}/exports:preflight"
            initial = client.post(preflight_url, headers=owner).json()
            assert initial["can_export"] is True, initial
            saved = client.put(
                f"/writing/projects/{project['id']}/chapters/{chapter_id}",
                headers=owner,
                json={"content": f"2026SR000001 ^[{citation_id}]"},
            )
            assert saved.status_code == 201, saved.text
            missing = client.post(preflight_url, headers=owner).json()
            assert missing["can_export"] is False
            assert "incomplete_evidence_coverage" in missing["blocking_reasons"]
            assert any(
                finding["code"] == "inventory_record_not_emitted"
                for blocker in missing["evidence_blockers"]
                for finding in blocker.get("findings", [])
            )
            unverified_export = client.post(
                f"/writing/projects/{project['id']}/exports",
                headers=owner,
            )
            assert unverified_export.status_code == 201, unverified_export.text
            assert unverified_export.json()["result"]["document_status"] == "draft"
    finally:
        _restore_database(old_path, old_url, path)


def test_temp_only_project_blocks_unsupported_export_without_citations(tmp_path: Path):
    from app.core.security import now
    from app.models.documents import Document, DocumentVersion
    from app.repositories import chat as chat_repo

    old_path, old_url, path = _client_database(tmp_path, "temp-only-export.sqlite3")
    try:
        with TestClient(app) as client:
            owner, user = _member_login(client, "temp-only-export-owner")
            session_id = client.post(
                "/chat/sessions",
                headers=owner,
                json={"title": "附件写作"},
            ).json()["id"]
            body = "软件名称：伪造平台\n登记号：2026SR009999"
            with SessionLocal() as db:
                stamp = now()
                db.add(
                    Document(
                        id="doc_temp_export",
                        name="附件证书",
                        is_temporary=True,
                        chat_session_id=session_id,
                        created_by_user_id=user["id"],
                        status="active",
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
                db.add(
                    DocumentVersion(
                        id="ver_temp_export",
                        document_id="doc_temp_export",
                        version_number=1,
                        processing_stage="ready",
                        queryable=True,
                        is_current_published=True,
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
                session = chat_repo.get_owned_session(db, session_id, user["id"])
                chat_repo.append_messages(
                    db,
                    session,
                    [
                        {
                            "id": "temp-export-user",
                            "role": "user",
                            "content": "依据附件写作",
                            "document_ids": ["doc_temp_export"],
                        },
                        {
                            "id": "temp-export-assistant",
                            "role": "assistant",
                            "content": body,
                        },
                    ],
                )
                db.commit()
            imported = client.post(
                "/writing/projects/from-chat",
                headers=owner,
                json={
                    "title": "附件文稿",
                    "seed_markdown": body,
                    "source_chat_session_id": session_id,
                    "source_chat_assistant_message_id": "temp-export-assistant",
                },
            )
            assert imported.status_code == 201, imported.text
            project = imported.json()
            assert project["space_ids"] == []
            assert project["document_ids"] == ["doc_temp_export"]
            url = f"/writing/projects/{project['id']}/exports"
            preflight = client.post(f"{url}:preflight", headers=owner)
            assert preflight.status_code == 200
            assert preflight.json()["can_export"] is False
            assert "missing_inline_bindings" in preflight.json()["blocking_reasons"]
            unverified_export = client.post(url, headers=owner)
            assert unverified_export.status_code == 201, unverified_export.text
            assert unverified_export.json()["result"]["document_status"] == "draft"
            ordinary = client.post(
                "/writing/projects/from-chat",
                headers=owner,
                json={"title": "无资料普通文稿", "seed_markdown": body},
            )
            assert ordinary.status_code == 201
            assert (
                client.post(
                    f"/writing/projects/{ordinary.json()['id']}/exports:preflight",
                    headers=owner,
                ).json()["can_export"]
                is True
            )
    finally:
        _restore_database(old_path, old_url, path)


# ── chapter preview snapshots ──────────────────────────────────────


def _preview_event_payload(
    operation_id: str,
    chapter_id: str,
    *,
    markdown: str,
    status: PreviewStatus = "streaming",
) -> dict:
    stream = ChapterPreviewStream(
        operation_id=operation_id,
        chapter_id=chapter_id,
        attempt=1,
        base_revision=1,
    )
    stream.feed(
        json.dumps(
            {"sections": [{"chapter_id": chapter_id, "markdown": markdown}]},
            ensure_ascii=False,
        )
    )
    stream.transition(status, markdown=markdown)
    return {
        "stage": "writing",
        "scope": f"document:{chapter_id}",
        "chapter_id": chapter_id,
        "preview": stream.snapshot(event_id=1, updated_at="2026-09-15T00:00:00+00:00"),
    }


def test_operation_previews_require_a_current_source_scope(tmp_path: Path):
    old_path, old_url, path = _client_database(tmp_path, "writing-previews-acl.sqlite3")
    try:
        with TestClient(app) as client:
            owner, scope = _scoped_member(client, "writing-preview-owner")
            admin = _admin_headers(client)
            outsider = _member_headers(client, "writing-preview-outsider")
            created = client.post(
                "/writing/projects",
                headers=owner,
                json={"title": "预览授权", "space_ids": [scope["id"]]},
            )
            assert created.status_code == 201, created.text
            project = client.put(
                f"/writing/projects/{created.json()['id']}/outline",
                headers=owner,
                json={"chapters": [{"title": "经营摘要"}, {"title": "风险提示"}]},
            ).json()
            chapter_a = project["outline"]["chapters"][0]["id"]
            chapter_b = project["outline"]["chapters"][1]["id"]
            client.post(
                f"/writing/projects/{created.json()['id']}/outline:confirm",
                headers=owner,
            )
            run = client.post(
                f"/writing/projects/{created.json()['id']}/document-runs",
                headers=owner,
            ).json()
            subset = client.post(
                f"/writing/projects/{created.json()['id']}/document-runs",
                headers=owner,
                json={"chapter_ids": [chapter_b]},
            ).json()
            with SessionLocal() as db:
                db.add_all(
                    [
                        writing_repo.record_agent_event(
                            db,
                            platform_run_id=f"pi-intent:{run['id']}",
                            event_id=1,
                            event_type=writing_repo.PREVIEW_EVENT_TYPE,
                            payload_sha256=f"{1:064x}",
                            payload_json=json.dumps(
                                _preview_event_payload(
                                    run["id"], chapter_a, markdown="本季度收入320万元。"
                                ),
                                ensure_ascii=False,
                            ),
                        ),
                        writing_repo.record_agent_event(
                            db,
                            platform_run_id=f"pi-intent:{run['id']}",
                            event_id=2,
                            event_type="message_update",
                            payload_sha256=f"{2:064x}",
                            payload_json=json.dumps(
                                {
                                    "stage": "writing",
                                    "scope": f"document:{chapter_a}",
                                    "delta": "本季度收入320万元。",
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    ]
                )
                operation = writing_repo.operation_by_id(db, run["id"])
                assert operation is not None
                operation.result = json.dumps({
                    "quality_scope": {
                        "space_ids": [scope["id"]],
                        "document_ids": [],
                        "brief_revision": project["brief"]["revision"],
                    },
                    "quality_by_chapter": {
                        chapter_a: {
                            "status": "needs_attention",
                            "issues": [{"text": "未公开核验原句320万元"}],
                        },
                    },
                })
                section = writing_repo.save_candidate(
                    db,
                    operation=operation,
                    platform_run_id=f"pi-intent:{run['id']}",
                    tool_call_id="private-section",
                    kind="section",
                    chapter_id=chapter_a,
                    payload={
                        "markdown": "本季度收入320万元。",
                        "evidence_payload": {"excerpt": "未公开财务资料"},
                    },
                )
                outline = writing_repo.save_candidate(
                    db,
                    operation=operation,
                    platform_run_id=f"pi-intent:{run['id']}",
                    tool_call_id="private-outline",
                    kind="outline",
                    chapter_id=None,
                    payload={"chapters": [{"title": "未公开财务资料"}]},
                )
                stored_project = db.get(WritingProject, project["id"])
                chapter = writing_repo.chapter_for_project(db, project["id"], chapter_a)
                assert stored_project is not None and chapter is not None
                _, human = writing_repo.save_human_suggestion(
                    db,
                    stored_project,
                    chapter,
                    author_user_id=scope["user_id"],
                    markdown="作者独立提出的修改建议",
                )
                section_id, outline_id, human_id = section.id, outline.id, human.id
                db.commit()

            previews = client.get(
                f"/writing/operations/{run['id']}/previews", headers=owner
            )
            assert previews.status_code == 200, previews.text
            assert previews.json()["target_chapter_ids"] == [chapter_a, chapter_b]
            assert previews.json()["items"][0]["markdown"] == "本季度收入320万元。"
            operation_url = f"/writing/operations/{run['id']}"
            detail_url = f"/writing/projects/{project['id']}"
            assert "未公开核验原句320万元" in client.get(
                operation_url, headers=owner
            ).text
            assert "未公开核验原句320万元" in client.get(
                detail_url, headers=owner
            ).text
            # 换成另一份需求后旧检查不得冒充当前结果；恢复只用于继续验证撤权。
            with SessionLocal() as db:
                stored_project = db.get(WritingProject, project["id"])
                stored_project.brief_revision += 1
                db.commit()
            assert "quality_by_chapter" not in client.get(
                detail_url, headers=owner
            ).text
            with SessionLocal() as db:
                stored_project = db.get(WritingProject, project["id"])
                stored_project.brief_revision -= 1
                db.commit()

            subset_previews = client.get(
                f"/writing/operations/{subset['id']}/previews", headers=owner
            ).json()
            assert subset_previews["target_chapter_ids"] == [chapter_b]

            listed = client.get(
                f"/writing/operations/{run['id']}/events", headers=owner
            ).json()
            assert listed["items"][0]["payload"]["preview"]["markdown"] == (
                "本季度收入320万元。"
            )
            assert listed["items"][1]["payload"]["delta"] == "本季度收入320万元。"
            candidates_url = f"/writing/projects/{project['id']}/section-candidates"
            visible_candidates = client.get(candidates_url, headers=owner).json()
            visible_section = next(
                item for item in visible_candidates if item["id"] == section_id
            )
            assert (
                visible_section["payload"]["evidence_payload"]["excerpt"]
                == "未公开财务资料"
            )

            assert (
                client.get(
                    f"/writing/operations/{run['id']}/previews", headers=outsider
                ).status_code
                == 404
            )

            revoked = client.delete(
                f"/knowledge-spaces/{scope['id']}/members/{scope['user_id']}",
                headers=admin,
            )
            assert revoked.status_code == 204, revoked.text
            for url in (operation_url, detail_url):
                response = client.get(url, headers=owner)
                assert response.status_code == 200
                assert "quality_by_chapter" not in response.text
                assert "未公开核验原句320万元" not in response.text

            denied = client.get(
                f"/writing/operations/{run['id']}/previews", headers=owner
            )
            assert denied.status_code == 403, denied.text
            assert [
                item["id"] for item in client.get(candidates_url, headers=owner).json()
            ] == [human_id]
            assert (
                client.get(
                    f"/writing/projects/{project['id']}/outline-candidates",
                    headers=owner,
                ).json()
                == []
            )
            for force in (False, True):
                blocked = client.post(
                    f"{candidates_url}/{section_id}:publish",
                    params={"force": force},
                    headers=owner,
                )
                assert blocked.status_code == 403, blocked.text
            assert (
                client.post(
                    f"{candidates_url}/{section_id}:reject", headers=owner
                ).status_code
                == 403
            )
            assert (
                client.post(
                    f"/writing/projects/{project['id']}/outline-candidates/{outline_id}:publish",
                    headers=owner,
                ).status_code
                == 403
            )
            untouched = client.get(
                f"/writing/projects/{project['id']}", headers=owner
            ).json()
            assert untouched["outline"]["chapters"][0]["content"] == ""
            with SessionLocal() as db:
                assert (
                    writing_repo.candidate_for_project(
                        db, project["id"], section_id
                    ).status
                    == "pending"
                )
            human_result = client.post(
                f"{candidates_url}/{human_id}:publish", headers=owner
            )
            assert human_result.status_code == 200, human_result.text
            assert human_result.json()["content"] == "作者独立提出的修改建议"

            redacted = client.get(
                f"/writing/operations/{run['id']}/events", headers=owner
            ).json()
            assert redacted["items"][0]["chapter_id"] == chapter_a
            assert "preview" not in redacted["items"][0]["payload"]
            assert "delta" not in redacted["items"][1]["payload"]
            cancelled = client.post(
                f"/writing/operations/{run['id']}:cancel", headers=owner
            )
            assert cancelled.status_code == 200, cancelled.text
            assert cancelled.json()["status"] == "cancelled"
            stream = client.get(
                f"/writing/operations/{run['id']}/events/stream", headers=owner
            )
            assert stream.status_code == 200
            assert "本季度收入320万元。" not in stream.text
    finally:
        _restore_database(old_path, old_url, path)


def test_operation_previews_reconcile_a_settled_chapter_without_its_terminal_snapshot(
    tmp_path: Path,
):
    from app.models.writing import WritingChapter

    old_path, old_url, path = _client_database(
        tmp_path, "writing-previews-settled.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner = _member_headers(client, "writing-preview-settled-owner")
            created = client.post(
                "/writing/projects",
                headers=owner,
                json={"title": "预览结算", "space_ids": []},
            ).json()
            project = client.put(
                f"/writing/projects/{created['id']}/outline",
                headers=owner,
                json={"chapters": [{"title": "经营摘要"}]},
            ).json()
            chapter_id = project["outline"]["chapters"][0]["id"]
            client.post(
                f"/writing/projects/{created['id']}/outline:confirm", headers=owner
            )
            run = client.post(
                f"/writing/projects/{created['id']}/document-runs", headers=owner
            ).json()
            with SessionLocal() as db:
                operation = writing_repo.operation_by_id(db, run["id"])
                assert operation is not None
                writing_repo.complete_operation(
                    db, operation, {"applied_chapter_ids": [chapter_id]}
                )
                stored_project = db.get(WritingProject, created["id"])
                assert stored_project is not None
                candidate = writing_repo.save_candidate(
                    db,
                    operation=operation,
                    platform_run_id=f"pi-intent:{run['id']}",
                    tool_call_id="settled-section",
                    kind="section",
                    chapter_id=chapter_id,
                    payload={"markdown": "已完成正文。", "base_revision": 1},
                )
                writing_repo.publish_section_candidate(
                    db, stored_project, candidate, force=True
                )
                # The terminal snapshot write was lost: only the active one is
                # left behind.
                writing_repo.record_agent_event(
                    db,
                    platform_run_id=f"pi-intent:{run['id']}",
                    event_id=1,
                    event_type=writing_repo.PREVIEW_EVENT_TYPE,
                    payload_sha256=f"{1:064x}",
                    payload_json=json.dumps(
                        _preview_event_payload(
                            run["id"],
                            chapter_id,
                            markdown="已完成正文。",
                            status="validating",
                        ),
                        ensure_ascii=False,
                    ),
                )
                db.commit()
                stored_chapter = db.get(WritingChapter, chapter_id)
                assert stored_chapter is not None
                published_revision = writing_repo.chapter_revision(stored_chapter)

            previews = client.get(
                f"/writing/operations/{run['id']}/previews", headers=owner
            ).json()
            preview = previews["items"][0]
            assert preview["status"] == "saved"
            assert preview["markdown"] == "已完成正文。"
            assert preview["candidate_id"] == candidate.id
            assert preview["revision"] == published_revision
    finally:
        _restore_database(old_path, old_url, path)


def test_pending_section_staleness_prefers_the_generation_base_revision():
    import datetime

    from app.models.writing import WritingCandidate, WritingChapter

    stamp = writing_repo.now()
    chapter = WritingChapter(
        id="wch_stale",
        outline_id="wol_stale",
        position=1,
        title="章节",
        content="人工改过的正文。",
        revision=2,
        created_at=stamp,
        updated_at=stamp,
    )
    candidate = WritingCandidate(
        id="wca_stale",
        writing_operation_id="wop_stale",
        project_id="wpr_stale",
        chapter_id=chapter.id,
        kind="section",
        candidate_revision=1,
        source_platform_run_id="pi-intent:wop_stale",
        source_tool_call_id="stale-section",
        payload_json=json.dumps({"markdown": "模型正文。", "base_revision": 1}),
        content_sha256="0" * 64,
        status="pending",
        created_at=stamp + datetime.timedelta(seconds=5),
    )
    # The candidate row is newer than the edit, so the timestamp rule alone
    # would have called it fresh while it actually pre-dates the edit.
    assert writing_repo.is_section_candidate_stale(chapter, candidate) is True

    candidate.payload_json = json.dumps(
        {"markdown": "模型正文。", "base_revision": chapter.revision}
    )
    assert writing_repo.is_section_candidate_stale(chapter, candidate) is False

    # Candidates recorded before that field existed keep the timestamp rule.
    candidate.payload_json = json.dumps({"markdown": "模型正文。"})
    assert writing_repo.is_section_candidate_stale(chapter, candidate) is False
