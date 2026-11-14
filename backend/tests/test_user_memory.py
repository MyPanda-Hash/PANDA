"""High-risk contracts for durable personal memory.

These tests cover what the design document calls out as the failures that can
actually happen: one employee reaching another's memory, a retry saving twice,
a stale background result overwriting a fresh correction, a forgotten name
coming back, and a chat edit leaving a memory that no longer has a source.

Cheap forwarding and formatting are deliberately not tested here; the migration
case runs the real Alembic revision against an isolated SQLite file with
foreign keys enforced, because the whole point of the composite keys is that
the database - not application code - refuses a cross-owner link.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import database
from app.core.security import new_token
from app.core.config import settings
from app.models.auth import User
from app.models.chat import ChatMessage, ChatSession
from app.models.user_memory import (
    UserMemoryEntry,
    UserMemoryEvidence,
    UserMemoryJob,
    UserMemoryOperation,
    UserMemoryState,
    UserMemoryTurn,
)
from app.repositories import chat as chat_repo
from app.repositories import user_memory as repo
from app.services import user_memory as service

ROOT = Path(__file__).parents[2]

OWNER = "user-mei"
OTHER = "user-fang"


@pytest.fixture
def memory_db(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "user-memory.sqlite3"
    database.reset_database(path)
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.initialize_database()
    db = database.SessionLocal()
    try:
        _seed_users(db)
        yield db
    finally:
        db.close()
        database.reset_database(path)
        settings.db_path, settings.database_url = original


def _seed_users(db: Session) -> None:
    stamp = datetime.now(timezone.utc)
    # Same display name on purpose: addressing must never fall back to matching
    # employees by what they are called.
    for user_id, username in ((OWNER, "mei"), (OTHER, "fang")):
        db.add(
            User(
                id=user_id,
                username=username,
                email=f"{username}@example.com",
                display_name="同名同事",
                password_hash="unused",
                created_at=stamp,
            )
        )
    db.commit()


def _register(
    db: Session,
    *,
    owner: str = OWNER,
    text_value: str = "我叫李梅",
    request_key: str = "req-1",
    channel: str = "web",
    chat_message_id: str | None = None,
) -> UserMemoryTurn:
    turn = repo.register_turn(
        db,
        owner_user_id=owner,
        channel=channel,
        request_key=request_key,
        raw_text=text_value,
        chat_message_id=chat_message_id,
        chat_session_id=(
            db.get(ChatMessage, chat_message_id).session_id if chat_message_id else None
        ),
    )
    db.commit()
    return turn


def _seed_chat_message(db: Session, *, owner: str, content: str) -> str:
    stamp = datetime.now(timezone.utc)
    session_id = f"sess-{owner}"
    if db.get(ChatSession, session_id) is None:
        db.add(
            ChatSession(
                id=session_id,
                owner_user_id=owner,
                title="t",
                created_at=stamp,
                updated_at=stamp,
            )
        )
        db.flush()
    message_id = f"msg-{owner}-{len(content)}"
    db.add(
        ChatMessage(
            id=message_id,
            session_id=session_id,
            owner_user_id=owner,
            role="user",
            content=content,
            created_at=stamp,
        )
    )
    db.commit()
    return message_id


def _propose_and_confirm(
    db: Session, *, owner: str, key: str, value: str, turn_id: str, origin="explicit"
) -> UserMemoryEntry:
    entry = repo.propose_entry(
        db,
        owner_user_id=owner,
        key=key,
        value=value,
        origin=origin,
        turn_id=turn_id,
        quote=value,
        quote_start=0,
        quote_end=len(value),
    )
    db.commit()
    confirmed = repo.confirm_entry(
        db,
        owner_user_id=owner,
        memory_id=entry.id,
        expected_version=entry.version,
    )
    db.commit()
    return confirmed


# ---------------------------------------------------------------------------
# Owner isolation (A01, A03).
# ---------------------------------------------------------------------------


def test_two_employees_with_the_same_display_name_keep_separate_names(
    memory_db: Session,
) -> None:
    mei_turn = _register(memory_db, owner=OWNER, text_value="我叫李梅")
    fang_turn = _register(
        memory_db, owner=OTHER, text_value="我叫王芳", request_key="req-2"
    )
    _propose_and_confirm(
        memory_db,
        owner=OWNER,
        key="profile.self_reported_name",
        value="李梅",
        turn_id=mei_turn.id,
    )
    _propose_and_confirm(
        memory_db,
        owner=OTHER,
        key="profile.self_reported_name",
        value="王芳",
        turn_id=fang_turn.id,
    )

    mei_active = repo.get_active_entry(
        memory_db, owner_user_id=OWNER, key="profile.self_reported_name"
    )
    fang_active = repo.get_active_entry(
        memory_db, owner_user_id=OTHER, key="profile.self_reported_name"
    )
    assert json.loads(mei_active.value_json) == "李梅"
    assert json.loads(fang_active.value_json) == "王芳"

    # Guessing the other employee's memory id answers "not found", never 403,
    # so the response never confirms that the row exists.
    with pytest.raises(repo.MemoryNotFoundError):
        repo.get_owned_entry(memory_db, owner_user_id=OWNER, memory_id=fang_active.id)
    assert [
        item.id for item in repo.list_owned_entries(memory_db, owner_user_id=OWNER)
    ] == [mei_active.id]


def test_database_rejects_evidence_that_crosses_owners(memory_db: Session) -> None:
    mei_turn = _register(memory_db, owner=OWNER)
    fang_turn = _register(memory_db, owner=OTHER, request_key="req-2")
    mei_entry = repo.propose_entry(
        memory_db,
        owner_user_id=OWNER,
        key="profile.self_reported_name",
        value="李梅",
        origin="explicit",
        turn_id=mei_turn.id,
    )
    memory_db.commit()

    # Mei's fact against Fang's turn: the composite FK on (owner, turn) fails.
    memory_db.add(
        UserMemoryEvidence(
            id="forged-1",
            owner_user_id=OWNER,
            memory_id=mei_entry.id,
            turn_id=fang_turn.id,
            source_hash=fang_turn.source_hash,
            evidence_kind="user_statement",
            created_at=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        memory_db.commit()
    memory_db.rollback()

    # Fang's owner against Mei's fact: the composite FK on (owner, memory) fails.
    memory_db.add(
        UserMemoryEvidence(
            id="forged-2",
            owner_user_id=OTHER,
            memory_id=mei_entry.id,
            turn_id=fang_turn.id,
            source_hash=fang_turn.source_hash,
            evidence_kind="user_statement",
            created_at=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        memory_db.commit()
    memory_db.rollback()


def test_only_one_active_entry_per_key(memory_db: Session) -> None:
    stamp = datetime.now(timezone.utc)
    for index in (1, 2):
        memory_db.add(
            UserMemoryEntry(
                id=f"dup-{index}",
                owner_user_id=OWNER,
                key="profile.preferred_name",
                value_json=json.dumps("小梅"),
                status="active",
                origin="manual",
                version=1,
                created_at=stamp,
                updated_at=stamp,
            )
        )
    with pytest.raises(IntegrityError):
        memory_db.commit()
    memory_db.rollback()

    # The same key is free to hold many terminal rows; only "active" is capped.
    for index, status in enumerate(("superseded", "forgotten", "rejected")):
        memory_db.add(
            UserMemoryEntry(
                id=f"old-{index}",
                owner_user_id=OWNER,
                key="profile.preferred_name",
                value_json=None,
                status=status,
                origin="manual",
                version=1,
                created_at=stamp,
                updated_at=stamp,
            )
        )
    memory_db.commit()


# ---------------------------------------------------------------------------
# Source registration and idempotency (A07).
# ---------------------------------------------------------------------------


def test_retry_reuses_the_turn_but_a_reused_key_with_new_text_conflicts(
    memory_db: Session,
) -> None:
    first = _register(memory_db, text_value="我叫李梅", request_key="turn-a")
    again = repo.register_turn(
        memory_db,
        owner_user_id=OWNER,
        channel="web",
        request_key="turn-a",
        raw_text="我叫李梅",
    )
    assert again.id == first.id

    with pytest.raises(repo.MemoryConflictError) as excinfo:
        repo.register_turn(
            memory_db,
            owner_user_id=OWNER,
            channel="web",
            request_key="turn-a",
            raw_text="我叫王芳",
        )
    assert excinfo.value.code == "memory_request_conflict"

    # The same key under a different owner is a different turn entirely.
    other = repo.register_turn(
        memory_db,
        owner_user_id=OTHER,
        channel="web",
        request_key="turn-a",
        raw_text="我叫王芳",
    )
    memory_db.commit()
    assert other.id != first.id


def test_receipt_replays_instead_of_mutating_twice(memory_db: Session) -> None:
    turn = _register(memory_db)
    payload = repo.request_hash({"key": "profile.preferred_name", "value": "小梅"})
    operation, replay = repo.claim_operation(
        memory_db,
        owner_user_id=OWNER,
        kind="confirm",
        request_id="op-1",
        payload_hash=payload,
        turn_id=turn.id,
    )
    assert replay is False
    entry = _propose_and_confirm(
        memory_db,
        owner=OWNER,
        key="profile.preferred_name",
        value="小梅",
        turn_id=turn.id,
    )
    repo.complete_operation(
        memory_db,
        operation=operation,
        status="succeeded",
        result_memory_id=entry.id,
        result_revision=repo.get_or_create_state(memory_db, OWNER).revision,
    )
    memory_db.commit()

    replayed, is_replay = repo.claim_operation(
        memory_db,
        owner_user_id=OWNER,
        kind="confirm",
        request_id="op-1",
        payload_hash=payload,
    )
    assert is_replay is True
    assert replayed.id == operation.id
    assert replayed.result_memory_id == entry.id

    with pytest.raises(repo.MemoryConflictError) as excinfo:
        repo.claim_operation(
            memory_db,
            owner_user_id=OWNER,
            kind="confirm",
            request_id="op-1",
            payload_hash=repo.request_hash(
                {"key": "profile.preferred_name", "value": "李工"}
            ),
        )
    assert excinfo.value.code == "memory_request_conflict"


# ---------------------------------------------------------------------------
# Activation, conflict and correction (A02, A06).
# ---------------------------------------------------------------------------


def test_confirming_a_correction_supersedes_without_two_active_names(
    memory_db: Session,
) -> None:
    turn = _register(memory_db)
    first = _propose_and_confirm(
        memory_db,
        owner=OWNER,
        key="profile.self_reported_name",
        value="李梅",
        turn_id=turn.id,
    )
    before = repo.get_or_create_state(memory_db, OWNER).revision

    correction = repo.propose_entry(
        memory_db,
        owner_user_id=OWNER,
        key="profile.self_reported_name",
        value="王芳",
        origin="explicit",
        turn_id=turn.id,
    )
    memory_db.commit()
    # While it waits, the old value is still the one in force.
    assert (
        repo.get_active_entry(
            memory_db, owner_user_id=OWNER, key="profile.self_reported_name"
        ).id
        == first.id
    )

    repo.confirm_entry(
        memory_db,
        owner_user_id=OWNER,
        memory_id=correction.id,
        expected_version=correction.version,
        expected_active_version=first.version,
    )
    memory_db.commit()

    active = repo.get_active_entry(
        memory_db, owner_user_id=OWNER, key="profile.self_reported_name"
    )
    assert json.loads(active.value_json) == "王芳"
    assert memory_db.get(UserMemoryEntry, first.id).status == "superseded"
    assert repo.get_or_create_state(memory_db, OWNER).revision > before


def test_a_stale_candidate_cannot_overwrite_a_newer_correction(
    memory_db: Session,
) -> None:
    turn = _register(memory_db)
    original = _propose_and_confirm(
        memory_db,
        owner=OWNER,
        key="profile.self_reported_name",
        value="李梅",
        turn_id=turn.id,
    )
    # A background extraction raised against the value that was active then.
    late = repo.propose_entry(
        memory_db,
        owner_user_id=OWNER,
        key="profile.self_reported_name",
        value="小李",
        origin="automatic",
        turn_id=turn.id,
        expected_active_version=original.version,
    )
    memory_db.commit()

    # Meanwhile the employee corrected the name in another session.
    newer = repo.propose_entry(
        memory_db,
        owner_user_id=OWNER,
        key="profile.self_reported_name",
        value="王芳",
        origin="explicit",
        turn_id=turn.id,
        expected_active_version=original.version,
    )
    memory_db.commit()
    repo.confirm_entry(
        memory_db,
        owner_user_id=OWNER,
        memory_id=newer.id,
        expected_version=newer.version,
    )
    memory_db.commit()

    with pytest.raises(repo.MemoryConflictError):
        repo.confirm_entry(
            memory_db,
            owner_user_id=OWNER,
            memory_id=late.id,
            expected_version=late.version,
        )
    memory_db.rollback()
    assert (
        json.loads(
            repo.get_active_entry(
                memory_db, owner_user_id=OWNER, key="profile.self_reported_name"
            ).value_json
        )
        == "王芳"
    )
    # 用户重新核对当前版本后，可以明确确认不同值，而非永久卡死。
    current = repo.get_active_entry(
        memory_db, owner_user_id=OWNER, key="profile.self_reported_name"
    )
    confirmed = repo.confirm_entry(
        memory_db, owner_user_id=OWNER, memory_id=late.id,
        expected_version=late.version, expected_active_version=current.version,
    )
    assert json.loads(confirmed.value_json) == "小李"


@pytest.mark.parametrize(
    "origins", [("explicit", "automatic"), ("automatic", "explicit")]
)
def test_equivalent_proposals_share_confirmation_without_policy_escalation(
    memory_db: Session,
    origins: tuple[str, str],
) -> None:
    turn = _register(memory_db)
    first = repo.propose_entry(
        memory_db,
        owner_user_id=OWNER,
        key="profile.preferred_name",
        value="凯老师",
        origin=origins[0],
        turn_id=turn.id,
    )
    duplicate = repo.propose_entry(
        memory_db,
        owner_user_id=OWNER,
        key="profile.preferred_name",
        value="凯老师",
        origin=origins[1],
        turn_id=turn.id,
        auto_confirm=True,
    )
    assert duplicate.id == first.id
    assert duplicate.status == "candidate"
    repo.confirm_entry(
        memory_db,
        owner_user_id=OWNER,
        memory_id=first.id,
        expected_version=first.version,
    )
    status = service.read_turn_status(memory_db, owner_user_id=OWNER, turn_id=turn.id)
    assert [(item.id, item.status) for item in status.candidates] == [
        (first.id, "active")
    ]


def test_historical_equivalent_candidate_converges_with_its_receipt(
    memory_db: Session,
) -> None:
    turn = _register(memory_db)
    active = _propose_and_confirm(
        memory_db,
        owner=OWNER,
        key="profile.preferred_name",
        value="凯老师",
        turn_id=turn.id,
    )
    unrelated = _propose_and_confirm(
        memory_db,
        owner=OWNER,
        key="preference.language",
        value="zh-CN",
        turn_id=turn.id,
    )
    other_turn = _register(memory_db, request_key="duplicate-turn")
    # 直接构造旧版已经落库的重复候选，不经过新的共享提案去重。
    duplicate = UserMemoryEntry(
        id="ume-historical",
        owner_user_id=OWNER,
        key=active.key,
        value_json=active.value_json,
        value_hash=active.value_hash,
        origin="automatic",
        status="candidate",
        version=1,
        expected_active_version=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    memory_db.add(duplicate)
    memory_db.flush()
    repo.add_evidence(
        memory_db,
        owner_user_id=OWNER,
        memory_id=duplicate.id,
        turn_id=other_turn.id,
        quote="凯老师",
        quote_start=None,
        quote_end=None,
        evidence_kind="user_statement",
    )
    operation, _ = repo.claim_operation(
        memory_db,
        owner_user_id=OWNER,
        kind="propose",
        request_id="old-proposal",
        payload_hash=repo.request_hash({}),
        turn_id=other_turn.id,
    )
    repo.complete_operation(
        memory_db,
        operation=operation,
        status="succeeded",
        result_memory_id=duplicate.id,
        result_revision=1,
    )
    # 遗忘其他字段推进 epoch，不能抹掉已授权的称呼或它的来源。
    repo.forget_entry(
        memory_db,
        owner_user_id=OWNER,
        memory_id=unrelated.id,
        expected_version=unrelated.version,
    )
    status = service.read_turn_status(
        memory_db, owner_user_id=OWNER, turn_id=other_turn.id
    )
    assert [(item.id, item.status) for item in status.candidates] == [
        (active.id, "active")
    ]
    assert status.receipts[0].memory_id == active.id
    assert not status.receipts[0].revoked
    assert duplicate.status == "superseded"
    confirmed = repo.confirm_entry(
        memory_db,
        owner_user_id=OWNER,
        memory_id=duplicate.id,
        expected_version=1,
    )
    assert confirmed.id == active.id
    assert json.loads(confirmed.value_json) == "凯老师"
    changed_turn = _register(
        memory_db, request_key="later-name", text_value="以后叫我李老师"
    )
    _propose_and_confirm(
        memory_db,
        owner=OWNER,
        key="profile.preferred_name",
        value="李老师",
        turn_id=changed_turn.id,
    )
    with pytest.raises(repo.MemoryConflictError):
        repo.confirm_entry(
            memory_db,
            owner_user_id=OWNER,
            memory_id=duplicate.id,
            expected_version=1,
        )
    repo.invalidate_turn_sources(
        memory_db, owner_user_id=OWNER, turn_ids=[other_turn.id]
    )
    assert (
        json.loads(
            repo.get_active_entry(
                memory_db,
                owner_user_id=OWNER,
                key="profile.preferred_name",
            ).value_json
        )
        == "李老师"
    )


# ---------------------------------------------------------------------------
# Forgetting (A07, A09, A10).
# ---------------------------------------------------------------------------


def test_forget_wipes_values_and_blocks_automatic_resurrection(
    memory_db: Session,
) -> None:
    turn = _register(memory_db)
    entry = _propose_and_confirm(
        memory_db,
        owner=OWNER,
        key="profile.preferred_name",
        value="小梅",
        turn_id=turn.id,
    )
    operation, _ = repo.claim_operation(
        memory_db,
        owner_user_id=OWNER,
        kind="confirm",
        request_id="op-forget",
        payload_hash="hash",
    )
    repo.complete_operation(
        memory_db, operation=operation, status="succeeded", result_memory_id=entry.id
    )
    memory_db.commit()
    epoch_before = repo.get_or_create_state(memory_db, OWNER).epoch

    repo.forget_entry(
        memory_db,
        owner_user_id=OWNER,
        memory_id=entry.id,
        expected_version=entry.version,
    )
    memory_db.commit()

    tombstone = memory_db.get(UserMemoryEntry, entry.id)
    assert tombstone.status == "forgotten"
    # Nothing that could reconstruct the value survives - not even the hash,
    # which for a short name is trivially reversible.
    assert tombstone.value_json is None
    assert tombstone.value_hash is None
    assert tombstone.forgotten_source_cutoff_at is not None
    evidence = memory_db.scalars(
        UserMemoryEvidence.__table__.select().where(
            UserMemoryEvidence.memory_id == entry.id
        )
    ).all()
    assert all(row.quote is None for row in memory_db.query(UserMemoryEvidence).all())
    assert evidence  # the bare provenance link stays as an anti-resurrection tombstone

    assert repo.get_or_create_state(memory_db, OWNER).epoch > epoch_before
    assert (
        repo.automatic_cutoff_for_key(
            memory_db, owner_user_id=OWNER, key="profile.preferred_name"
        )
        is not None
    )
    # A replayed confirm reports the revoked state; it must not hand back the
    # deleted value or bring it back.
    assert repo.receipt_is_revoked(memory_db, operation) is True
    with pytest.raises(repo.MemoryRepositoryError) as excinfo:
        repo.propose_entry(
            memory_db,
            owner_user_id=OWNER,
            key="profile.preferred_name",
            value="小梅",
            origin="automatic",
            turn_id=turn.id,
        )
    assert excinfo.value.code == "memory_source_invalid"
    memory_db.rollback()


def test_clear_forgets_everything_and_fences_queued_extraction(
    memory_db: Session,
) -> None:
    turn = _register(memory_db)
    state = repo.get_or_create_state(memory_db, OWNER)
    job = repo.enqueue_extraction_job(
        memory_db,
        owner_user_id=OWNER,
        turn_id=turn.id,
        source_hash_value=turn.source_hash,
        extractor_version="v1",
        epoch=state.epoch,
    )
    _propose_and_confirm(
        memory_db,
        owner=OWNER,
        key="preference.language",
        value="zh-CN",
        turn_id=turn.id,
    )
    memory_db.commit()

    revision = repo.get_or_create_state(memory_db, OWNER).revision
    cleared = repo.clear_entries(
        memory_db, owner_user_id=OWNER, expected_revision=revision
    )
    memory_db.commit()
    assert cleared == 1
    assert repo.list_active_entries(memory_db, owner_user_id=OWNER) == []
    assert memory_db.get(UserMemoryJob, job.id).status == "cancelled"

    with pytest.raises(repo.MemoryConflictError) as excinfo:
        repo.clear_entries(memory_db, owner_user_id=OWNER, expected_revision=revision)
    assert excinfo.value.code == "memory_revision_conflict"


# ---------------------------------------------------------------------------
# Source lifecycle (A10).
# ---------------------------------------------------------------------------


def test_identical_replace_keeps_the_source_but_a_real_edit_invalidates_it(
    memory_db: Session,
) -> None:
    message_id = _seed_chat_message(memory_db, owner=OWNER, content="我叫李梅")
    turn = _register(memory_db, text_value="我叫李梅", chat_message_id=message_id)
    state = repo.get_or_create_state(memory_db, OWNER)
    job = repo.enqueue_extraction_job(
        memory_db,
        owner_user_id=OWNER,
        turn_id=turn.id,
        source_hash_value=turn.source_hash,
        extractor_version="v1",
        epoch=state.epoch,
    )
    memory_db.commit()
    candidate = repo.propose_entry(
        memory_db,
        owner_user_id=OWNER,
        key="profile.self_reported_name",
        value="李梅",
        origin="explicit",
        turn_id=turn.id,
        quote="我叫李梅",
    )
    memory_db.commit()
    session = memory_db.get(ChatSession, f"sess-{OWNER}")
    revision = repo.get_or_create_state(memory_db, OWNER).revision

    # A transport-driven delete/reinsert of the same text is not new evidence
    # and must not cancel anything.
    chat_repo.replace_messages(
        memory_db,
        session,
        [{"id": message_id, "role": "user", "content": "我叫李梅"}],
        base_revision=session.messages_revision,
    )
    memory_db.commit()
    assert repo.get_or_create_state(memory_db, OWNER).revision == revision
    assert memory_db.get(UserMemoryTurn, turn.id).chat_message_id == message_id
    assert (
        service.resolve_turn_source_text(
            memory_db, memory_db.get(UserMemoryTurn, turn.id)
        )
        == "我叫李梅"
    )
    assert memory_db.get(UserMemoryEntry, candidate.id).status == "candidate"
    assert memory_db.get(UserMemoryTurn, turn.id).status == "live"
    assert memory_db.get(UserMemoryJob, job.id).status == "pending"

    # A real content change invalidates the source and cancels its extraction.
    chat_repo.replace_messages(
        memory_db,
        session,
        [{"id": message_id, "role": "user", "content": "我叫王芳"}],
        base_revision=session.messages_revision,
    )
    memory_db.rollback()
    assert memory_db.get(UserMemoryTurn, turn.id).status == "live"
    assert memory_db.get(ChatMessage, message_id).content == "我叫李梅"
    chat_repo.replace_messages(
        memory_db,
        session,
        [{"id": message_id, "role": "user", "content": "我叫王芳"}],
        base_revision=session.messages_revision,
    )
    memory_db.commit()
    assert memory_db.get(UserMemoryEntry, candidate.id).status == "expired"
    assert memory_db.get(UserMemoryEntry, candidate.id).value_json is None
    assert (
        memory_db.scalar(
            select(UserMemoryEvidence.quote).where(
                UserMemoryEvidence.memory_id == candidate.id
            )
        )
        is None
    )
    assert memory_db.get(UserMemoryTurn, turn.id).status == "invalidated"
    assert memory_db.get(UserMemoryJob, job.id).status == "cancelled"
    assert (
        repo.job_is_still_authorized(memory_db, memory_db.get(UserMemoryJob, job.id))
        is False
    )


def test_deleting_the_source_expires_automatic_memory_but_not_a_confirmed_one(
    memory_db: Session,
) -> None:
    message_id = _seed_chat_message(memory_db, owner=OWNER, content="我用简体中文")
    auto_turn = _register(
        memory_db,
        text_value="我用简体中文",
        request_key="auto",
        chat_message_id=message_id,
    )
    manual_turn = _register(
        memory_db, text_value="确认", request_key="manual", channel="manual"
    )

    automatic = _propose_and_confirm(
        memory_db,
        owner=OWNER,
        key="preference.language",
        value="zh-CN",
        turn_id=auto_turn.id,
        origin="automatic",
    )
    confirmed = repo.propose_entry(
        memory_db,
        owner_user_id=OWNER,
        key="preference.response_length",
        value="concise",
        origin="automatic",
        turn_id=auto_turn.id,
    )
    memory_db.commit()
    repo.confirm_entry(
        memory_db,
        owner_user_id=OWNER,
        memory_id=confirmed.id,
        expected_version=confirmed.version,
    )
    # The employee's own confirmation is an independent source of its own.
    repo.add_evidence(
        memory_db,
        owner_user_id=OWNER,
        memory_id=confirmed.id,
        turn_id=manual_turn.id,
        quote=None,
        quote_start=None,
        quote_end=None,
        evidence_kind="manual_confirmation",
    )
    memory_db.commit()

    chat_repo.delete_session(memory_db, memory_db.get(ChatSession, f"sess-{OWNER}"))
    memory_db.commit()
    memory_db.expire_all()

    assert memory_db.get(ChatMessage, message_id) is None
    assert memory_db.get(UserMemoryTurn, auto_turn.id).status == "invalidated"
    assert memory_db.get(UserMemoryTurn, auto_turn.id).chat_message_id is None
    assert memory_db.get(UserMemoryEntry, automatic.id).status == "expired"
    # Deleting the original chat does not pretend the confirmation never
    # happened; the employee can still forget this one on its own.
    assert memory_db.get(UserMemoryEntry, confirmed.id).status == "active"


# ---------------------------------------------------------------------------
# Background execution (A06).
# ---------------------------------------------------------------------------


def test_only_one_worker_claims_a_job_and_a_stale_lease_cannot_commit(
    memory_db: Session,
) -> None:
    turn = _register(memory_db)
    state = repo.get_or_create_state(memory_db, OWNER)
    job = repo.enqueue_extraction_job(
        memory_db,
        owner_user_id=OWNER,
        turn_id=turn.id,
        source_hash_value=turn.source_hash,
        extractor_version="v1",
        epoch=state.epoch,
    )
    memory_db.commit()
    # The idempotency key is what stops a retried registration paying twice.
    assert (
        repo.enqueue_extraction_job(
            memory_db,
            owner_user_id=OWNER,
            turn_id=turn.id,
            source_hash_value=turn.source_hash,
            extractor_version="v1",
            epoch=state.epoch,
        ).id
        == job.id
    )
    memory_db.commit()

    worker_a = database.SessionLocal()
    worker_b = database.SessionLocal()
    try:
        claimed = repo.claim_extraction_job(
            worker_a, lease_owner="worker-a", lease_seconds=120
        )
        assert claimed is not None and claimed.id == job.id
        generation = claimed.claim_generation
        # A second worker finds nothing while the lease is held.
        assert (
            repo.claim_extraction_job(
                worker_b, lease_owner="worker-b", lease_seconds=120
            )
            is None
        )

        # The lease expires and the job is re-claimed, which moves the
        # generation; the original worker's late commit is then refused.
        worker_a.execute(
            text("UPDATE user_memory_jobs SET lease_expires_at = :past WHERE id = :id"),
            {"past": datetime.now(timezone.utc) - timedelta(minutes=5), "id": job.id},
        )
        worker_a.commit()
        reclaimed = repo.claim_extraction_job(
            worker_b, lease_owner="worker-b", lease_seconds=120
        )
        assert reclaimed is not None
        assert reclaimed.claim_generation == generation + 1

        assert (
            repo.finish_extraction_job(
                worker_a,
                job_id=job.id,
                lease_owner="worker-a",
                claim_generation=generation,
                status="succeeded",
            )
            is False
        )
        # The repository never commits for the caller, so the refused UPDATE
        # above still opened a write transaction on worker A. Releasing it is
        # the caller's job; on SQLite a worker that forgets blocks every other
        # writer.
        worker_a.rollback()
        assert (
            repo.finish_extraction_job(
                worker_b,
                job_id=job.id,
                lease_owner="worker-b",
                claim_generation=reclaimed.claim_generation,
                status="succeeded",
            )
            is True
        )
        worker_b.commit()
    finally:
        worker_a.close()
        worker_b.close()


def test_manual_mode_and_a_moved_epoch_both_deauthorize_a_running_job(
    memory_db: Session,
) -> None:
    message_id = _seed_chat_message(memory_db, owner=OWNER, content="我叫李梅")
    turn = _register(memory_db, chat_message_id=message_id)
    state = repo.get_or_create_state(memory_db, OWNER)
    state.learning_mode = "suggest"
    memory_db.commit()
    job = repo.enqueue_extraction_job(
        memory_db,
        owner_user_id=OWNER,
        turn_id=turn.id,
        source_hash_value=turn.source_hash,
        extractor_version="v1",
        epoch=state.epoch,
    )
    memory_db.commit()
    repo.close_turn(memory_db, owner_user_id=OWNER, turn_id=turn.id)
    memory_db.commit()
    job = repo.claim_extraction_job(memory_db, lease_owner="worker", lease_seconds=60)
    assert job is not None
    assert repo.job_is_still_authorized(memory_db, job) is True

    repo.update_settings(
        memory_db,
        owner_user_id=OWNER,
        expected_revision=repo.get_or_create_state(memory_db, OWNER).revision,
        learning_mode="manual",
    )
    memory_db.commit()
    # Switching learning off both fences the epoch and cancels queued work.
    assert (
        repo.job_is_still_authorized(memory_db, memory_db.get(UserMemoryJob, job.id))
        is False
    )
    assert memory_db.get(UserMemoryJob, job.id).status == "cancelled"


def test_an_expired_lease_cannot_finish_even_before_another_worker_claims(
    memory_db: Session,
) -> None:
    turn = _register(memory_db)
    repo.enqueue_extraction_job(
        memory_db,
        owner_user_id=OWNER,
        turn_id=turn.id,
        source_hash_value=turn.source_hash,
        extractor_version="v1",
        epoch=turn.epoch,
    )
    memory_db.commit()
    job = repo.claim_extraction_job(memory_db, lease_owner="slow", lease_seconds=60)
    assert job is not None
    assert not memory_db.in_transaction()
    memory_db.execute(
        update(UserMemoryJob)
        .where(UserMemoryJob.id == job.id)
        .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    )
    memory_db.commit()
    assert not repo.finish_extraction_job(
        memory_db,
        job_id=job.id,
        lease_owner="slow",
        claim_generation=job.claim_generation,
        status="succeeded",
    )
    memory_db.rollback()
    assert memory_db.get(UserMemoryJob, job.id).status == "running"


@pytest.mark.parametrize(
    "revocation", ["disabled_user", "disabled_use", "edited_source"]
)
def test_running_job_rechecks_current_authority_not_identity_map(
    memory_db: Session,
    revocation: str,
) -> None:
    message_id = _seed_chat_message(memory_db, owner=OWNER, content="我叫李梅")
    turn = _register(memory_db, chat_message_id=message_id)
    state = repo.get_or_create_state(memory_db, OWNER)
    state.learning_mode = "suggest"
    repo.enqueue_extraction_job(
        memory_db,
        owner_user_id=OWNER,
        turn_id=turn.id,
        source_hash_value=turn.source_hash,
        extractor_version="v1",
        epoch=turn.epoch,
    )
    memory_db.commit()
    job = repo.claim_extraction_job(memory_db, lease_owner="worker", lease_seconds=60)
    assert job is not None
    # Deliberately leave stale ORM instances in the Session. Source validation
    # must read the current durable fields even if another path bypassed hooks.
    message = memory_db.get(ChatMessage, message_id)
    user = memory_db.get(User, OWNER)
    state = repo.get_or_create_state(memory_db, OWNER)
    if revocation == "disabled_user":
        statement = update(User).where(User.id == OWNER).values(status="disabled")
    elif revocation == "disabled_use":
        statement = (
            update(UserMemoryState)
            .where(UserMemoryState.owner_user_id == OWNER)
            .values(use_enabled=False)
        )
    else:
        statement = (
            update(ChatMessage)
            .where(ChatMessage.id == message_id)
            .values(content="我叫王芳")
        )
    memory_db.execute(statement.execution_options(synchronize_session=False))
    assert message.content == "我叫李梅"
    assert user.status == "active"
    assert state.use_enabled is True
    assert not repo.job_is_still_authorized(memory_db, job)
    memory_db.rollback()


def test_legacy_merge_invalidates_only_when_the_stored_body_really_changes(
    memory_db: Session,
) -> None:
    message_id = _seed_chat_message(memory_db, owner=OWNER, content="")
    turn = _register(memory_db, text_value="", chat_message_id=message_id)
    session = memory_db.get(ChatSession, f"sess-{OWNER}")
    chat_repo.replace_messages(
        memory_db,
        session,
        [{"id": message_id, "role": "user", "content": "", "images": []}],
    )
    memory_db.commit()
    assert (
        repo.get_owned_turn(memory_db, owner_user_id=OWNER, turn_id=turn.id).status
        == "live"
    )
    chat_repo.replace_messages(
        memory_db,
        session,
        [{"id": message_id, "role": "user", "content": "我叫李梅"}],
    )
    memory_db.commit()
    assert memory_db.get(ChatMessage, message_id).content == "我叫李梅"
    assert (
        repo.get_owned_turn(memory_db, owner_user_id=OWNER, turn_id=turn.id).status
        == "invalidated"
    )


def test_receipt_and_initial_state_do_not_escape_a_rolled_back_transaction(
    memory_db: Session,
) -> None:
    repo.claim_operation(
        memory_db,
        owner_user_id=OWNER,
        kind="confirm",
        request_id="rolled-back",
        payload_hash="payload",
    )
    memory_db.rollback()
    assert (
        memory_db.scalar(
            select(UserMemoryOperation.id).where(
                UserMemoryOperation.owner_user_id == OWNER,
                UserMemoryOperation.request_id == "rolled-back",
            )
        )
        is None
    )
    assert memory_db.get(UserMemoryState, OWNER) is None


def test_fork_publication_and_client_restore_do_not_create_memory_evidence(
    memory_db: Session,
) -> None:
    message_id = _seed_chat_message(memory_db, owner=OWNER, content="我叫李梅")
    turn = _register(memory_db, chat_message_id=message_id)
    entry = _propose_and_confirm(
        memory_db,
        owner=OWNER,
        key="profile.self_reported_name",
        value="李梅",
        turn_id=turn.id,
        origin="automatic",
    )
    source = memory_db.get(ChatSession, f"sess-{OWNER}")
    assistant = chat_repo.append_messages(
        memory_db, source, [{"id": "reply", "role": "assistant", "content": "你好"}]
    )[0]
    memory_db.commit()
    memory_db.expire(source, ["messages"])
    forked = chat_repo.fork_session(
        memory_db,
        source_session=source,
        owner_user_id=OWNER,
        fork_message_id=assistant.id,
    )
    publication = chat_repo.create_publication(
        memory_db,
        source,
        shared_by_user_id=OWNER,
        validated_space_ids=[],
        share_scope="all_internal",
        allow_cite=True,
    )
    forked.forked_from_session_id = None
    forked.forked_from_message_id = None
    memory_db.flush()
    restored = chat_repo.restore_session_for_owner(
        memory_db,
        session_id="chs_restored_memory",
        owner_user_id=OWNER,
    )
    chat_repo.append_missing_messages(
        memory_db,
        restored,
        chat_repo.publication_snapshot(publication),
    )
    memory_db.commit()
    assert memory_db.scalars(select(UserMemoryTurn.id)).all() == [turn.id]
    assert memory_db.scalars(select(UserMemoryEvidence.turn_id)).all() == [turn.id]
    assert memory_db.scalars(select(UserMemoryJob.id)).all() == []
    # A copied prefix is not an independent source: deleting the original must
    # still revoke the derived fact even after lineage metadata is consumed.
    chat_repo.delete_session(memory_db, source)
    memory_db.commit()
    memory_db.expire_all()
    assert memory_db.get(UserMemoryEntry, entry.id).status == "expired"
    assert any(item.content == "我叫李梅" for item in forked.messages)
    assert any(item.content == "我叫李梅" for item in restored.messages)


def test_cleanup_erases_completed_source_but_preserves_verified_active_evidence(
    memory_db: Session,
) -> None:
    turn = repo.register_turn(
        memory_db,
        owner_user_id=OWNER,
        channel="open",
        request_key="open-input",
        raw_text="\n 我叫李梅 \n",
        source_text_safe="\n 我叫李梅 \n",
        source_text_retention=timedelta(days=7),
    )
    state = repo.get_or_create_state(memory_db, OWNER)
    state.learning_mode = "suggest"
    repo.enqueue_extraction_job(
        memory_db,
        owner_user_id=OWNER,
        turn_id=turn.id,
        source_hash_value=turn.source_hash,
        extractor_version="v1",
        epoch=turn.epoch,
    )
    memory_db.commit()
    job = repo.claim_extraction_job(memory_db, lease_owner="worker", lease_seconds=60)
    assert job is not None
    assert repo.job_is_still_authorized(memory_db, job)
    entry = repo.propose_entry(
        memory_db,
        owner_user_id=OWNER,
        key="profile.self_reported_name",
        value="李梅",
        origin="automatic",
        turn_id=turn.id,
        quote="我叫李梅",
    )
    entry = repo.confirm_entry(
        memory_db,
        owner_user_id=OWNER,
        memory_id=entry.id,
        expected_version=entry.version,
    )
    assert repo.finish_extraction_job(
        memory_db,
        job_id=job.id,
        lease_owner="worker",
        claim_generation=job.claim_generation,
        status="succeeded",
    )
    memory_db.commit()
    repo.cleanup_memory_data(memory_db, limit=1)
    memory_db.commit()
    assert memory_db.get(UserMemoryTurn, turn.id).source_text_safe is None
    assert memory_db.get(UserMemoryEntry, entry.id).status == "active"
    assert (
        memory_db.scalar(
            select(UserMemoryEvidence.quote).where(
                UserMemoryEvidence.memory_id == entry.id,
            )
        )
        == "我叫李梅"
    )
    assert repo.expire_unsourced_automatic_entries(memory_db, owner_user_id=OWNER) == 0


def test_cleanup_terminates_exhausted_claim_without_erasing_unknown_usage(
    memory_db: Session,
) -> None:
    turn = repo.register_turn(
        memory_db,
        owner_user_id=OWNER,
        channel="open",
        request_key="crashed-input",
        raw_text="我叫李梅",
        source_text_safe="我叫李梅",
        source_text_retention=timedelta(days=7),
    )
    repo.enqueue_extraction_job(
        memory_db,
        owner_user_id=OWNER,
        turn_id=turn.id,
        source_hash_value=turn.source_hash,
        extractor_version="v1",
        epoch=turn.epoch,
    )
    memory_db.commit()
    job = repo.claim_extraction_job(memory_db, lease_owner="crashed", lease_seconds=60)
    assert job is not None
    operation, _ = repo.claim_operation(
        memory_db,
        owner_user_id=OWNER,
        kind="extract_attempt",
        request_id=f"{job.id}:{job.claim_generation}",
        payload_hash="request",
        turn_id=turn.id,
        job_id=job.id,
    )
    usage = json.dumps(
        {
            "calls": [
                {
                    "provider": "openai-compatible",
                    "model": "test-model",
                    "usage_status": "unknown",
                    "cost_status": "reserved_unknown",
                    "cost": 1.5,
                }
            ]
        }
    )
    operation.usage_json = usage
    memory_db.execute(
        update(UserMemoryJob)
        .where(UserMemoryJob.id == job.id)
        .values(
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )
    memory_db.commit()
    repo.cleanup_memory_data(memory_db, limit=1, max_attempts=1)
    memory_db.commit()
    assert memory_db.get(UserMemoryJob, job.id).status == "failed"
    assert memory_db.get(UserMemoryOperation, operation.id).status == "failed"
    assert (
        memory_db.get(UserMemoryOperation, operation.id).error_code
        == "memory_outcome_unknown"
    )
    assert memory_db.get(UserMemoryOperation, operation.id).usage_json == usage
    assert memory_db.get(UserMemoryTurn, turn.id).source_text_safe is None


def test_two_initial_candidates_cannot_bypass_the_absent_active_version_fence(
    memory_db: Session,
) -> None:
    turn = _register(memory_db)
    first = repo.propose_entry(
        memory_db,
        owner_user_id=OWNER,
        key="profile.preferred_name",
        value="小梅",
        origin="automatic",
        turn_id=turn.id,
    )
    second = repo.propose_entry(
        memory_db,
        owner_user_id=OWNER,
        key="profile.preferred_name",
        value="李工",
        origin="explicit",
        turn_id=turn.id,
    )
    repo.confirm_entry(
        memory_db,
        owner_user_id=OWNER,
        memory_id=second.id,
        expected_version=second.version,
    )
    memory_db.commit()
    with pytest.raises(repo.MemoryConflictError):
        repo.confirm_entry(
            memory_db,
            owner_user_id=OWNER,
            memory_id=first.id,
            expected_version=first.version,
        )
    memory_db.rollback()
    assert (
        json.loads(
            repo.get_active_entry(
                memory_db,
                owner_user_id=OWNER,
                key="profile.preferred_name",
            ).value_json
        )
        == "李工"
    )


def test_user_deletion_cascades_only_that_owners_memory(
    memory_db: Session,
) -> None:
    for owner in (OWNER, OTHER):
        turn = _register(memory_db, owner=owner)
        _propose_and_confirm(
            memory_db,
            owner=owner,
            key="profile.self_reported_name",
            value="李梅",
            turn_id=turn.id,
        )
        repo.enqueue_extraction_job(
            memory_db,
            owner_user_id=owner,
            turn_id=turn.id,
            source_hash_value=turn.source_hash,
            extractor_version="v1",
            epoch=turn.epoch,
        )
    memory_db.commit()
    memory_db.delete(memory_db.get(User, OWNER))
    memory_db.commit()
    for model in (
        UserMemoryState,
        UserMemoryEntry,
        UserMemoryTurn,
        UserMemoryEvidence,
        UserMemoryJob,
    ):
        assert memory_db.scalars(select(model.owner_user_id)).all() == [OTHER]


# ---------------------------------------------------------------------------
# Migration contract (A03, A10).
# ---------------------------------------------------------------------------


def test_migration_round_trip_creates_owner_scoped_constraints(tmp_path: Path) -> None:
    """Upgrade, downgrade one revision, upgrade again on an isolated SQLite file.

    Downgrade is exercised here only; production never runs it. Foreign keys are
    switched on explicitly so the cross-owner insert is actually refused rather
    than quietly accepted.
    """

    database_path = tmp_path / "user-memory-migration.sqlite3"
    original = settings.database_url
    settings.database_url = f"sqlite:///{database_path}"
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    # In-process migrations must not replace or disable other tests' loggers.
    config.config_file_name = None
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "20260908_0087")
        engine = create_engine(settings.database_url)
        assert "user_memory_entries" not in inspect(engine).get_table_names()
        engine.dispose()
        command.upgrade(config, "head")

        engine = create_engine(settings.database_url)

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_connection, _record):  # pragma: no cover - trivial
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {
            "user_memory_states",
            "user_memory_turns",
            "user_memory_entries",
            "user_memory_evidence",
            "user_memory_jobs",
            "user_memory_operations",
        } <= tables

        # The non-partial composite UNIQUE that makes the evidence FK legal.
        entry_uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("user_memory_entries")
        }
        turn_uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("user_memory_turns")
        }
        assert ("owner_user_id", "id") in entry_uniques
        assert ("owner_user_id", "id") in turn_uniques

        # A separate, partial index - it does not replace the constraint above.
        active_index = next(
            item
            for item in inspector.get_indexes("user_memory_entries")
            if item["name"] == "uq_user_memory_entries_active_key"
        )
        assert active_index["unique"]  # SQLite reports 1, PostgreSQL True
        assert tuple(active_index["column_names"]) == ("owner_user_id", "key")
        index_sql = _index_sql(engine, "uq_user_memory_entries_active_key")
        assert "WHERE" in index_sql.upper() and "active" in index_sql

        evidence_fks = {
            (tuple(item["constrained_columns"]), tuple(item["referred_columns"]))
            for item in inspector.get_foreign_keys("user_memory_evidence")
        }
        assert (("owner_user_id", "memory_id"), ("owner_user_id", "id")) in evidence_fks
        assert (("owner_user_id", "turn_id"), ("owner_user_id", "id")) in evidence_fks

        _assert_database_enforces_owner_binding(engine)
        engine.dispose()
    finally:
        settings.database_url = original


def _index_sql(engine, name: str) -> str:
    with engine.begin() as connection:
        return (
            connection.execute(
                text("SELECT sql FROM sqlite_master WHERE type='index' AND name=:n"),
                {"n": name},
            ).scalar()
            or ""
        )


def _assert_database_enforces_owner_binding(engine) -> None:
    """Legal evidence writes; both cross-owner mis-bindings are refused."""

    stamp = datetime.now(timezone.utc).isoformat(sep=" ")
    with engine.begin() as connection:
        for user_id, username in ((OWNER, "mei"), (OTHER, "fang")):
            connection.execute(
                text(
                    "INSERT INTO users (id, username, email, display_name, "
                    "password_hash, role, status, linkmind_login_status, "
                    "first_login, auth_version, failed_attempts, created_at) "
                    "VALUES (:id, :u, :e, '同名同事', 'x', 'user', 'active', "
                    "'logged_out', TRUE, 0, 0, :ts)"
                ),
                {
                    "id": user_id,
                    "u": username,
                    "e": f"{username}@example.com",
                    "ts": stamp,
                },
            )
        for user_id, turn_id, entry_id in (
            (OWNER, "turn-mei", "entry-mei"),
            (OTHER, "turn-fang", "entry-fang"),
        ):
            connection.execute(
                text(
                    "INSERT INTO user_memory_turns (id, owner_user_id, channel, "
                    "request_key, source_hash, epoch, status, created_at) VALUES "
                    "(:t, :o, 'web', :t, 'hash', 1, 'live', :ts)"
                ),
                {"t": turn_id, "o": user_id, "ts": stamp},
            )
            connection.execute(
                text(
                    "INSERT INTO user_memory_entries (id, owner_user_id, key, "
                    "value_json, status, origin, version, created_at, updated_at) "
                    "VALUES (:e, :o, 'profile.self_reported_name', '\"n\"', "
                    "'active', 'manual', 1, :ts, :ts)"
                ),
                {"e": entry_id, "o": user_id, "ts": stamp},
            )
        connection.execute(
            text(
                "INSERT INTO user_memory_evidence (id, owner_user_id, memory_id, "
                "turn_id, source_hash, evidence_kind, created_at) VALUES "
                "('ok', :o, 'entry-mei', 'turn-mei', 'hash', 'user_statement', :ts)"
            ),
            {"o": OWNER, "ts": stamp},
        )

    for evidence_id, owner, memory_id, turn_id in (
        ("bad-turn", OWNER, "entry-mei", "turn-fang"),
        ("bad-entry", OTHER, "entry-mei", "turn-fang"),
    ):
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO user_memory_evidence (id, owner_user_id, "
                        "memory_id, turn_id, source_hash, evidence_kind, "
                        "created_at) VALUES (:i, :o, :m, :t, 'hash', "
                        "'user_statement', :ts)"
                    ),
                    {
                        "i": evidence_id,
                        "o": owner,
                        "m": memory_id,
                        "t": turn_id,
                        "ts": stamp,
                    },
                )


# ---------------------------------------------------------------------------
# Policy: what may become a fact at all (A02, A04).
# ---------------------------------------------------------------------------


def _proposal(**overrides):
    from app.schemas.user_memory import MemoryProposalInput

    payload = {
        "key": "profile.self_reported_name",
        "value": "李梅",
        "quote": "我叫李梅",
        "quote_start": 0,
        "quote_end": 4,
        "self_attribution": "self",
        "temporality": "persistent",
        "explicitness": "explicit",
    }
    payload.update(overrides)
    return MemoryProposalInput.model_validate(payload)


def test_evidence_must_actually_occur_where_the_model_says_it_does(
    memory_db: Session,
) -> None:
    message_id = _seed_chat_message(memory_db, owner=OWNER, content="我叫李梅")
    turn = _register(memory_db, text_value="我叫李梅", chat_message_id=message_id)

    value, quote = service.validate_memory_proposal(
        memory_db, owner_user_id=OWNER, turn=turn, proposal=_proposal()
    )
    assert (value, quote) == ("李梅", "我叫李梅")

    # The words appearing somewhere is not evidence that they were said there.
    for bad in (
        {"quote_start": 1, "quote_end": 5},
        {"quote": "我叫王芳"},
        {"quote_start": 0, "quote_end": 999},
    ):
        with pytest.raises(service.MemoryPolicyError) as excinfo:
            service.validate_memory_proposal(
                memory_db,
                owner_user_id=OWNER,
                turn=turn,
                proposal=_proposal(**bad),
            )
        assert excinfo.value.code == "memory_source_invalid"


def test_ghostwriting_this_turn_instructions_and_secrets_never_become_memory(
    memory_db: Session,
) -> None:
    cases = [
        # Writing on someone else's behalf is not a fact about the employee.
        ({"self_attribution": "other"}, "我叫李梅", "memory_value_invalid"),
        ({"self_attribution": "uncertain"}, "我叫李梅", "memory_value_invalid"),
        # A one-off request for this reply is not a long-term preference.
        ({"temporality": "current_turn"}, "我叫李梅", "memory_value_invalid"),
        ({"temporality": "temporary"}, "我叫李梅", "memory_value_invalid"),
    ]
    for index, (overrides, content, expected) in enumerate(cases):
        message_id = _seed_chat_message(
            memory_db, owner=OWNER, content=content + "x" * index
        )
        turn = _register(
            memory_db,
            text_value=content + "x" * index,
            request_key=f"case-{index}",
            chat_message_id=message_id,
        )
        with pytest.raises(service.MemoryPolicyError) as excinfo:
            service.validate_memory_proposal(
                memory_db,
                owner_user_id=OWNER,
                turn=turn,
                proposal=_proposal(**overrides),
            )
        assert excinfo.value.code == expected

    # A credential is refused outright, so neither the entry nor the receipt
    # ever holds a copy of it.
    secret = "我的密钥是 sk-live-0123456789abcdefghij"
    message_id = _seed_chat_message(memory_db, owner=OWNER, content=secret)
    turn = _register(
        memory_db, text_value=secret, request_key="secret", chat_message_id=message_id
    )
    with pytest.raises(service.MemoryPolicyError) as excinfo:
        service.validate_memory_proposal(
            memory_db,
            owner_user_id=OWNER,
            turn=turn,
            proposal=_proposal(
                key="work.current_focus",
                value="sk-live-0123456789abcdefghij",
                quote=secret,
                quote_start=0,
                quote_end=len(secret),
            ),
        )
    assert excinfo.value.code == "memory_value_invalid"
    assert service.contains_secret("我的身份证是 11010119900307123X") is True


def test_a_source_from_before_a_forget_barrier_cannot_resurrect_the_key(
    memory_db: Session,
) -> None:
    message_id = _seed_chat_message(memory_db, owner=OWNER, content="我叫李梅")
    turn = _register(memory_db, text_value="我叫李梅", chat_message_id=message_id)
    entry = _propose_and_confirm(
        memory_db,
        owner=OWNER,
        key="profile.self_reported_name",
        value="李梅",
        turn_id=turn.id,
    )
    repo.forget_entry(
        memory_db,
        owner_user_id=OWNER,
        memory_id=entry.id,
        expected_version=entry.version,
    )
    memory_db.commit()

    # Re-reading the very conversation that first taught the name is not a new
    # statement; re-saving needs input from after the barrier.
    with pytest.raises(service.MemoryPolicyError) as excinfo:
        service.validate_memory_proposal(
            memory_db, owner_user_id=OWNER, turn=turn, proposal=_proposal()
        )
    assert excinfo.value.code == "memory_source_invalid"


def test_only_the_opt_in_policy_can_activate_without_the_employee(
    memory_db: Session,
) -> None:
    state = repo.get_or_create_state(memory_db, OWNER)

    # Default and suggest never activate on their own.
    for mode in ("manual", "suggest"):
        state.learning_mode = mode
        assert (
            service.apply_memory_policy(
                state, proposal=_proposal(), has_conflicting_active=False
            )
            is False
        )

    state.learning_mode = "auto_low_risk"
    assert (
        service.apply_memory_policy(
            state, proposal=_proposal(), has_conflicting_active=False
        )
        is True
    )
    # An inference stays a suggestion however often it recurs.
    assert (
        service.apply_memory_policy(
            state,
            proposal=_proposal(explicitness="inferred"),
            has_conflicting_active=False,
        )
        is False
    )
    # A conflict keeps the old value and shows the new one.
    assert (
        service.apply_memory_policy(
            state, proposal=_proposal(), has_conflicting_active=True
        )
        is False
    )
    # Work descriptions are suggestion-only whatever the mode.
    assert (
        service.apply_memory_policy(
            state,
            proposal=_proposal(key="work.role_description", value="后端开发"),
            has_conflicting_active=False,
        )
        is False
    )


# ---------------------------------------------------------------------------
# Addressing and snapshot (A02, A08, A09).
# ---------------------------------------------------------------------------


def test_addressing_follows_nickname_then_self_reported_then_account(
    memory_db: Session,
) -> None:
    from app.models.auth import User as UserModel

    user = memory_db.get(UserModel, OWNER)
    turn = _register(memory_db)

    assert (
        service.resolve_addressing_name(memory_db, owner_user_id=OWNER, user=user)
        == "同名同事"
    )

    _propose_and_confirm(
        memory_db,
        owner=OWNER,
        key="profile.self_reported_name",
        value="李梅",
        turn_id=turn.id,
    )
    assert (
        service.resolve_addressing_name(memory_db, owner_user_id=OWNER, user=user)
        == "李梅"
    )

    nickname = _propose_and_confirm(
        memory_db,
        owner=OWNER,
        key="profile.preferred_name",
        value="小梅",
        turn_id=turn.id,
    )
    assert (
        service.resolve_addressing_name(memory_db, owner_user_id=OWNER, user=user)
        == "小梅"
    )

    # Forgetting only the nickname falls back to the self-reported name, and
    # the account display name is never overwritten in either direction.
    repo.forget_entry(
        memory_db,
        owner_user_id=OWNER,
        memory_id=nickname.id,
        expected_version=nickname.version,
    )
    memory_db.commit()
    assert (
        service.resolve_addressing_name(memory_db, owner_user_id=OWNER, user=user)
        == "李梅"
    )
    assert memory_db.get(UserModel, OWNER).display_name == "同名同事"


def test_the_snapshot_is_bounded_and_stops_being_current_when_it_should(
    memory_db: Session, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "personal_memory_enabled", True)
    turn = _register(memory_db)
    _propose_and_confirm(
        memory_db,
        owner=OWNER,
        key="profile.preferred_name",
        value="小梅",
        turn_id=turn.id,
    )
    _propose_and_confirm(
        memory_db,
        owner=OWNER,
        key="preference.language",
        value="zh-CN",
        turn_id=turn.id,
    )
    snapshot = service.build_memory_snapshot(memory_db, owner_user_id=OWNER)
    assert [item.key for item in snapshot.entries][0] == "profile.preferred_name"
    assert snapshot.addressing_name == "小梅"

    assert service.snapshot_is_current(
        memory_db,
        owner_user_id=OWNER,
        revision=snapshot.revision,
        epoch=snapshot.epoch,
        valid_until=snapshot.valid_until,
    )
    # An entry can lapse without any write happening, so revision equality
    # alone may never keep a cached snapshot alive.
    assert (
        service.snapshot_is_current(
            memory_db,
            owner_user_id=OWNER,
            revision=snapshot.revision,
            epoch=snapshot.epoch,
            valid_until=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        is False
    )

    # Turning memory off stops the next model call using it, without deleting
    # anything already saved.
    repo.update_settings(
        memory_db,
        owner_user_id=OWNER,
        expected_revision=repo.get_or_create_state(memory_db, OWNER).revision,
        use_enabled=False,
    )
    memory_db.commit()
    assert service.build_memory_snapshot(memory_db, owner_user_id=OWNER).entries == []
    assert (
        service.snapshot_is_current(
            memory_db,
            owner_user_id=OWNER,
            revision=snapshot.revision,
            epoch=snapshot.epoch,
            valid_until=None,
        )
        is False
    )
    assert len(repo.list_active_entries(memory_db, owner_user_id=OWNER)) == 2


def test_an_edited_source_message_stops_being_a_source(memory_db: Session) -> None:
    message_id = _seed_chat_message(memory_db, owner=OWNER, content="我叫李梅")
    turn = _register(memory_db, text_value="我叫李梅", chat_message_id=message_id)
    assert service.resolve_turn_source_text(memory_db, turn) == "我叫李梅"

    message = memory_db.get(ChatMessage, message_id)
    message.content = "我叫王芳"
    memory_db.commit()
    # Re-read and re-hashed rather than trusted from the cached turn row.
    assert service.resolve_turn_source_text(memory_db, turn) is None


# ---------------------------------------------------------------------------
# PostgreSQL migration contract (A03).
# ---------------------------------------------------------------------------


def _postgres_url() -> str | None:
    """A throwaway PostgreSQL to verify the migration against, if provided.

    Deployment runs on PostgreSQL while local development runs on SQLite, so a
    dialect mistake is invisible locally and fatal on deploy - a boolean
    ``DEFAULT 1`` is accepted by SQLite and rejected by PostgreSQL. This test
    closes that gap when a URL is available and skips honestly when it is not.
    """

    return os.environ.get("DOC_NEXUS_TEST_POSTGRES_URL") or None


@pytest.mark.skipif(
    _postgres_url() is None,
    reason="set DOC_NEXUS_TEST_POSTGRES_URL to cover the PostgreSQL dialect",
)
def test_migration_round_trip_on_postgresql() -> None:
    """Exercise pgvector and migrations only in a disposable test database.

    A schema-only search_path hides an already-installed public.vector type.
    A separate database keeps both the extension and all DDL isolated.
    """

    from sqlalchemy.engine import make_url

    name = f"mem_test_{new_token(6).lower().replace('-', '').replace('_', '')}"
    schema = "public"
    base_url = _postgres_url()
    admin = create_engine(base_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()

    scoped = make_url(base_url).set(database=name)
    original = settings.database_url
    settings.database_url = scoped.render_as_string(hide_password=False)
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    config.config_file_name = None
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "20260908_0087")
        command.upgrade(config, "head")

        engine = create_engine(settings.database_url, future=True)
        try:
            inspector = inspect(engine)
            tables = set(inspector.get_table_names(schema=schema))
            assert {
                "user_memory_states",
                "user_memory_turns",
                "user_memory_entries",
                "user_memory_evidence",
                "user_memory_jobs",
                "user_memory_operations",
            } <= tables

            # The non-partial composite UNIQUE the evidence keys point at.
            entry_uniques = {
                tuple(item["column_names"])
                for item in inspector.get_unique_constraints(
                    "user_memory_entries", schema=schema
                )
            }
            assert ("owner_user_id", "id") in entry_uniques

            # The partial index is a real partial index here, not a full one.
            active_index = next(
                item
                for item in inspector.get_indexes("user_memory_entries", schema=schema)
                if item["name"] == "uq_user_memory_entries_active_key"
            )
            assert active_index["unique"]
            with engine.begin() as connection:
                definition = connection.execute(
                    text(
                        "SELECT indexdef FROM pg_indexes "
                        "WHERE schemaname = :s AND indexname = :n"
                    ),
                    {"s": schema, "n": "uq_user_memory_entries_active_key"},
                ).scalar_one()
            assert "WHERE" in definition and "active" in definition

            # The boolean default really is a boolean, not the integer literal
            # SQLite would have accepted.
            with engine.begin() as connection:
                default = connection.execute(
                    text(
                        "SELECT column_default FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = "
                        "'user_memory_states' AND column_name = 'use_enabled'"
                    ),
                    {"s": schema},
                ).scalar_one()
            assert "true" in (default or "").lower()

            _assert_database_enforces_owner_binding(engine)
        finally:
            engine.dispose()
    finally:
        settings.database_url = original
        admin = create_engine(base_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{name}"'))
        finally:
            admin.dispose()
