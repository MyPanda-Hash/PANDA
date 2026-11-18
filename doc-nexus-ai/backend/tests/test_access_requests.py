"""Contract tests for the access-request schemas and bounded JSON envelopes.

These guard the security contract at the layer where it is actually enforced:
a freely typed ``source`` object would silently swallow ``document_id``, and a
request model without ``extra="forbid"`` would turn "submit an id ⇒ 422" into
"submit an id ⇒ ignored".
"""

import json

import pytest
from pydantic import ValidationError

from app.schemas.access_requests import (
    AccessRequestResolveRequest,
    AccessRequestSubmitRequest,
)
from app.services.access_requests import (
    MAX_CITATION_REFS,
    dump_bounded_items,
    envelope_was_recorded,
    load_bounded_items,
)


def _reject(model, payload: dict) -> str:
    with pytest.raises(ValidationError) as captured:
        model.model_validate(payload)
    return captured.value.errors()[0]["type"]


def test_known_anchor_shapes_are_accepted():
    writing = AccessRequestResolveRequest.model_validate(
        {
            "source": {"kind": "writing", "project_id": "wpr_1"},
            "citation_id": "writing-evidence:ver_1:10-20",
        }
    )
    assert writing.source.kind == "writing"
    publication = AccessRequestSubmitRequest.model_validate(
        {
            "source": {"kind": "writing_publication", "publication_id": "wpp_1"},
            "reason": "用于撰写投标方案",
        }
    )
    assert publication.source.kind == "writing_publication"
    discovery = AccessRequestResolveRequest.model_validate(
        {"source": {"kind": "discovery", "discovery_id": "did_1"}}
    )
    assert discovery.citation_id is None


def test_nested_unexpected_id_is_rejected_by_the_branch_schema():
    """The outer model cannot catch this: the branch has to forbid extras."""
    assert _reject(
        AccessRequestResolveRequest,
        {"source": {"kind": "writing", "project_id": "wpr_1", "document_id": "doc_1"}},
    ) == "extra_forbidden"


def test_outer_unexpected_field_is_rejected():
    assert _reject(
        AccessRequestSubmitRequest,
        {"source": {"kind": "writing", "project_id": "wpr_1"}, "space_id": "spc_1"},
    ) == "extra_forbidden"


def test_unknown_anchor_kind_is_rejected():
    assert _reject(
        AccessRequestResolveRequest,
        {"source": {"kind": "space", "space_id": "spc_1"}},
    ) == "union_tag_invalid"


@pytest.mark.parametrize(
    "source",
    [
        {"kind": "chat_publication", "publication_id": "chp_1"},
        {"kind": "discovery", "discovery_id": "did_1"},
    ],
)
def test_citation_id_is_rejected_for_anchors_without_a_citation_set(source):
    assert _reject(
        AccessRequestResolveRequest, {"source": source, "citation_id": "[citation:0]"}
    ) == "value_error"


def test_reason_length_is_bounded():
    assert _reject(
        AccessRequestSubmitRequest,
        {
            "source": {"kind": "writing", "project_id": "wpr_1"},
            "reason": "x" * 501,
        },
    ) == "string_too_long"


def test_dump_bounded_items_deduplicates_and_flags_truncation():
    assert json.loads(dump_bounded_items(["b", "a", "b"], 3)) == {
        "items": ["b", "a"],
        "truncated": False,
    }
    assert json.loads(dump_bounded_items(["a", "b", "c"], 2)) == {
        "items": ["a", "b"],
        "truncated": True,
    }
    assert len(json.loads(dump_bounded_items(range(200), MAX_CITATION_REFS))["items"]) == (
        MAX_CITATION_REFS
    )


def test_dump_bounded_items_deduplicates_by_key_for_object_entries():
    entries = [
        {"space_id": "spc_1", "document_id": "doc_1"},
        {"space_id": "spc_1", "document_id": "doc_1"},
        {"space_id": "spc_1", "document_id": "doc_2"},
    ]
    dumped = json.loads(
        dump_bounded_items(entries, 10, key=lambda item: (item["space_id"], item["document_id"]))
    )
    assert dumped == {"items": [entries[0], entries[2]], "truncated": False}


def test_dump_bounded_items_deduplicates_structured_entries_without_a_key():
    """Locators/source refs are dicts: the default marker must stay stable."""
    entries = [
        {"space_id": "spc_1", "document_id": "doc_1"},
        {"space_id": "spc_1", "document_id": "doc_1"},
        {"space_id": "spc_1"},
    ]
    dumped = json.loads(dump_bounded_items(entries, 10))
    assert dumped == {"items": [entries[0], entries[2]], "truncated": False}
    assert load_bounded_items(json.dumps(dumped)) == [entries[0], entries[2]]


def test_load_bounded_items_tolerates_null_and_malformed_values():
    assert load_bounded_items(None) == []
    assert load_bounded_items("") == []
    assert load_bounded_items("not json") == []
    assert load_bounded_items('["a"]') == []
    assert load_bounded_items('{"items": "a"}') == []
    assert load_bounded_items('{"items": [], "truncated": false}') == []


def test_null_and_recorded_empty_stay_distinguishable():
    """NULL means "never recorded"; an empty envelope means "recorded, empty"."""
    assert envelope_was_recorded(None) is False
    assert envelope_was_recorded(dump_bounded_items([], MAX_CITATION_REFS)) is True


def test_access_requests_table_is_created_with_pending_uniqueness(tmp_path):
    """Guards model registration: without it ``create_all`` never makes the table.

    Also pins the partial unique index the idempotency rule depends on: one
    pending request per ``(target_type, target_id, requester)``.
    """
    from sqlalchemy import create_engine, inspect

    from app.core import database
    from app.core.config import settings

    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "access-requests-table.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        database.initialize_database()
        engine = create_engine(settings.database_url)
        try:
            inspector = inspect(engine)
            assert "access_requests" in inspector.get_table_names()
            columns = {
                column["name"] for column in inspector.get_columns("access_requests")
            }
            assert {
                "target_type",
                "target_id",
                "granted_role",
                "requester_user_id",
                "source_refs_json",
                "citation_refs_json",
                "open_locator_json",
                "status",
                "decision_note",
            } <= columns
            names = {index["name"] for index in inspector.get_indexes("access_requests")}
            assert "uq_access_requests_pending_target" in names
        finally:
            engine.dispose()
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)
