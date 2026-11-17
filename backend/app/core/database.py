from __future__ import annotations

import logging
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models import Base

logger = logging.getLogger(__name__)

_SQLITE_BUSY_TIMEOUT_MS = 30_000

_engine: Engine | None = None
_session_local: sessionmaker[Session] | None = None
_engine_url: str | None = None


def _get_engine() -> Engine:
    global _engine, _session_local, _engine_url
    url = settings.database_url
    if url.startswith("sqlite"):
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    if _engine is None or _engine_url != url:
        if _engine is not None:
            _engine.dispose()
        kwargs = (
            {"connect_args": {"check_same_thread": False}}
            if url.startswith("sqlite")
            else {}
        )
        _engine = create_engine(url, future=True, pool_pre_ping=True, **kwargs)
        if url.startswith("sqlite"):
            event.listen(_engine, "connect", _configure_sqlite)
        _session_local = sessionmaker(
            bind=_engine, class_=Session, expire_on_commit=False, autoflush=False
        )
        _engine_url = url
    return _engine


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    """Enable WAL, busy timeout, and sqlite-vec on every SQLite connection."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()
    try:
        dbapi_connection.enable_load_extension(True)
        import sqlite_vec

        sqlite_vec.load(dbapi_connection)
        dbapi_connection.enable_load_extension(False)
    except Exception:
        logger.warning("sqlite-vec extension was not loaded; vector search is unavailable")


def SessionLocal() -> Session:
    _get_engine()
    assert _session_local is not None
    return _session_local()


# 事务持有时长告警线：SQLite 是库级写锁，一个长事务会挡住所有人。
# 超过这个秒数就留一条带调用栈的告警，下次排查不必再猜。
SLOW_TRANSACTION_SECONDS = 5.0


@contextmanager
def transaction():
    db = SessionLocal()
    started = time.monotonic()
    stack = ""
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        elapsed = time.monotonic() - started
        if elapsed >= SLOW_TRANSACTION_SECONDS:
            stack = stack or "".join(traceback.format_stack()[-8:])
            logger.warning(
                "slow transaction held the write lock for %.1fs\n%s",
                elapsed,
                stack,
            )
        db.close()


def initialize_database() -> None:
    engine = _get_engine()
    Base.metadata.create_all(bind=engine)
    _ensure_temporary_document_space_id_nullable(engine)
    _ensure_user_email_nullable(engine)
    _ensure_document_chat_session_id(engine)
    if settings.database_url.startswith("sqlite"):
        _ensure_sqlite_document_embeddings(engine)
        _ensure_sqlite_document_version_columns(engine)
        _ensure_sqlite_temporary_document_columns(engine)
        _ensure_sqlite_writing_operation_columns(engine)
        _ensure_sqlite_writing_project_columns(engine)
        _ensure_sqlite_writing_chapter_columns(engine)
        _ensure_sqlite_writing_publication_columns(engine)
        _ensure_sqlite_artifact_columns(engine)
        _ensure_sqlite_agent_event_payload(engine)
        _ensure_sqlite_model_price_columns(engine)
        _ensure_sqlite_chat_message_columns(engine)
        _ensure_sqlite_chat_session_columns(engine)
        _ensure_sqlite_citation_columns(engine)
        _ensure_sqlite_model_endpoint_columns(engine)
        _ensure_sqlite_auth_version_column(engine)
        _ensure_sqlite_user_api_key_column(engine)
        _ensure_sqlite_user_api_keys_table(engine)
        _ensure_sqlite_group_todo_columns(engine)
        _ensure_sqlite_assistant_event_columns(engine)
        _ensure_sqlite_factory_scope_columns(engine)
    _load_rule_threshold_overrides()
    _load_voice_config_overrides()


def _load_voice_config_overrides() -> None:
    """界面改过的语音配置在启动时也要生效，否则重启后回到 .env 的旧值。"""
    from app.services import voice_config

    try:
        with SessionLocal() as db:
            voice_config.apply_overrides(db)
    except Exception:  # noqa: BLE001 - 配置读不出来不该阻止启动
        pass


def _load_rule_threshold_overrides() -> None:
    """把库里的阈值覆盖值装进进程缓存。

    放在初始化末尾：规则函数是纯函数、拿不到 db，只能靠进程缓存；
    进程启动时读一次，之后每次改值都会同步更新缓存。
    """
    from app.services import rule_thresholds

    try:
        with SessionLocal() as db:
            count = rule_thresholds.load_overrides(db)
        if count:
            logger.info("rule threshold overrides loaded: %s", count)
    except Exception:  # noqa: BLE001 - 读不到就用默认值，不能挡住启动
        logger.warning("rule threshold overrides not loaded", exc_info=True)


def _ensure_user_email_nullable(engine: Engine) -> None:
    """Allow creating users without an email address."""
    from sqlalchemy import String, inspect, text

    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    columns = {item["name"]: item for item in inspector.get_columns("users")}
    email_col = columns.get("email")
    if not email_col or email_col.get("nullable"):
        return

    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "postgresql":
            conn.execute(text("ALTER TABLE users ALTER COLUMN email DROP NOT NULL"))
        elif dialect == "sqlite":
            from alembic.migration import MigrationContext
            from alembic.operations import Operations

            ctx = MigrationContext.configure(conn)
            ops = Operations(ctx)
            with ops.batch_alter_table("users") as batch:
                batch.alter_column(
                    "email",
                    existing_type=String(320),
                    nullable=True,
                )


def _ensure_temporary_document_space_id_nullable(engine: Engine) -> None:
    """Allow temporary chat attachments without a knowledge-space binding."""
    from sqlalchemy import String, inspect, text

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    targets = [name for name in ("documents", "document_jobs") if name in tables]
    if not targets:
        return

    dialect = engine.dialect.name
    with engine.begin() as conn:
        for table_name in targets:
            columns = {item["name"]: item for item in inspector.get_columns(table_name)}
            space_col = columns.get("space_id")
            if not space_col or space_col.get("nullable"):
                continue
            if dialect == "postgresql":
                conn.execute(
                    text(f"ALTER TABLE {table_name} ALTER COLUMN space_id DROP NOT NULL")
                )
            elif dialect == "sqlite":
                # SQLite cannot DROP NOT NULL in-place; recreate via batch alter.
                from alembic.migration import MigrationContext
                from alembic.operations import Operations

                ctx = MigrationContext.configure(conn)
                ops = Operations(ctx)
                with ops.batch_alter_table(table_name) as batch:
                    batch.alter_column(
                        "space_id",
                        existing_type=String(64),
                        nullable=True,
                    )


def _ensure_document_chat_session_id(engine: Engine) -> None:
    """Bind temporary chat attachments to a chat session when missing."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "documents" not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns("documents")}
    indexes = {item["name"] for item in inspector.get_indexes("documents")}
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if "chat_session_id" not in columns:
            if dialect == "postgresql":
                conn.execute(
                    text(
                        "ALTER TABLE documents ADD COLUMN chat_session_id "
                        "VARCHAR(64) NULL REFERENCES chat_sessions(id) "
                        "ON DELETE SET NULL"
                    )
                )
            else:
                conn.execute(
                    text(
                        "ALTER TABLE documents ADD COLUMN chat_session_id "
                        "VARCHAR(64)"
                    )
                )
        if "ix_documents_chat_session_id" not in indexes:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_documents_chat_session_id "
                    "ON documents (chat_session_id)"
                )
            )
        if "ix_documents_temporary_chat_session" not in indexes:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_documents_temporary_chat_session "
                    "ON documents (is_temporary, chat_session_id)"
                )
            )


def _ensure_sqlite_model_endpoint_columns(engine: Engine) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(model_endpoints)")).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        if "adapter" not in existing:
            conn.execute(
                text(
                    "ALTER TABLE model_endpoints "
                    "ADD COLUMN adapter VARCHAR(64) NOT NULL DEFAULT "
                    "'openai_compatible'"
                )
            )
            conn.execute(
                text(
                    "UPDATE model_endpoints SET adapter = CASE "
                    "WHEN replace(lower(provider), '-', '_') IN ('openai', 'open_ai') "
                    "THEN 'openai' "
                    "WHEN replace(lower(provider), '-', '_') LIKE '%azure%' "
                    "THEN 'azure_openai' ELSE 'openai_compatible' END"
                )
            )
        if "api_protocol" not in existing:
            conn.execute(
                text(
                    "ALTER TABLE model_endpoints "
                    "ADD COLUMN api_protocol VARCHAR(32) NOT NULL DEFAULT "
                    "'chat_completions'"
                )
            )
            conn.execute(
                text(
                    "UPDATE model_endpoints SET api_protocol = CASE "
                    "WHEN adapter IN ('openai', 'azure_openai') "
                    "OR replace(lower(provider), '-', '_') IN "
                    "('openai_responses', 'openai_compatible_responses') "
                    "THEN 'responses' ELSE 'chat_completions' END"
                )
            )


def _ensure_sqlite_auth_version_column(engine: Engine) -> None:
    """Add the token invalidation version to existing SQLite databases."""
    from sqlalchemy import text

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(users)")).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        if "auth_version" not in existing:
            conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN auth_version "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            )


def _ensure_sqlite_user_api_key_column(engine: Engine) -> None:
    """Add and populate plaintext user API keys in local SQLite databases."""
    from sqlalchemy import text

    from app.core.security import new_api_key

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(users)")).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        if "api_key" not in existing:
            conn.execute(
                text("ALTER TABLE users ADD COLUMN api_key VARCHAR(128)")
            )
        missing = conn.execute(
            text("SELECT id FROM users WHERE api_key IS NULL OR api_key = ''")
        ).fetchall()
        for row in missing:
            conn.execute(
                text("UPDATE users SET api_key = :api_key WHERE id = :user_id"),
                {"api_key": new_api_key(), "user_id": row[0]},
            )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_api_key "
                "ON users (api_key)"
            )
        )


def _ensure_sqlite_user_api_keys_table(engine: Engine) -> None:
    from sqlalchemy import text
    from app.core.security import new_api_key, now

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_api_keys (
                id VARCHAR(64) PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                api_key VARCHAR(128) NOT NULL UNIQUE,
                status VARCHAR(32) NOT NULL DEFAULT 'active',
                created_at DATETIME NOT NULL
            )
        """))
        existing = conn.execute(text("SELECT user_id FROM user_api_keys")).fetchall()
        known = {row[0] for row in existing}
        users = conn.execute(text("SELECT id, api_key, created_at FROM users WHERE api_key IS NOT NULL AND api_key != ''")).fetchall()
        for user_id, api_key, created_at in users:
            if user_id not in known:
                conn.execute(text("INSERT INTO user_api_keys (id,user_id,api_key,status,created_at) VALUES (:id,:user_id,:api_key,'active',:created_at)"), {"id": f"uak_{new_api_key()[4:14]}", "user_id": user_id, "api_key": api_key, "created_at": created_at or now()})
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_api_keys_user_id ON user_api_keys (user_id)"))


def _ensure_sqlite_chat_message_columns(engine: Engine) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(chat_messages)")).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        if "session_references_json" not in existing:
            conn.execute(
                text(
                    "ALTER TABLE chat_messages "
                    "ADD COLUMN session_references_json TEXT NOT NULL DEFAULT '[]'"
                )
            )
        if "retrieval_trace_json" not in existing:
            conn.execute(
                text(
                    "ALTER TABLE chat_messages "
                    "ADD COLUMN retrieval_trace_json TEXT"
                )
            )
        if "writing_context_json" not in existing:
            conn.execute(
                text("ALTER TABLE chat_messages ADD COLUMN writing_context_json TEXT")
            )


def _ensure_sqlite_chat_session_columns(engine: Engine) -> None:
    """Add chat-session columns introduced after an existing SQLite database boot."""
    from sqlalchemy import text

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(chat_sessions)")).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        if "messages_revision" not in existing:
            conn.execute(
                text(
                    "ALTER TABLE chat_sessions "
                    "ADD COLUMN messages_revision INTEGER NOT NULL DEFAULT 0"
                )
            )
        if "title_finalized" not in existing:
            conn.execute(
                text(
                    "ALTER TABLE chat_sessions "
                    "ADD COLUMN title_finalized BOOLEAN NOT NULL DEFAULT 0"
                )
            )
            conn.execute(text("UPDATE chat_sessions SET title_finalized = 1"))


def _ensure_sqlite_citation_columns(engine: Engine) -> None:
    """Add nullable page evidence fields to databases created before 0066."""
    from sqlalchemy import text

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(citations)")).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        if "page_number" not in existing:
            conn.execute(text("ALTER TABLE citations ADD COLUMN page_number INTEGER"))
        if "page_block_id" not in existing:
            conn.execute(text("ALTER TABLE citations ADD COLUMN page_block_id VARCHAR(64)"))
        if "bbox_json" not in existing:
            conn.execute(text("ALTER TABLE citations ADD COLUMN bbox_json TEXT"))


def _ensure_sqlite_model_price_columns(engine: Engine) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(model_prices)")).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        alters = []
        if "cache_read_per_million" not in existing:
            alters.append(
                "ALTER TABLE model_prices ADD COLUMN cache_read_per_million VARCHAR(32)"
            )
        if "cache_write_per_million" not in existing:
            alters.append(
                "ALTER TABLE model_prices ADD COLUMN cache_write_per_million VARCHAR(32)"
            )
        for stmt in alters:
            conn.execute(text(stmt))
        conn.execute(
            text(
                "UPDATE model_prices SET cache_read_per_million = input_per_million "
                "WHERE cache_read_per_million IS NULL OR cache_read_per_million = ''"
            )
        )
        conn.execute(
            text(
                "UPDATE model_prices SET cache_write_per_million = input_per_million "
                "WHERE cache_write_per_million IS NULL OR cache_write_per_million = ''"
            )
        )


def _ensure_sqlite_document_embeddings(engine: Engine) -> None:
    """Create the knowledge-chunk table used by sqlite-vec cosine search."""
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS document_embeddings (
                    id VARCHAR(128) PRIMARY KEY,
                    category VARCHAR(128) NOT NULL,
                    file_id VARCHAR(128) NOT NULL,
                    document TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    metadata JSON NOT NULL DEFAULT '{}',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_document_embeddings_category_file "
                "ON document_embeddings (category, file_id)"
            )
        )


def _ensure_sqlite_document_version_columns(engine: Engine) -> None:
    """create_all does not alter existing tables; add columns introduced after first boot."""
    from sqlalchemy import text

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(document_versions)")).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        alters = []
        if "file_path" not in existing:
            alters.append("ALTER TABLE document_versions ADD COLUMN file_path TEXT")
        if "ext_task_id" not in existing:
            alters.append(
                "ALTER TABLE document_versions ADD COLUMN ext_task_id VARCHAR(128)"
            )
        if "chunk_count" not in existing:
            alters.append(
                "ALTER TABLE document_versions ADD COLUMN chunk_count INTEGER NOT NULL DEFAULT 0"
            )
        if "summary" not in existing:
            alters.append("ALTER TABLE document_versions ADD COLUMN summary TEXT")
        if "extraction_method" not in existing:
            alters.append("ALTER TABLE document_versions ADD COLUMN extraction_method VARCHAR(16)")
        if "page_count" not in existing:
            alters.append("ALTER TABLE document_versions ADD COLUMN page_count INTEGER NOT NULL DEFAULT 0")
        if "ocr_page_count" not in existing:
            alters.append("ALTER TABLE document_versions ADD COLUMN ocr_page_count INTEGER NOT NULL DEFAULT 0")
        if "vision_page_count" not in existing:
            alters.append("ALTER TABLE document_versions ADD COLUMN vision_page_count INTEGER NOT NULL DEFAULT 0")
        if "vision_usage_json" not in existing:
            alters.append("ALTER TABLE document_versions ADD COLUMN vision_usage_json TEXT NOT NULL DEFAULT '{}'")
        if "extraction_warning_code" not in existing:
            alters.append("ALTER TABLE document_versions ADD COLUMN extraction_warning_code VARCHAR(64)")
        for stmt in alters:
            conn.execute(text(stmt))


def _ensure_sqlite_temporary_document_columns(engine: Engine) -> None:
    """Add temporary-attachment columns to existing SQLite databases."""
    from sqlalchemy import text

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(documents)")).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        alters = []
        if "is_temporary" not in existing:
            alters.append(
                "ALTER TABLE documents ADD COLUMN is_temporary "
                "BOOLEAN NOT NULL DEFAULT 0"
            )
        if "expires_at" not in existing:
            alters.append("ALTER TABLE documents ADD COLUMN expires_at DATETIME")
        if "temporary_cleanup_at" not in existing:
            alters.append(
                "ALTER TABLE documents ADD COLUMN temporary_cleanup_at DATETIME"
            )
        if "chat_session_id" not in existing:
            alters.append(
                "ALTER TABLE documents ADD COLUMN chat_session_id VARCHAR(64)"
            )
        for stmt in alters:
            conn.execute(text(stmt))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_documents_temporary_expires_at "
                "ON documents (is_temporary, expires_at, temporary_cleanup_at)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_documents_chat_session_id "
                "ON documents (chat_session_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_documents_temporary_chat_session "
                "ON documents (is_temporary, chat_session_id)"
            )
        )


_WRITING_OPERATION_STATUS_CHECK = (
    "status in ('queued','dispatching','running','completed','failed',"
    "'cancelling','cancelled','stopped')"
)


def _ensure_sqlite_writing_operation_columns(engine: Engine) -> None:
    """Repair writing_operations when an old SQLite file was stamped without column upgrades."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(writing_operations)")).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        alters = []
        if "idempotency_key" not in existing:
            alters.append(
                "ALTER TABLE writing_operations ADD COLUMN idempotency_key VARCHAR(256)"
            )
        if "attempt" not in existing:
            alters.append(
                "ALTER TABLE writing_operations ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0"
            )
        if "input_revision" not in existing:
            alters.append(
                "ALTER TABLE writing_operations ADD COLUMN input_revision INTEGER"
            )
        if "error_code" not in existing:
            alters.append(
                "ALTER TABLE writing_operations ADD COLUMN error_code VARCHAR(64)"
            )
        if "error_message_safe" not in existing:
            alters.append(
                "ALTER TABLE writing_operations "
                "ADD COLUMN error_message_safe VARCHAR(500)"
            )
        if "last_event_cursor" not in existing:
            alters.append(
                "ALTER TABLE writing_operations "
                "ADD COLUMN last_event_cursor INTEGER NOT NULL DEFAULT 0"
            )
        if "started_at" not in existing:
            alters.append(
                "ALTER TABLE writing_operations ADD COLUMN started_at DATETIME"
            )
        if "input_payload" not in existing:
            alters.append(
                "ALTER TABLE writing_operations "
                "ADD COLUMN input_payload TEXT NOT NULL DEFAULT '{}'"
            )
        for stmt in alters:
            conn.execute(text(stmt))
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_writing_operation_idempotency "
                "ON writing_operations (project_id, idempotency_key)"
            )
        )

        table_sql = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='writing_operations'"
            )
        ).scalar()
        if table_sql and "dispatching" not in table_sql:
            ctx = MigrationContext.configure(conn)
            op = Operations(ctx)
            with op.batch_alter_table("writing_operations") as batch:
                batch.drop_constraint("ck_writing_operations_status", type_="check")
                batch.create_check_constraint(
                    "ck_writing_operations_status",
                    _WRITING_OPERATION_STATUS_CHECK,
                )


def _ensure_sqlite_writing_chapter_columns(engine: Engine) -> None:
    """Concurrency columns for databases created before multi-editor support."""
    from sqlalchemy import text

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(writing_chapters)")).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        alters = []
        if "revision" not in existing:
            alters.append(
                "ALTER TABLE writing_chapters "
                "ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
            )
        if "last_editor_user_id" not in existing:
            alters.append(
                "ALTER TABLE writing_chapters "
                "ADD COLUMN last_editor_user_id VARCHAR(64)"
            )
        if "edit_lock_user_id" not in existing:
            alters.append(
                "ALTER TABLE writing_chapters ADD COLUMN edit_lock_user_id VARCHAR(64)"
            )
        if "edit_lock_expires_at" not in existing:
            alters.append(
                "ALTER TABLE writing_chapters ADD COLUMN edit_lock_expires_at DATETIME"
            )
        for stmt in alters:
            conn.execute(text(stmt))


def _ensure_sqlite_writing_publication_columns(engine: Engine) -> None:
    """Frozen citation set for databases created before it existed.

    Added nullable with no default: NULL keeps meaning "never recorded" for
    pre-existing publications, which must stay distinguishable from a recorded
    empty set. Existing rows are not backfilled.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        rows = conn.execute(
            text("PRAGMA table_info(writing_project_publications)")
        ).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        if "citation_refs_json" not in existing:
            conn.execute(
                text(
                    "ALTER TABLE writing_project_publications "
                    "ADD COLUMN citation_refs_json TEXT"
                )
            )


def _ensure_sqlite_writing_project_columns(engine: Engine) -> None:
    from sqlalchemy import inspect, text

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(writing_projects)")).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        alters = []
        if "pi_agent_session_id" not in existing:
            alters.append(
                "ALTER TABLE writing_projects "
                "ADD COLUMN pi_agent_session_id VARCHAR(128)"
            )
        if "pi_lease_owner" not in existing:
            alters.append(
                "ALTER TABLE writing_projects ADD COLUMN pi_lease_owner VARCHAR(128)"
            )
        if "pi_lease_operation_id" not in existing:
            alters.append(
                "ALTER TABLE writing_projects "
                "ADD COLUMN pi_lease_operation_id VARCHAR(64)"
            )
        if "pi_lease_expires_at" not in existing:
            alters.append(
                "ALTER TABLE writing_projects ADD COLUMN pi_lease_expires_at DATETIME"
            )
        if "brief_json" not in existing:
            alters.append(
                "ALTER TABLE writing_projects "
                "ADD COLUMN brief_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "brief_revision" not in existing:
            alters.append(
                "ALTER TABLE writing_projects "
                "ADD COLUMN brief_revision INTEGER NOT NULL DEFAULT 1"
            )
        if "pi_session_stage" not in existing:
            alters.append(
                "ALTER TABLE writing_projects ADD COLUMN pi_session_stage VARCHAR(32)"
            )
        if "selected_document_ids" not in existing:
            alters.append(
                "ALTER TABLE writing_projects "
                "ADD COLUMN selected_document_ids TEXT NOT NULL DEFAULT '[]'"
            )
        if "source_chat_session_id" not in existing:
            alters.append(
                "ALTER TABLE writing_projects "
                "ADD COLUMN source_chat_session_id VARCHAR(64)"
            )
        if "source_chat_assistant_message_id" not in existing:
            alters.append(
                "ALTER TABLE writing_projects "
                "ADD COLUMN source_chat_assistant_message_id VARCHAR(64)"
            )
        # Collaboration: review lifecycle and issued-document identity.
        if "doc_status" not in existing:
            alters.append(
                "ALTER TABLE writing_projects "
                "ADD COLUMN doc_status VARCHAR(16) NOT NULL DEFAULT 'draft'"
            )
        if "review_required" not in existing:
            alters.append(
                "ALTER TABLE writing_projects "
                "ADD COLUMN review_required BOOLEAN NOT NULL DEFAULT 0"
            )
        if "approved_review_id" not in existing:
            alters.append(
                "ALTER TABLE writing_projects "
                "ADD COLUMN approved_review_id VARCHAR(64)"
            )
        if "approved_content_hash" not in existing:
            alters.append(
                "ALTER TABLE writing_projects "
                "ADD COLUMN approved_content_hash VARCHAR(64)"
            )
        if "doc_number" not in existing:
            alters.append(
                "ALTER TABLE writing_projects ADD COLUMN doc_number VARCHAR(64)"
            )
        if "issued_at" not in existing:
            alters.append(
                "ALTER TABLE writing_projects ADD COLUMN issued_at DATETIME"
            )
        if "valid_until" not in existing:
            alters.append(
                "ALTER TABLE writing_projects ADD COLUMN valid_until DATETIME"
            )
        for stmt in alters:
            conn.execute(text(stmt))

        target_unique_signature = (
            "owner_user_id",
            "source_chat_session_id",
            "source_chat_assistant_message_id",
        )
        inspector = inspect(conn)
        unique_constraint_signatures = {
            tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints("writing_projects")
        }
        indexes = inspector.get_indexes("writing_projects")
        redundant_named_index = next(
            (
                item
                for item in indexes
                if item.get("name") == "uq_writing_projects_chat_auto_export"
                and item.get("unique")
                and tuple(item.get("column_names") or ())
                == target_unique_signature
            ),
            None,
        )
        if (
            target_unique_signature in unique_constraint_signatures
            and redundant_named_index is not None
        ):
            conn.execute(text('DROP INDEX "uq_writing_projects_chat_auto_export"'))
            indexes.remove(redundant_named_index)

        unique_index_signatures = {
            tuple(item.get("column_names") or ())
            for item in indexes
            if item.get("unique")
        }
        if target_unique_signature not in (
            unique_constraint_signatures | unique_index_signatures
        ):
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX uq_writing_projects_chat_auto_export "
                    "ON writing_projects "
                    "(owner_user_id, source_chat_session_id, "
                    "source_chat_assistant_message_id)"
                )
            )

        index_signatures = {
            tuple(item.get("column_names") or ()) for item in indexes
        }
        if ("source_chat_session_id",) not in index_signatures:
            conn.execute(
                text(
                    "CREATE INDEX ix_writing_projects_source_chat_session_id "
                    "ON writing_projects (source_chat_session_id)"
                )
            )


def _ensure_sqlite_artifact_columns(engine: Engine) -> None:
    """Repair artifacts snapshot columns when an old SQLite file was stamped without 0009."""
    from sqlalchemy import text

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(artifacts)")).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        alters = []
        if "content" not in existing:
            alters.append("ALTER TABLE artifacts ADD COLUMN content BLOB")
        if "checksum_sha256" not in existing:
            alters.append(
                "ALTER TABLE artifacts ADD COLUMN checksum_sha256 VARCHAR(64)"
            )
        if "content_type" not in existing:
            alters.append("ALTER TABLE artifacts ADD COLUMN content_type VARCHAR(255)")
        if "content_size" not in existing:
            alters.append("ALTER TABLE artifacts ADD COLUMN content_size INTEGER")
        if "source_version_ids" not in existing:
            alters.append("ALTER TABLE artifacts ADD COLUMN source_version_ids TEXT")
        if "manifest_json" not in existing:
            alters.append(
                "ALTER TABLE artifacts ADD COLUMN manifest_json TEXT NOT NULL DEFAULT '{}'"
            )
        for stmt in alters:
            conn.execute(text(stmt))


def _ensure_sqlite_agent_event_payload(engine: Engine) -> None:
    """Repair agent_event_receipts.payload_json on old SQLite databases."""
    from sqlalchemy import text

    with engine.begin() as conn:
        rows = conn.execute(
            text("PRAGMA table_info(agent_event_receipts)")
        ).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        if "payload_json" not in existing:
            conn.execute(
                text(
                    "ALTER TABLE agent_event_receipts "
                    "ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'"
                )
            )


def reset_database(path: Path) -> None:
    global _engine, _session_local, _engine_url
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_local = None
    _engine_url = None
    if path.exists():
        path.unlink()


def _ensure_sqlite_group_todo_columns(engine: Engine) -> None:
    """待办表新增列；create_all 不会改已有表。"""
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(group_todos)"))}
        if "assistant_event_ids_json" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE group_todos ADD COLUMN assistant_event_ids_json "
                    "TEXT NOT NULL DEFAULT '[]'"
                )
            )
        # 关联单据（跳转链接）
        if "refs_json" not in columns:
            conn.execute(
                text("ALTER TABLE group_todos ADD COLUMN refs_json TEXT NOT NULL DEFAULT '[]'")
            )
        # 主题键（同主题合并）
        if "topic" not in columns:
            conn.execute(
                text("ALTER TABLE group_todos ADD COLUMN topic VARCHAR(160) NOT NULL DEFAULT ''")
            )
        # 催办痕迹（状态回流）
        if "last_action_at" not in columns:
            conn.execute(text("ALTER TABLE group_todos ADD COLUMN last_action_at DATETIME"))
        if "nudged_at" not in columns:
            conn.execute(text("ALTER TABLE group_todos ADD COLUMN nudged_at DATETIME"))
        if "nudge_count" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE group_todos ADD COLUMN nudge_count "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            )
        if "escalated_at" not in columns:
            conn.execute(text("ALTER TABLE group_todos ADD COLUMN escalated_at DATETIME"))


def _ensure_sqlite_assistant_event_columns(engine: Engine) -> None:
    """主动监控事件表新增列；create_all 不会改已有表。"""
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(assistant_events)"))}
        additions = {
            "reminders": "INTEGER NOT NULL DEFAULT 0",
            "last_notified_at": "DATETIME",
            "first_seen_at": "DATETIME",
            "original_severity": "VARCHAR(16) NOT NULL DEFAULT 'info'",
            "note": "TEXT NOT NULL DEFAULT ''",
            "archived_at": "DATETIME",
            # 复发：同一件事关掉又回来，计一次，别当新问题重新报一遍
            "recurrence": "INTEGER NOT NULL DEFAULT 0",
            "last_reopened_at": "DATETIME",
        }
        for name, ddl in additions.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE assistant_events ADD COLUMN {name} {ddl}"))
        conn.execute(
            text("UPDATE assistant_events SET first_seen_at = created_at WHERE first_seen_at IS NULL")
        )

def _ensure_sqlite_factory_scope_columns(engine: Engine) -> None:
    """账套隔离：待办与主动发现加 factory_code。

    MES 是账套权威源，AI 只镜像。存量数据归默认账套 F01，
    与 MES 侧 V8 迁移的回填口径一致——否则历史待办会在切账套后消失。
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in ("group_todos", "assistant_events"):
            if table not in tables:
                continue
            columns = {item["name"] for item in inspector.get_columns(table)}
            if "factory_code" not in columns:
                conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN factory_code VARCHAR(20)")
                )
            conn.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_factory "
                    f"ON {table} (factory_code)"
                )
            )
            conn.execute(
                text(
                    f"UPDATE {table} SET factory_code = :code "
                    f"WHERE factory_code IS NULL OR factory_code = ''"
                ),
                {"code": "F01"},
            )
