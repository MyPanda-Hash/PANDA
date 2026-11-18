"""Tests for chat auto-DOCX delivery (翠花写完稿自动生成可下载 docx)."""

from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app.core import database
from app.core.config import settings
from app.main import app
from app.api.chat import _append_auto_docx_delivery
from app.models.artifacts import Artifact
from app.models.writing import WritingProject
from app.services.chat_docx_delivery import (
    deliver_chat_reply_docx,
    should_auto_export_docx,
)
from tests._auth import bootstrap_admin_response
from tests.test_usage import _isolated_client, _restore_database


def _admin_headers(client: TestClient) -> dict[str, str]:
    response = bootstrap_admin_response(client)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ── 判定启发式 ──────────────────────────────────────────────

LONG_DOC = (
    "# 一、项目概述\n\n"
    "本项目面向企业客户提供一体化解决方案，覆盖咨询、实施与验收全流程。方案以客户现有业务为基线，通过流程梳理识别断点，围绕数据、系统与组织三个层面制定改进路径，确保建设成果可落地、可度量、可验收。\n\n"
    "## 1.1 建设目标\n\n"
    "通过平台化能力整合，将交付周期缩短 30%，并建立可复用的交付方法论。目标包括：业务指标可量化、系统接口标准化、运营流程可视化，以及知识资产在组织内可持续沉淀与复用。\n\n"
    "## 1.2 建设范围\n\n"
    "包括需求调研、方案设计、系统部署、数据迁移与培训推广五部分。其中需求调研覆盖业务、系统与数据现状；方案设计输出总体架构与集成规范；系统部署包含环境准备与联调测试；数据迁移遵循映射确认、清洗转换、试迁校验与正式切换；培训面向关键用户分层开展。\n\n"
    "## 1.3 组织保障\n\n"
    "成立专项项目组，由项目经理统筹，业务与技术双线并行推进。项目组下设业务组、技术组与质量组，分别负责需求确认、方案落地与独立测试评审，确保交付物在进入下一阶段前满足既定标准。\n\n"
    "# 二、实施计划\n\n"
    "第一阶段完成需求确认与蓝图设计，输出详细设计文档并通过评审；第二阶段开展系统实施与集成测试，完成功能开发、接口联调与性能验证；第三阶段完成试运行与正式上线，试运行期间安排驻场支持并闭环用户反馈。\n\n"
    "# 三、风险与应对\n\n"
    "针对进度风险设置里程碑检查点，偏差超阈值即启动纠偏；针对质量风险引入独立测试与评审放行机制；针对人员变动建立知识交接与双人复核制度；针对需求变更采用变更控制流程，评估影响后由双方确认再纳入计划。\n\n"
    "# 四、验收标准\n\n"
    "验收以合同与需求说明书为依据，逐项核对功能实现、性能指标与交付文档。功能用例通过率不低于 95%，性能满足并发与响应时间要求，文档覆盖设计、测试、操作与运维四类资料，全部通过后出具验收报告。\n"
)


def test_should_auto_export_docx_accepts_long_structured_reply():
    assert should_auto_export_docx(
        LONG_DOC,
        has_existing_project=False,
        has_generated_files=False,
        user_request="请写一份慧云科技项目方案",
    )


def test_should_auto_export_docx_rejects_short_reply():
    assert not should_auto_export_docx(
        "你好，有什么可以帮你？",
        has_existing_project=False,
        has_generated_files=False,
        user_request="请写一份慧云科技项目方案",
    )


def test_should_auto_export_docx_rejects_existing_project_or_files():
    assert not should_auto_export_docx(
        LONG_DOC,
        has_existing_project=True,
        has_generated_files=False,
        user_request="请写一份慧云科技项目方案",
    )
    assert not should_auto_export_docx(
        LONG_DOC,
        has_existing_project=False,
        has_generated_files=True,
        user_request="请写一份慧云科技项目方案",
    )


def test_should_auto_export_docx_rejects_code_heavy_reply():
    code = (
        "# 说明\n\n```python\n"
        + "\n".join(f"def fn_{i}():\n    return {i}" for i in range(60))
        + "\n```\n"
    )
    assert not should_auto_export_docx(
        code,
        has_existing_project=False,
        has_generated_files=False,
        user_request="请写一份慧云科技项目方案",
    )


def test_should_auto_export_docx_rejects_reply_with_download_link():
    with_link = LONG_DOC + "\n\n文件已生成\n- [方案.docx](https://example.com/a.docx)\n"
    assert not should_auto_export_docx(
        with_link,
        has_existing_project=False,
        has_generated_files=False,
        user_request="请写一份慧云科技项目方案",
    )


def test_append_auto_docx_delivery_formats_inline_card():
    appended = _append_auto_docx_delivery(
        "# 正文标题\n\n一段正文内容", "art_abc", "慧云科技介绍.docx"
    )
    assert appended.endswith(
        "\n\n文件已生成\n- [慧云科技介绍.docx](/api/files/artifacts/art_abc/慧云科技介绍.docx)"
    )


def test_append_auto_docx_delivery_is_idempotent():
    text = "# 正文\n\n文件已生成\n- [a.docx](/api/files/artifacts/art_1/a.docx)"
    again = _append_auto_docx_delivery(text, "art_1", "a.docx")
    assert again == text


# ── 交付集成 ────────────────────────────────────────────────


def _user_record(client: TestClient) -> object:
    """Bootstrap admin and return the user object from its login response."""
    response = bootstrap_admin_response(client)
    assert response.status_code == 200
    return response.json()["user"]


def test_deliver_chat_reply_docx_creates_project_and_downloadable_artifact(
    tmp_path: Path,
):
    original_path, original_url, database_path = _isolated_client(
        tmp_path, "chat-docx-delivery.sqlite3"
    )
    try:
        with TestClient(app) as client:
            admin_headers = _admin_headers(client)
            user = _user_record(client)

            from app.models.auth import User

            with database.transaction() as db:
                db_user = db.get(User, user["id"])
                assert db_user is not None
                result = deliver_chat_reply_docx(
                    db=db,
                    user=db_user,
                    reply=LONG_DOC,
                    title_hint="慧云科技项目方案",
                    local_session_id="chat-session-1",
                    assistant_message_id="assistant-message-1",
                )
            assert result is not None
            assert result["project_id"].startswith("wpr_")
            assert result["artifact_id"].startswith("art_")
            assert result["name"].endswith(".docx")

            # 项目可见且标题正确
            projects = client.get("/writing/projects", headers=admin_headers).json()[
                "items"
            ]
            assert any(item["id"] == result["project_id"] for item in projects)

            # artifact 可通过既有 /artifacts 下载接口取到（owner 鉴权）
            artifact = client.get(
                f"/artifacts/{result['artifact_id']}", headers=admin_headers
            )
            assert artifact.status_code == 200
            download = client.post(
                f"/artifacts/{result['artifact_id']}/download", headers=admin_headers
            )
            assert download.status_code == 200
            assert download.headers["content-type"].startswith(
                "application/vnd.openxmlformats-officedocument.wordprocessingml"
            )
            with ZipFile(io.BytesIO(download.content)) as document:
                assert "word/document.xml" in document.namelist()
                body = document.read("word/document.xml").decode("utf-8")
                assert "本项目面向企业客户提供一体化解决方案" in body
                assert "验收以合同与需求说明书为依据" in body

            # 通过 /files/artifacts/{id}/{name} 下载（聊天卡片同款直链）
            file_download = client.get(
                f"/files/artifacts/{result['artifact_id']}/{result['name']}",
                headers=admin_headers,
            )
            assert file_download.status_code == 200
            assert file_download.content == download.content
    finally:
        _restore_database(original_path, original_url, database_path)


def _prepare_auto_docx_session(tmp_path: Path, name: str):
    original_path, original_url, database_path = _isolated_client(tmp_path, name)
    with TestClient(app) as client:
        auth = _admin_headers(client)
        created = client.post(
            "/chat/sessions", headers=auth, json={"title": "自动成稿"}
        )
        assert created.status_code == 201, created.text
        return original_path, original_url, database_path, auth, created.json()["id"]


def test_auto_docx_and_chat_persistence_share_one_transaction(
    tmp_path: Path, monkeypatch
):
    original_path, original_url, database_path, auth, session_id = (
        _prepare_auto_docx_session(tmp_path, "chat-docx-rollback.sqlite3")
    )
    try:
        from app.api import chat as chat_api

        with database.SessionLocal() as db:
            owner_id = db.scalar(select(WritingProject.owner_user_id).limit(1))
        assert owner_id is None
        # Resolve the owner from the authenticated session without making the
        # test depend on a generated user id.
        from app.models.chat import ChatSession

        with database.SessionLocal() as db:
            owner_id = db.get(ChatSession, session_id).owner_user_id

        monkeypatch.setattr(
            chat_api.chat_repo,
            "persist_chat_turn",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("chat save failed")
            ),
        )
        with pytest.raises(RuntimeError, match="chat save failed"):
            chat_api._persist_server_chat_messages(
                user_id=owner_id,
                local_session_id=session_id,
                payloads=[
                    {"id": "auto-user-rollback", "role": "user", "content": "写方案"},
                    {
                        "id": "auto-assistant-rollback",
                        "role": "assistant",
                        "content": LONG_DOC,
                    },
                ],
                auto_docx_reply=LONG_DOC,
                auto_docx_state={},
            )

        with database.SessionLocal() as db:
            assert db.scalars(select(WritingProject)).all() == []
            assert db.scalars(select(Artifact)).all() == []
    finally:
        _restore_database(original_path, original_url, database_path)


def test_auto_docx_retry_uses_stable_message_identity(tmp_path: Path):
    original_path, original_url, database_path, _auth, session_id = (
        _prepare_auto_docx_session(tmp_path, "chat-docx-idempotent.sqlite3")
    )
    try:
        from app.api import chat as chat_api
        from app.models.chat import ChatSession

        with database.SessionLocal() as db:
            owner_id = db.get(ChatSession, session_id).owner_user_id

        payloads = [
            {"id": "auto-user-idempotent", "role": "user", "content": "写方案"},
            {
                "id": "auto-assistant-idempotent",
                "role": "assistant",
                "content": LONG_DOC,
            },
        ]
        first_state: dict[str, object] = {}
        chat_api._persist_server_chat_messages(
            user_id=owner_id,
            local_session_id=session_id,
            payloads=payloads,
            auto_docx_reply=LONG_DOC,
            auto_docx_state=first_state,
        )
        second_state: dict[str, object] = {}
        chat_api._persist_server_chat_messages(
            user_id=owner_id,
            local_session_id=session_id,
            payloads=payloads,
            auto_docx_reply=LONG_DOC,
            auto_docx_state=second_state,
        )

        with database.SessionLocal() as db:
            assert len(db.scalars(select(WritingProject)).all()) == 1
            assert len(db.scalars(select(Artifact)).all()) == 1
            assert first_state["artifact_id"] == second_state["artifact_id"]
    finally:
        _restore_database(original_path, original_url, database_path)


def test_revised_chat_reply_does_not_inherit_existing_auto_docx_project(
    tmp_path: Path, monkeypatch
):
    from app.api import chat as chat_api
    from app.models.chat import ChatSession
    from app.services.pi_runtime.runner import PiRunResult

    original_path, original_url, database_path, auth, session_id = (
        _prepare_auto_docx_session(tmp_path, "chat-docx-revision.sqlite3")
    )
    try:
        with database.SessionLocal() as db:
            owner_id = db.get(ChatSession, session_id).owner_user_id

        first_state: dict[str, object] = {}
        chat_api._persist_server_chat_messages(
            user_id=owner_id,
            local_session_id=session_id,
            payloads=[
                {
                    "id": "auto-user-v1",
                    "role": "user",
                    "content": "请写一份项目方案",
                },
                {
                    "id": "auto-assistant-v1",
                    "role": "assistant",
                    "content": LONG_DOC,
                },
            ],
            auto_docx_reply=LONG_DOC,
            auto_docx_state=first_state,
        )
        project_id = str(first_state["project_id"])
        revised_reply = LONG_DOC.replace("缩短 30%", "缩短 45%")
        runtime_calls = 0

        class FakePiClient:
            effective_model = "chat-model"

            def __init__(self, **_kwargs):
                pass

            async def chat_async(self, message, **_kwargs):
                nonlocal runtime_calls
                runtime_calls += 1
                assert message.startswith(
                    "请重新输出完整项目方案，交付周期目标为 45%"
                )
                return (
                    PiRunResult(
                        reply=revised_reply,
                        stats={},
                        state={},
                        events=[],
                        token_usage={},
                    ),
                    object(),
                )

        monkeypatch.setattr(
            settings, "pi_nexus_capability_base_url", "http://nexus.test"
        )
        monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)

        request = {
            "message": "请重新输出完整项目方案，交付周期目标为 45%",
            "local_session_id": session_id,
            "client_message_id": "auto-user-v2",
            "client_assistant_message_id": "auto-assistant-v2",
        }
        with TestClient(app) as client:
            response = client.post("/chat/messages", headers=auth, json=request)
            assert response.status_code == 200, response.text
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            done = events[-1]
            assert done["type"] == "done"
            assert done["reply"] == revised_reply
            assert "writing_project_id" not in done

            detail = client.get(f"/chat/sessions/{session_id}", headers=auth)
            assert detail.status_code == 200, detail.text
            snapshot = detail.json()
            assert snapshot["writing_project_id"] == project_id
            revised_message = next(
                item
                for item in snapshot["messages"]
                if item["id"] == "auto-assistant-v2"
            )
            assert "缩短 45%" in revised_message["content"]
            assert revised_message["writing_project_id"] is None

            replay = client.post("/chat/messages", headers=auth, json=request)
            assert replay.status_code == 200, replay.text
            replay_events = [
                json.loads(line[6:])
                for line in replay.text.splitlines()
                if line.startswith("data: ")
            ]
            assert runtime_calls == 1
            assert replay_events[-1]["type"] == "done"
            assert "writing_project_id" not in replay_events[-1]
    finally:
        _restore_database(original_path, original_url, database_path)


def test_auto_docx_concurrent_same_message_identity_is_singleton(tmp_path: Path):
    original_path, original_url, database_path, _auth, session_id = (
        _prepare_auto_docx_session(tmp_path, "chat-docx-concurrent.sqlite3")
    )
    try:
        from app.api import chat as chat_api
        from app.models.chat import ChatSession

        with database.SessionLocal() as db:
            owner_id = db.get(ChatSession, session_id).owner_user_id

        payloads = [
            {"id": "auto-user-concurrent", "role": "user", "content": "写方案"},
            {
                "id": "auto-assistant-concurrent",
                "role": "assistant",
                "content": LONG_DOC,
            },
        ]

        def persist_once() -> dict[str, object]:
            state: dict[str, object] = {}
            chat_api._persist_server_chat_messages(
                user_id=owner_id,
                local_session_id=session_id,
                payloads=payloads,
                auto_docx_reply=LONG_DOC,
                auto_docx_state=state,
            )
            return state

        with ThreadPoolExecutor(max_workers=2) as executor:
            states = list(executor.map(lambda _index: persist_once(), range(2)))

        with database.SessionLocal() as db:
            assert len(db.scalars(select(WritingProject)).all()) == 1
            assert len(db.scalars(select(Artifact)).all()) == 1
        assert states[0]["artifact_id"] == states[1]["artifact_id"]
    finally:
        _restore_database(original_path, original_url, database_path)


@pytest.mark.parametrize(
    "user_request",
    [
        "你会做啥啊？",
        "请解释方案的设计步骤",
        "请写一份项目方案，不要生成 Word 文件",
    ],
)
def test_long_answers_do_not_imply_document_delivery(user_request: str):
    assert not should_auto_export_docx(
        LONG_DOC,
        user_request=user_request,
        has_existing_project=False,
        has_generated_files=False,
    )


@pytest.mark.parametrize(
    ("user_request", "reply"),
    [
        (
            "帮我写一份设备采购报价单",
            "# 设备采购报价单\n\n| 项目 | 数量 | 金额 |\n| --- | --- | --- |\n"
            "| 工作站 | 2 | 12000 元 |\n\n报价有效期：7 天。",
        ),
        (
            "介绍一下慧云科技公司",
            "# 慧云科技公司介绍\n\n慧云科技提供企业数字化服务。\n\n"
            "主要业务包括系统集成、知识管理及持续运维。",
        ),
        ("给我这段内容的下载链接", "项目将于下周一启动，请各负责人准备验收资料。"),
    ],
)
def test_short_deliverables_and_explicit_downloads_are_supported(
    user_request: str, reply: str
):
    assert should_auto_export_docx(
        reply,
        user_request=user_request,
        has_existing_project=False,
        has_generated_files=False,
    )


@pytest.mark.parametrize(
    ("user_request", "reply", "expected_title"),
    [
        ("你会做啥啊？", LONG_DOC, None),
        (
            "请写一份项目方案",
            "# 慧云科技项目方案\n\n" + LONG_DOC,
            "慧云科技项目方案",
        ),
        ("请帮我写一份慧云科技项目方案", LONG_DOC, "慧云科技项目方案"),
    ],
)
def test_auto_docx_uses_request_and_document_title(
    tmp_path: Path, user_request: str, reply: str, expected_title: str | None
):
    original_path, original_url, database_path, auth, session_id = (
        _prepare_auto_docx_session(tmp_path, "chat-docx-intent-title.sqlite3")
    )
    try:
        from app.api import chat as chat_api
        from app.models.chat import ChatSession

        with database.transaction() as db:
            session = db.get(ChatSession, session_id)
            session.title = "新对话"
            owner_id = session.owner_user_id
        state: dict[str, str] = {}
        chat_api._persist_server_chat_messages(
            user_id=owner_id,
            local_session_id=session_id,
            payloads=[
                {"id": "intent-user", "role": "user", "content": user_request},
                {"id": "intent-assistant", "role": "assistant", "content": reply},
            ],
            auto_docx_reply=reply,
            auto_docx_state=state,
        )
        with TestClient(app) as client:
            saved = client.get(f"/chat/sessions/{session_id}", headers=auth).json()
            if expected_title is None:
                assert saved["messages"][-1]["content"] == reply
                assert saved["messages"][-1]["writing_project_id"] is None
                with database.SessionLocal() as db:
                    assert db.scalars(select(WritingProject)).all() == []
                    assert db.scalars(select(Artifact)).all() == []
            else:
                assert state["name"] == f"{expected_title}.docx"
                assert f"[{expected_title}.docx]" in saved["messages"][-1]["content"]
                download = client.get(
                    f"/files/artifacts/{state['artifact_id']}/{state['name']}",
                    headers=auth,
                )
                assert download.status_code == 200
                with ZipFile(io.BytesIO(download.content)) as document:
                    from xml.etree import ElementTree

                    tree = ElementTree.fromstring(document.read("word/document.xml"))
                    first_text = next(
                        tree.iter(
                            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                        )
                    )
                    assert first_text.text == expected_title
    finally:
        _restore_database(original_path, original_url, database_path)


def test_temporary_chat_source_requires_explicit_document_and_origin_session(
    tmp_path: Path, monkeypatch,
):
    from datetime import timedelta
    from app.core.security import now
    from app.models.auth import User
    from app.models.documents import Document, DocumentVersion
    from app.services.evidence_export import validate_evidence_sources
    from app.services.evidence_snapshots import freeze_text, make_citation_id

    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    original_path, original_url, database_path = _isolated_client(
        tmp_path, "chat-docx-temporary-source.sqlite3",
    )
    try:
        with TestClient(app) as client:
            auth = _admin_headers(client)
            owner_id = client.get("/auth/me", headers=auth).json()["id"]
            session_id = client.post(
                "/chat/sessions", headers=auth, json={"title": "临时来源会话"},
            ).json()["id"]
            text = "附件中的真实来源。"
            digest = freeze_text("ver_temp_export", text)
            citation_id = make_citation_id("ver_temp_export", digest, 0, len(text))
            source = {
                "id": citation_id, "document_id": "doc_temp_export",
                "document_version_id": "ver_temp_export", "text_sha256": digest,
                "char_start": 0, "char_end": len(text), "excerpt": text,
                "document_name": "临时登记文件",
            }
            reply = f"来自附件的正文。^[{citation_id}]"
            with database.transaction() as db:
                stamp = now()
                user = db.get(User, owner_id)
                db.add(Document(
                    id=source["document_id"], name="临时登记文件", space_id=None,
                    status="active", is_temporary=True, chat_session_id=session_id,
                    expires_at=stamp + timedelta(hours=1), created_by_user_id=owner_id,
                    created_at=stamp, updated_at=stamp,
                ))
                db.add(DocumentVersion(
                    id=source["document_version_id"], document_id=source["document_id"],
                    version_number=1, processing_stage="ready", queryable=True,
                    is_current_published=True, created_at=stamp, updated_at=stamp,
                ))
                db.flush()
                for selected, origin in [
                    ([], session_id), ([source["document_id"]], None),
                    ([source["document_id"]], "another-session"),
                ]:
                    denied = validate_evidence_sources(
                        db, user=user, texts=[reply], evidence=[source], space_ids=[],
                        document_ids=selected, chat_session_id=origin,
                    )
                    assert denied.blockers == [{
                        "code": "source_no_longer_authorized", "citation_id": citation_id,
                    }]
                delivered = deliver_chat_reply_docx(
                    db=db, user=user, reply=reply, local_session_id=session_id,
                    title_hint="临时来源文稿", evidence=[source], space_ids=[],
                    document_ids=[source["document_id"]],
                )
            preflight_url = f"/writing/projects/{delivered['project_id']}/exports:preflight"
            valid = client.post(preflight_url, headers=auth).json()
            assert valid["can_export"] is True, valid
            with database.transaction() as db:
                project = db.get(WritingProject, delivered["project_id"])
                project.source_chat_session_id = None
            assert client.post(preflight_url, headers=auth).json()["can_export"] is False
            with database.transaction() as db:
                db.get(WritingProject, delivered["project_id"]).source_chat_session_id = session_id
                db.get(Document, source["document_id"]).expires_at = now() - timedelta(seconds=1)
            assert client.post(preflight_url, headers=auth).json()["can_export"] is False
    finally:
        _restore_database(original_path, original_url, database_path)
