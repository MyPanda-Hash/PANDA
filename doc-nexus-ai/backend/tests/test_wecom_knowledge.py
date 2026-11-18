from __future__ import annotations

from unittest.mock import Mock, patch

from app.core.config import settings
from app.services.wecom import (
    WeComConfig,
    WeComMessage,
    clear_incoming_message_cache,
    process_incoming_message,
)
from app.services.wecom_knowledge import answer_wecom_knowledge, is_knowledge_question


class _Transaction:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, exc, tb):
        return False


class _Db:
    def __init__(self, scalar_value=0, space_ids=None, space_names=None, rows=None):
        self.scalar_value = scalar_value
        self.space_ids = space_ids or []
        self.space_names = space_names
        self.rows = rows or []
        self.query = None

    def scalar(self, query):
        self.query = query
        return self.scalar_value

    def scalars(self, query):
        self.query = query
        return iter(self.space_names if self.space_names is not None else self.space_ids)

    def execute(self, query):
        self.query = query
        return self

    def all(self):
        return self.rows


def test_knowledge_intent_gate_keeps_general_chat_on_agent_path():
    assert is_knowledge_question("公司产品是什么")
    assert is_knowledge_question("知识文档有多少篇")
    assert not is_knowledge_question("你好，翠花")
    assert not is_knowledge_question("你会做啥呢？")


def test_count_question_uses_only_configured_queryable_scope(monkeypatch):
    monkeypatch.setattr(settings, "wecom_knowledge_space_ids", "spc-a, spc-b")
    db = _Db(scalar_value=7)

    with patch(
        "app.services.wecom_knowledge.transaction",
        return_value=_Transaction(db),
    ):
        reply = answer_wecom_knowledge("知识文档有多少篇？")

    assert reply == "当前知识范围内共有 7 篇已发布且可问答的知识文档。"
    assert db.query is not None


def test_empty_scope_configuration_exposes_all_non_deleted_spaces(monkeypatch):
    monkeypatch.setattr(settings, "wecom_knowledge_space_ids", "")
    db = _Db(space_ids=["spc-a", "spc-b"])
    result = ("全资料空间均可检索。", "completed", {}, {}, {}, [], [])

    with (
        patch("app.services.wecom_knowledge.transaction", return_value=_Transaction(db)),
        patch("app.services.wecom_knowledge.generate_answer", return_value=result) as generate,
    ):
        reply = answer_wecom_knowledge("公司产品是什么？")

    assert reply == "全资料空间均可检索。"
    generate.assert_called_once_with(db, "公司产品是什么？", ["spc-a", "spc-b"])


def test_rag_answer_contains_bounded_document_sources(monkeypatch):
    monkeypatch.setattr(settings, "wecom_knowledge_space_ids", "spc-a")
    db = _Db()
    evidence = [
        {"id": "cit-1", "page_or_section": "文档《产品说明》v1"},
        {"id": "cit-2", "page_or_section": "文档《API》v2"},
        {"id": "cit-3", "page_or_section": "文档《流程》v1"},
        {"id": "cit-4", "page_or_section": "文档《不应展示》v1"},
    ]
    result = ("产品支持知识问答。", "completed", {}, {}, {}, [], evidence)

    with (
        patch("app.services.wecom_knowledge.transaction", return_value=_Transaction(db)),
        patch("app.services.wecom_knowledge.generate_answer", return_value=result) as generate,
    ):
        reply = answer_wecom_knowledge("公司产品是什么？")

    assert reply == (
        "产品支持知识问答。\n\n参考文档：\n"
        "- [cit-1] 文档《产品说明》v1\n"
        "- [cit-2] 文档《API》v2\n"
        "- [cit-3] 文档《流程》v1"
    )
    generate.assert_called_once_with(db, "公司产品是什么？", ["spc-a"])


def test_structured_document_list_uses_queryable_documents_only(monkeypatch):
    monkeypatch.setattr(settings, "wecom_knowledge_space_ids", "spc-a")
    db = _Db(
        rows=[
            ("产品空间", "doc-1", "产品说明"),
            ("产品空间", "doc-2", "API 文档"),
        ]
    )

    with patch(
        "app.services.wecom_knowledge.transaction",
        return_value=_Transaction(db),
    ):
        reply = answer_wecom_knowledge("有哪些知识文档？")

    assert reply == (
        "当前知识范围内共有 2 篇已发布且可问答的知识文档：\n"
        "产品说明、API 文档"
    )


def test_processes_grounded_reply_without_calling_general_agent():
    config = WeComConfig(
        corp_id="ww-test-corp",
        agent_id=1000002,
        secret="wecom-secret",
        token="callback-token",
        encoding_aes_key="a" * 43,
    )
    message = WeComMessage(
        to_user_name=config.corp_id,
        from_user_name="user-1",
        create_time="1720000000",
        msg_type="text",
        content="公司产品是什么？",
        msg_id="msg-knowledge",
    )
    send = Mock()

    with (
        patch("app.services.wecom.answer_wecom_knowledge", return_value="有依据的回答"),
        patch("app.services.wecom._ask_cuihua") as agent,
        patch("app.services.wecom._send_text", send),
    ):
        process_incoming_message(message, config=config)

    agent.assert_not_called()
    send.assert_called_once_with(config, to_user="user-1", content="有依据的回答")


def test_duplicate_self_built_callback_is_processed_once():
    config = WeComConfig(
        corp_id="ww-test-corp",
        agent_id=1000002,
        secret="wecom-secret",
        token="callback-token",
        encoding_aes_key="a" * 43,
    )
    message = WeComMessage(
        to_user_name=config.corp_id,
        from_user_name="user-1",
        create_time="1720000000",
        msg_type="text",
        content="请继续",
        msg_id="msg-duplicate",
    )
    clear_incoming_message_cache()
    with (
        patch("app.services.wecom.answer_wecom_knowledge", return_value=None),
        patch("app.services.wecom._ask_cuihua", return_value="回复") as agent,
        patch("app.services.wecom._send_text") as send,
    ):
        process_incoming_message(message, config=config)
        process_incoming_message(message, config=config)

    send.assert_called_once_with(config, to_user="user-1", content="回复")


def test_failed_delivery_can_retry_same_self_built_callback():
    config = WeComConfig(
        corp_id="ww-test-corp",
        agent_id=1000002,
        secret="wecom-secret",
        token="callback-token",
        encoding_aes_key="a" * 43,
    )
    message = WeComMessage(
        to_user_name=config.corp_id,
        from_user_name="user-1",
        create_time="1720000000",
        msg_type="text",
        content="请继续",
        msg_id="msg-retry",
    )
    clear_incoming_message_cache()
    with (
        patch("app.services.wecom.answer_wecom_knowledge", return_value=None),
        patch("app.services.wecom._ask_cuihua", return_value="回复") as agent,
        patch(
            "app.services.wecom._send_text",
            side_effect=[RuntimeError("temporary"), None],
        ) as send,
    ):
        process_incoming_message(message, config=config)
        process_incoming_message(message, config=config)

    assert agent.call_count == 2
    assert send.call_count == 2
