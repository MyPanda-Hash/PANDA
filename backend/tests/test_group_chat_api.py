from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import database
from app.core.config import settings
from app.main import app
from tests._auth import bootstrap_admin_response

ADMIN_PASSWORD = "Admin-group-tests-123"


@pytest.fixture
def client(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "group-api.sqlite3"
    database.reset_database(path)
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        database.reset_database(path)
        settings.db_path, settings.database_url = original


def _admin_headers(client: TestClient) -> dict[str, str]:
    payload = bootstrap_admin_response(client, password=ADMIN_PASSWORD).json()
    return {"Authorization": f"Bearer {payload['access_token']}"}


def _make_user(client: TestClient, admin_headers, username: str, display_name: str, password: str) -> dict[str, str]:
    created = client.post(
        "/admin/users",
        headers=admin_headers,
        json={"username": username, "display_name": display_name, "password": password},
    )
    assert created.status_code == 201, created.text
    login = client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _create_group(client: TestClient, headers, name: str = "A 型机架项目") -> dict:
    response = client.post("/groups", headers=headers, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_group_returns_owner_membership(client: TestClient):
    headers = _admin_headers(client)
    group = _create_group(client, headers)
    assert group["my_role"] == "owner"
    assert group["member_count"] == 1
    # 第 0 个固定是 AI 助手，建群人排在它后面
    assert group["members"][0]["role"] == "assistant"
    human = [m for m in group["members"] if m["role"] != "assistant"]
    assert human[0]["role"] == "owner"


def test_outsider_cannot_read_group_messages(client: TestClient):
    admin = _admin_headers(client)
    group = _create_group(client, admin)
    outsider = _make_user(client, admin, "outsider", "赵路人", "Outsider-pass-123")

    blocked = client.get(f"/groups/{group['id']}/messages", headers=outsider)
    assert blocked.status_code == 403
    assert client.get("/groups", headers=outsider).json() == []


def test_manager_invites_member_then_member_can_post(client: TestClient):
    admin = _admin_headers(client)
    group = _create_group(client, admin)
    member = _make_user(client, admin, "member", "王组员", "Member-pass-123")

    directory = client.get("/groups/directory-users", headers=admin, params={"q": "王"})
    assert directory.status_code == 200
    member_id = directory.json()[0]["id"]

    added = client.post(
        f"/groups/{group['id']}/members",
        headers=admin,
        json={"user_id": member_id, "role": "member"},
    )
    assert added.status_code == 201, added.text
    assert added.json()["member_count"] == 2

    posted = client.post(
        f"/groups/{group['id']}/messages",
        headers=member,
        json={"kind": "text", "content": "图纸已上传，请确认公差"},
    )
    assert posted.status_code == 201, posted.text
    assert posted.json()["mine"] is True

    listed = client.get(f"/groups/{group['id']}/messages", headers=admin).json()
    assert [item["content"] for item in listed["messages"]] == ["图纸已上传，请确认公差"]
    assert listed["messages"][0]["sender_name"] == "王组员"
    assert listed["messages"][0]["mine"] is False


def test_upload_text_attachment_then_reference_it_in_message(client: TestClient):
    admin = _admin_headers(client)
    group = _create_group(client, admin)

    uploaded = client.post(
        f"/groups/{group['id']}/attachments",
        headers=admin,
        files={"file": ("工艺卡.txt", "工序10 下料\n工序20 折弯\n".encode("utf-8"), "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment = uploaded.json()
    assert attachment["extract_status"] == "done"
    assert attachment["kind"] == "file"

    message = client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={
            "kind": "file",
            "content": "按这份工艺卡排产",
            "attachment_ids": [attachment["id"]],
        },
    )
    assert message.status_code == 201, message.text
    assert message.json()["attachments"][0]["filename"] == "工艺卡.txt"

    download = client.get(attachment["url"], headers=admin)
    assert download.status_code == 200
    assert "工序20 折弯" in download.text

    reused = client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={"kind": "text", "content": "再发一次", "attachment_ids": [attachment["id"]]},
    )
    assert reused.status_code == 409


def test_analyze_without_model_returns_503_and_records_nothing(client: TestClient):
    admin = _admin_headers(client)
    group = _create_group(client, admin)
    client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={"kind": "text", "content": "周三前确认机架图纸"},
    )

    response = client.post(f"/groups/{group['id']}:analyze", headers=admin)
    assert response.status_code == 503, response.text
    assert client.get("/todos", headers=admin).json() == []


def test_todo_status_is_owner_or_assignee_only(client: TestClient):
    admin = _admin_headers(client)
    group = _create_group(client, admin)
    other = _make_user(client, admin, "other", "孙外员", "Other-pass-123")
    member = _make_user(client, admin, "member2", "李组员", "Member2-pass-123")
    directory = client.get("/groups/directory-users", headers=admin, params={"q": "李"})
    member_id = directory.json()[0]["id"]
    client.post(
        f"/groups/{group['id']}/members",
        headers=admin,
        json={"user_id": member_id, "role": "member"},
    )

    from app.core.database import transaction
    from app.models.group_chat import GroupTodo
    from app.core.security import now

    with transaction() as db:
        stamp = now()
        db.add(
            GroupTodo(
                id="gtodo_api_1",
                group_id=group["id"],
                assignee_user_id=member_id,
                origin="ai",
                title="确认机架图纸公差",
                detail="",
                status="pending",
                priority="normal",
                created_at=stamp,
                updated_at=stamp,
            )
        )

    denied = client.patch(
        "/todos/gtodo_api_1", headers=other, json={"status": "done"}
    )
    assert denied.status_code == 403

    allowed = client.patch(
        "/todos/gtodo_api_1", headers=member, json={"status": "done"}
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["status"] == "done"

    listed = client.get("/todos", headers=member).json()
    assert [item["title"] for item in listed] == ["确认机架图纸公差"]


def _stub_model(monkeypatch, text: str) -> None:
    """顶掉真实模型调用，让 API 层能跑通完整分析链路。"""
    from types import SimpleNamespace

    async def fake_complete(db, messages, **kwargs):
        return SimpleNamespace(text=text, model="stub-model", endpoint_url="http://stub", masked=True)

    monkeypatch.setattr("app.services.plain_llm.complete_chat", fake_complete)


def test_analysis_creates_todos_and_posts_ai_message(client: TestClient, monkeypatch):
    admin = _admin_headers(client)
    group = _create_group(client, admin)
    client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={"kind": "text", "content": "周三前确认机架图纸公差，李工跟一下"},
    )
    _stub_model(
        monkeypatch,
        '{"summary":"确认图纸公差","todos":[{"title":"确认机架图纸公差",'
        '"detail":"重点看孔位","assignee":"管理员","priority":"high","due":"2026-11-20"}],'
        '"decisions":[],"risks":[]}',
    )

    response = client.post(f"/groups/{group['id']}:analyze", headers=admin)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "done"
    assert body["todos_created"] == 1

    todos = client.get("/todos", headers=admin).json()
    assert [item["title"] for item in todos] == ["确认机架图纸公差"]
    assert todos[0]["priority"] == "high"
    assert todos[0]["group_name"] == group["name"]

    messages = client.get(f"/groups/{group['id']}/messages", headers=admin).json()
    ai_messages = [item for item in messages["messages"] if item["kind"] == "ai"]
    assert len(ai_messages) == 1
    assert "@系统管理员 确认机架图纸公差" in ai_messages[0]["content"]
    assert ai_messages[0]["parts_json"]


def test_failed_analysis_is_persisted_as_run(client: TestClient, monkeypatch):
    """分析失败要通过 502 报出来，同时 run 记录必须留在库里（不能随事务回滚）。"""
    admin = _admin_headers(client)
    group = _create_group(client, admin)
    client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={"kind": "text", "content": "周三前确认机架图纸"},
    )
    _stub_model(monkeypatch, "我觉得可以，你看着办")

    response = client.post(f"/groups/{group['id']}:analyze", headers=admin)
    assert response.status_code == 502, response.text
    assert "JSON" in response.json()["detail"]

    runs = client.get(f"/groups/{group['id']}/runs", headers=admin).json()
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert client.get("/todos", headers=admin).json() == []


def test_only_owner_can_delete_group_and_children_go_with_it(client: TestClient):
    admin = _admin_headers(client)
    group = _create_group(client, admin)
    member = _make_user(client, admin, "member3", "周组员", "Member3-pass-123")
    directory = client.get("/groups/directory-users", headers=admin, params={"q": "周"})
    client.post(
        f"/groups/{group['id']}/members",
        headers=admin,
        json={"user_id": directory.json()[0]["id"], "role": "member"},
    )
    client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={"kind": "text", "content": "随便说说"},
    )

    denied = client.delete(f"/groups/{group['id']}", headers=member)
    assert denied.status_code == 403

    deleted = client.delete(f"/groups/{group['id']}", headers=admin)
    assert deleted.status_code == 204, deleted.text
    assert client.get(f"/groups/{group['id']}", headers=admin).status_code == 404
    assert client.get("/groups", headers=admin).json() == []


def _upload_voice(client, headers, group_id, filename="语音-1.webm"):
    return client.post(
        f"/groups/{group_id}/attachments",
        headers=headers,
        files={"file": (filename, b"RIFF....WEBMfake", "audio/webm")},
    )


def test_voice_upload_is_transcribed_and_used_as_message_text(client: TestClient, monkeypatch):
    """语音由服务端自己转写，AI 读的是文字，用户不用手写内容。"""
    from app.services import asr

    async def fake_transcribe(audio_bytes, media_type):
        assert media_type.startswith("audio/webm")
        return "周三前把机架图纸公差确认完"

    monkeypatch.setattr(asr, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr("app.api.group_chat.asr.transcribe_audio", fake_transcribe)

    admin = _admin_headers(client)
    group = _create_group(client, admin)

    uploaded = _upload_voice(client, admin, group["id"])
    assert uploaded.status_code == 201, uploaded.text
    attachment = uploaded.json()
    assert attachment["extract_status"] == "done"
    assert attachment["transcript"] == "周三前把机架图纸公差确认完"

    message = client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={"kind": "voice", "content": "", "attachment_ids": [attachment["id"]]},
    )
    assert message.status_code == 201, message.text
    body = message.json()
    assert body["content"] == "周三前把机架图纸公差确认完"
    assert body["attachments"][0]["kind"] == "voice"


def test_voice_upload_without_asr_key_is_pending_not_broken(client: TestClient, monkeypatch):
    from app.services import asr

    async def not_configured(audio_bytes, media_type):
        raise asr.ASRNotConfiguredError("DASHSCOPE_API_KEY 未配置")

    monkeypatch.setattr("app.api.group_chat.asr.transcribe_audio", not_configured)

    admin = _admin_headers(client)
    group = _create_group(client, admin)
    uploaded = _upload_voice(client, admin, group["id"])

    assert uploaded.status_code == 201, uploaded.text
    attachment = uploaded.json()
    assert attachment["kind"] == "voice"
    assert attachment["extract_status"] == "pending"
    assert attachment["transcript"] is None


def test_voice_transcription_can_be_retried_after_config(client: TestClient, monkeypatch):
    from app.services import asr

    async def not_configured(audio_bytes, media_type):
        raise asr.ASRNotConfiguredError("DASHSCOPE_API_KEY 未配置")

    monkeypatch.setattr("app.api.group_chat.asr.transcribe_audio", not_configured)
    admin = _admin_headers(client)
    group = _create_group(client, admin)
    attachment = _upload_voice(client, admin, group["id"]).json()

    async def ok(audio_bytes, media_type):
        return "补齐来料检验记录"

    monkeypatch.setattr("app.api.group_chat.asr.transcribe_audio", ok)
    retried = client.post(
        f"/groups/{group['id']}/attachments/{attachment['id']}:transcribe", headers=admin
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["extract_status"] == "done"
    assert retried.json()["transcript"] == "补齐来料检验记录"


def test_oversize_voice_is_rejected_without_calling_asr(client: TestClient, monkeypatch):
    from app.core.config import settings

    called = {"n": 0}

    async def should_not_run(audio_bytes, media_type):
        called["n"] += 1
        return "不该被调用"

    monkeypatch.setattr("app.api.group_chat.asr.transcribe_audio", should_not_run)
    monkeypatch.setattr(settings, "asr_max_upload_bytes", 4)  # 测试样本 16 字节，4 即超限

    admin = _admin_headers(client)
    group = _create_group(client, admin)
    uploaded = _upload_voice(client, admin, group["id"])

    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["extract_status"] == "failed"
    assert called["n"] == 0


def test_question_message_triggers_assistant_reply(client: TestClient, monkeypatch):
    """发一句「进度怎么样」，AI 应该自己回一段，而不是等人点按钮。"""
    admin = _admin_headers(client)
    group = _create_group(client, admin)
    client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={"kind": "text", "content": "图纸周三前要确认"},
    )
    _stub_model(
        monkeypatch,
        '{"reply":"目前 1 项待办未完成：确认机架图纸公差，还没人认领。",'
        '"summary":"进度询问","todos":[],"decisions":[],"risks":[]}',
    )

    posted = client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={"kind": "text", "content": "不知道项目进度怎么样"},
    )
    assert posted.status_code == 201, posted.text

    messages = client.get(f"/groups/{group['id']}/messages", headers=admin).json()
    ai = [item for item in messages["messages"] if item["kind"] == "ai"]
    assert len(ai) == 1
    assert "1 项待办未完成" in ai[0]["content"]


def test_plain_chatter_does_not_wake_the_assistant(client: TestClient, monkeypatch):
    admin = _admin_headers(client)
    group = _create_group(client, admin)
    _stub_model(
        monkeypatch,
        '{"reply":"不该出现在这里","summary":"","todos":[],"decisions":[],"risks":[]}',
    )

    client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={"kind": "text", "content": "好的，收到"},
    )

    messages = client.get(f"/groups/{group['id']}/messages", headers=admin).json()
    assert [item for item in messages["messages"] if item["kind"] == "ai"] == []


def test_question_text_reaches_the_model_for_answering(client: TestClient, monkeypatch):
    """背景里没有「回答哪一句」，模型只会复读；这里锁住这个行为。"""
    from types import SimpleNamespace

    captured: dict = {}

    async def fake_complete(db, messages, **kwargs):
        captured["messages"] = messages
        return SimpleNamespace(
            text='{"reply":"还没人负责，3 项待办都未指派。","summary":"","todos":[],"decisions":[],"risks":[]}',
            model="stub",
            endpoint_url="http://stub",
            masked=True,
        )

    monkeypatch.setattr("app.services.plain_llm.complete_chat", fake_complete)

    admin = _admin_headers(client)
    group = _create_group(client, admin)
    client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={"kind": "text", "content": "你们谁负责这个项目的？"},
    )

    user_turn = captured["messages"][-1]["content"]
    assert "你们谁负责这个项目的？" in user_turn
    assert "需要你现在回答" in user_turn

    messages = client.get(f"/groups/{group['id']}/messages", headers=admin).json()
    ai = [item for item in messages["messages"] if item["kind"] == "ai"]
    assert len(ai) == 1
    assert "还没人负责" in ai[0]["content"]


def test_group_detail_lists_ai_assistant_as_member(client: TestClient):
    admin = _admin_headers(client)
    group = _create_group(client, admin)

    detail = client.get(f"/groups/{group['id']}", headers=admin).json()
    roles = {item["role"] for item in detail["members"]}
    names = [item["display_name"] for item in detail["members"]]

    assert "assistant" in roles
    assert "AI 助手" in names
    # 人类成员计数不受影响
    assert detail["member_count"] == 1


def test_message_time_carries_utc_offset(client: TestClient):
    """时间必须带时区，否则前端按本地解析会差 8 小时。"""
    admin = _admin_headers(client)
    group = _create_group(client, admin)
    posted = client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={"kind": "text", "content": "看时间"},
    ).json()

    created = posted["created_at"]
    assert created.endswith("Z") or "+00:00" in created, created


def _second_member(client, admin, group_id, username="member9", display="周组员"):
    created = client.post(
        "/admin/users",
        headers=admin,
        json={"username": username, "display_name": display, "password": "Member9-pass-123"},
    )
    assert created.status_code == 201, created.text
    directory = client.get("/groups/directory-users", headers=admin, params={"q": display[0]})
    uid = directory.json()[0]["id"]
    client.post(
        f"/groups/{group_id}/members",
        headers=admin,
        json={"user_id": uid, "role": "member"},
    )
    login = client.post("/auth/login", json={"username": username, "password": "Member9-pass-123"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}, uid


def test_mark_read_moves_unread_divider(client: TestClient):
    admin = _admin_headers(client)
    group = _create_group(client, admin)
    member_headers, _uid = _second_member(client, admin, group["id"])

    first = client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={"kind": "text", "content": "第一条"},
    ).json()
    second = client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={"kind": "text", "content": "第二条"},
    ).json()

    before = client.get(f"/groups/{group['id']}/messages", headers=member_headers).json()
    assert before["unread_from_id"] == first["id"]

    marked = client.post(
        f"/groups/{group['id']}:read", headers=member_headers, json={"message_id": first["id"]}
    )
    assert marked.status_code == 204, marked.text

    after = client.get(f"/groups/{group['id']}/messages", headers=member_headers).json()
    assert after["unread_from_id"] == second["id"]


def test_read_count_tracks_other_members(client: TestClient):
    admin = _admin_headers(client)
    group = _create_group(client, admin)
    member_headers, _uid = _second_member(client, admin, group["id"], "member8", "吴组员")

    mine = client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={"kind": "text", "content": "请确认"},
    ).json()
    listed = client.get(f"/groups/{group['id']}/messages", headers=admin).json()
    assert listed["messages"][0]["read_count"] == 0
    assert listed["messages"][0]["read_total"] == 1

    client.post(
        f"/groups/{group['id']}:read", headers=member_headers, json={"message_id": mine["id"]}
    )
    listed = client.get(f"/groups/{group['id']}/messages", headers=admin).json()
    assert listed["messages"][0]["read_count"] == 1


def test_recall_within_window_and_permission(client: TestClient):
    admin = _admin_headers(client)
    group = _create_group(client, admin)
    member_headers, _uid = _second_member(client, admin, group["id"], "member7", "郑组员")

    sent = client.post(
        f"/groups/{group['id']}/messages",
        headers=member_headers,
        json={"kind": "text", "content": "说错话了"},
    ).json()

    denied = client.delete(f"/groups/{group['id']}/messages/{sent['id']}", headers=admin)
    assert denied.status_code == 204  # 组长可以撤回任何人的消息

    left = client.get(f"/groups/{group['id']}/messages", headers=admin).json()["messages"]
    assert all(item["content"] != "说错话了" for item in left)
    assert any("撤回了一条消息" in item["content"] for item in left)


def test_quote_target_is_embedded_in_response(client: TestClient):
    admin = _admin_headers(client)
    group = _create_group(client, admin)
    original = client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={"kind": "text", "content": "周五前交货"},
    ).json()
    reply = client.post(
        f"/groups/{group['id']}/messages",
        headers=admin,
        json={"kind": "text", "content": "收到", "reply_to_message_id": original["id"]},
    ).json()

    assert reply["quoted"]["id"] == original["id"]
    assert reply["quoted"]["content"] == "周五前交货"
