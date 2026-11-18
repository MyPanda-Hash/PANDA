import warnings
from pathlib import Path

import pytest

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.core.config import settings
from app.core.database import (
    _ensure_sqlite_writing_project_columns,
    _ensure_sqlite_writing_publication_columns,
)
from app.models import Base


ROOT = Path(__file__).parents[2]


def test_alembic_revision_graph_has_unique_revisions_and_single_head():
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        revisions = ScriptDirectory.from_config(config)
        heads = revisions.get_heads()
        list(revisions.walk_revisions())

    duplicate_warnings = [
        str(item.message)
        for item in captured
        if "present more than once" in str(item.message)
    ]
    assert duplicate_warnings == []
    assert len(heads) == 1


def test_repair_migration_downgrade_preserves_historical_schema_and_data(
    tmp_path: Path,
):
    database_path = tmp_path / "writing-migrations.sqlite3"
    old_database_url = settings.database_url
    settings.database_url = f"sqlite:///{database_path}"
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    try:
        command.upgrade(config, "head")
        engine = create_engine(settings.database_url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "insert into system_config "
                    "(key, value, description, updated_at) "
                    "values ('sentinel', 'preserved', '', CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "insert into writing_operations "
                    "(id, project_id, kind, status, usage, result, created_at, "
                    "attempt, last_event_cursor, input_payload) values "
                    "('sentinel-op', 'missing-project', 'outline', 'completed', "
                    "'{}', :result, CURRENT_TIMESTAMP, 0, 0, '{}')"
                ),
                {"result": '{"sentinel":true}'},
            )
        engine.dispose()

        command.downgrade(config, "20260731_0019")

        engine = create_engine(settings.database_url)
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert "system_config" in inspector.get_table_names()
            document_version_columns = {
                item["name"] for item in inspector.get_columns("document_versions")
            }
            writing_operation_columns = {
                item["name"] for item in inspector.get_columns("writing_operations")
            }
            assert {"file_path", "ext_task_id", "chunk_count"} <= (
                document_version_columns
            )
            assert "result" in writing_operation_columns
            assert (
                connection.execute(
                    text("select value from system_config where key='sentinel'")
                ).scalar_one()
                == "preserved"
            )
            assert (
                connection.execute(
                    text("select result from writing_operations where id='sentinel-op'")
                ).scalar_one()
                == '{"sentinel":true}'
            )
        engine.dispose()
    finally:
        settings.database_url = old_database_url


def test_chat_auto_docx_identity_repair_handles_stamped_revision_collision(
    tmp_path: Path,
):
    database_path = tmp_path / "chat-auto-docx-repair.sqlite3"
    old_database_url = settings.database_url
    settings.database_url = f"sqlite:///{database_path}"
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    try:
        command.upgrade(config, "20260904_0081")
        engine = create_engine(settings.database_url)
        with engine.begin() as connection:
            columns = {
                item["name"]
                for item in inspect(connection).get_columns("writing_projects")
            }
            assert "source_chat_session_id" not in columns
            assert "source_chat_assistant_message_id" not in columns
            connection.execute(
                text(
                    "INSERT INTO writing_projects "
                    "(id, owner_user_id, title, selected_space_ids, "
                    "selected_document_ids, brief_json, brief_revision, "
                    "created_at, updated_at) VALUES "
                    "('wpr_sentinel', 'usr_sentinel', 'Preserved project', "
                    "'[]', '[]', '{}', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
        engine.dispose()

        # A historical worktree used the same 0082 revision ID for a different
        # migration, so affected databases reached 0084 without these columns.
        command.stamp(config, "20260904_0084")
        command.upgrade(config, "head")

        engine = create_engine(settings.database_url)
        with engine.connect() as connection:
            inspector = inspect(connection)
            columns = {
                item["name"] for item in inspector.get_columns("writing_projects")
            }
            constraint_names = {
                item["name"]
                for item in inspector.get_unique_constraints("writing_projects")
            }
            index_names = {
                item["name"] for item in inspector.get_indexes("writing_projects")
            }
            assert {
                "source_chat_session_id",
                "source_chat_assistant_message_id",
            } <= columns
            assert "uq_writing_projects_chat_auto_export" in constraint_names
            assert "ix_writing_projects_source_chat_session_id" in index_names
            assert (
                connection.execute(
                    text("SELECT title FROM writing_projects WHERE id = 'wpr_sentinel'")
                ).scalar_one()
                == "Preserved project"
            )
            # Assert the repair reached the current head rather than a
            # hard-coded revision: pinning the string here means every later
            # migration breaks this test for a reason unrelated to the repair.
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ScriptDirectory.from_config(config).get_current_head()
        engine.dispose()
    finally:
        settings.database_url = old_database_url


def test_sqlite_writing_project_repair_deduplicates_equivalent_unique_index(
    tmp_path: Path,
):
    database_path = tmp_path / "writing-project-indexes.sqlite3"
    old_database_url = settings.database_url
    settings.database_url = f"sqlite:///{database_path}"
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    try:
        command.upgrade(config, "head")
        engine = create_engine(settings.database_url)
        Base.metadata.create_all(bind=engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_writing_projects_chat_auto_export "
                    "ON writing_projects "
                    "(owner_user_id, source_chat_session_id, "
                    "source_chat_assistant_message_id)"
                )
            )

        _ensure_sqlite_writing_project_columns(engine)
        Base.metadata.create_all(bind=engine)
        _ensure_sqlite_writing_project_columns(engine)

        with engine.connect() as connection:
            matching_indexes = []
            for row in connection.execute(
                text("PRAGMA index_list('writing_projects')")
            ).fetchall():
                index_columns = tuple(
                    item[2]
                    for item in connection.execute(
                        text(f'PRAGMA index_info("{row[1]}")')
                    ).fetchall()
                )
                if index_columns == (
                    "owner_user_id",
                    "source_chat_session_id",
                    "source_chat_assistant_message_id",
                ):
                    matching_indexes.append(
                        {"name": row[1], "unique": row[2], "origin": row[3]}
                    )

            assert len(matching_indexes) == 1
            assert matching_indexes[0]["unique"] == 1
            assert matching_indexes[0]["origin"] == "u"
        engine.dispose()
    finally:
        settings.database_url = old_database_url


def test_sqlite_writing_publication_repair_adds_frozen_citation_column(
    tmp_path: Path,
):
    """Legacy publications keep NULL: "never recorded" must not read as "empty"."""
    database_path = tmp_path / "writing-publication-columns.sqlite3"
    old_database_url = settings.database_url
    settings.database_url = f"sqlite:///{database_path}"
    try:
        engine = create_engine(settings.database_url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE writing_project_publications (id TEXT PRIMARY KEY)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO writing_project_publications (id) "
                    "VALUES ('wpp_legacy')"
                )
            )

        _ensure_sqlite_writing_publication_columns(engine)
        _ensure_sqlite_writing_publication_columns(engine)

        with engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    text("PRAGMA table_info(writing_project_publications)")
                ).fetchall()
            }
            assert "citation_refs_json" in columns
            assert (
                connection.execute(
                    text(
                        "SELECT citation_refs_json FROM writing_project_publications "
                        "WHERE id = 'wpp_legacy'"
                    )
                ).scalar_one()
                is None
            )
        engine.dispose()
    finally:
        settings.database_url = old_database_url


def test_legacy_artifact_link_does_not_become_writing_context(tmp_path: Path):
    from app.core.database import _ensure_sqlite_chat_message_columns

    engine = create_engine(f"sqlite:///{tmp_path / 'chat-context.sqlite3'}")
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE chat_messages "
                "(id TEXT PRIMARY KEY, content TEXT, writing_project_id TEXT)"
            ))
            connection.execute(text(
                "INSERT INTO chat_messages VALUES "
                "('legacy', 'Keep original reply', 'artifact-project')"
            ))
        _ensure_sqlite_chat_message_columns(engine)
        _ensure_sqlite_chat_message_columns(engine)
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT content, writing_project_id, writing_context_json "
                "FROM chat_messages WHERE id = 'legacy'"
            )).one()
        assert tuple(row) == ("Keep original reply", "artifact-project", None)
    finally:
        engine.dispose()


@pytest.mark.parametrize("legacy_orphan", [False, True])
def test_template_deleted_status_migration_preserves_referenced_rows_and_refuses_restore(
    tmp_path: Path, legacy_orphan: bool,
):
    import sqlite3
    from datetime import datetime, timezone

    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    from app.models.auth import User
    from app.models.writing import (
        WritingDocTemplate,
        WritingDocTemplateRevision,
        WritingProject,
    )

    database_path = tmp_path / "template-deleted-status.sqlite3"
    old_database_url = settings.database_url
    settings.database_url = f"sqlite:///{database_path}"
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    engine = None

    def enable_foreign_keys(connection, _record):
        if isinstance(connection, sqlite3.Connection):
            connection.execute("PRAGMA foreign_keys=ON")

    def snapshot(connection):
        inspector = inspect(connection)
        return {
            table: {
                "rows": connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).fetchall(),
                "indexes": sorted(inspector.get_indexes(table), key=lambda item: item["name"]),
                "unique": sorted(
                    inspector.get_unique_constraints(table), key=lambda item: item["name"] or ""
                ),
                "foreign_keys": sorted(
                    inspector.get_foreign_keys(table),
                    key=lambda item: tuple(item["constrained_columns"]),
                ),
            }
            for table in (
                "writing_doc_templates",
                "writing_doc_template_revisions",
                "writing_projects",
                "writing_operations",
            )
        }

    try:
        command.upgrade(config, "20260929_0100")
        if legacy_orphan:
            # 无关历史孤儿行不得阻止模板约束升级，也不得借迁移修复或删除。
            with sqlite3.connect(database_path) as legacy:
                legacy.execute(
                    "INSERT INTO writing_operations "
                    "(id, project_id, kind, status, usage, result, created_at, "
                    "attempt, last_event_cursor, input_payload) VALUES "
                    "('legacy-operation', 'missing-project', 'outline', 'completed', "
                    "'{}', '{}', CURRENT_TIMESTAMP, 0, 0, '{}')"
                )
        # Alembic 独立创建 engine；让迁移和断言连接都真正开启外键检查。
        event.listen(Engine, "connect", enable_foreign_keys)
        engine = create_engine(settings.database_url)
        stamp = datetime.now(timezone.utc)
        with engine.begin() as connection:
            connection.execute(User.__table__.insert().values(
                id="migration-owner", username="migration-owner", display_name="Owner",
                password_hash="not-a-login", created_at=stamp,
            ))
            connection.execute(WritingDocTemplate.__table__.insert().values(
                id="migration-template", name="Template sentinel",
                created_by_user_id="migration-owner", create_idempotency_key="sentinel",
                create_request_sha256="a" * 64, created_at=stamp, updated_at=stamp,
            ))
            connection.execute(WritingDocTemplateRevision.__table__.insert().values(
                id="migration-revision", template_id="migration-template", revision_no=1,
                created_by_user_id="migration-owner", source_format="pptx", state="published",
                source_storage_key="keep/original.pptx", source_sha256="b" * 64,
                render_package_key="keep/base.pptx", render_package_sha256="c" * 64,
                compile_json='{"sentinel":"preserved"}', created_at=stamp, updated_at=stamp,
            ))
            connection.execute(WritingProject.__table__.insert().values(
                id="migration-project", owner_user_id="migration-owner", title="Bound document",
                selected_space_ids="[]", pptx_template_revision_id="migration-revision",
                created_at=stamp, updated_at=stamp,
            ))
            original = snapshot(connection)
            original_fk_issues = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        engine.dispose()

        command.upgrade(config, "20261003_0101")
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == original_fk_issues
            assert snapshot(connection) == original
        engine.dispose()
        command.downgrade(config, "20260929_0100")
        with engine.connect() as connection:
            assert snapshot(connection) == original
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == original_fk_issues
        engine.dispose()
        command.upgrade(config, "20261003_0101")
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE writing_doc_templates SET status='deleted' WHERE id='migration-template'"
            ))
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT status FROM writing_doc_templates WHERE id='migration-template'"
            )).scalar_one() == "deleted"
            deleted_snapshot = snapshot(connection)
        engine.dispose()

        with pytest.raises(RuntimeError, match="存在已删除模板"):
            command.downgrade(config, "20260929_0100")
        with engine.connect() as connection:
            assert snapshot(connection) == deleted_snapshot
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == original_fk_issues
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20261003_0101"
    finally:
        if engine is not None:
            engine.dispose()
            event.remove(Engine, "connect", enable_foreign_keys)
        settings.database_url = old_database_url
