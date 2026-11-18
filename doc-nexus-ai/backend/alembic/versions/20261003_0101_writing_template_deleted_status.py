"""允许永久逻辑删除写作模板。

Revision ID: 20261003_0101
Revises: 20260929_0100
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20261003_0101"
down_revision = "20260929_0100"
branch_labels = None
depends_on = None

_TABLE = "writing_doc_templates"
_CONSTRAINT = "ck_writing_doc_templates_status"


def _replace_check(expression: str, *, reject_deleted: bool = False) -> None:
    if context.is_offline_mode():
        raise RuntimeError("模板状态约束迁移需要在线数据库连接进行外键及数据校验")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # 替换约束期间阻止模板生命周期写入。
        bind.execute(sa.text("LOCK TABLE writing_doc_templates IN ACCESS EXCLUSIVE MODE"))
        if reject_deleted:
            _require_no_deleted(bind)
        op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
        op.create_check_constraint(_CONSTRAINT, _TABLE, expression)
        return
    if bind.dialect.name != "sqlite":
        raise RuntimeError("模板状态约束迁移仅支持 SQLite 和 PostgreSQL")

    # SQLite 开启外键时 DROP 父表会级联删除版本；事务外关闭检查，
    # 再以原子写事务重建。原表不改名，避免改写其他表的外键目标。
    with op.get_context().autocommit_block():
        foreign_keys = int(bind.exec_driver_sql("PRAGMA foreign_keys").scalar())
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
        if bind.exec_driver_sql("PRAGMA foreign_keys").scalar() != 0:
            raise RuntimeError("无法安全关闭 SQLite 外键检查，拒绝重建模板表")
        try:
            bind.exec_driver_sql("BEGIN IMMEDIATE")
            existing_fk_issues = set(bind.exec_driver_sql("PRAGMA foreign_key_check").all())
            try:
                if reject_deleted:
                    _require_no_deleted(bind)
                with op.batch_alter_table(_TABLE, recreate="always") as batch:
                    batch.drop_constraint(_CONSTRAINT, type_="check")
                    batch.create_check_constraint(_CONSTRAINT, expression)
                new_fk_issues = set(bind.exec_driver_sql("PRAGMA foreign_key_check").all())
                if new_fk_issues - existing_fk_issues:
                    raise RuntimeError("模板约束迁移新增外键异常，已拒绝提交")
                bind.exec_driver_sql("COMMIT")
            except Exception:
                bind.exec_driver_sql("ROLLBACK")
                raise
        finally:
            bind.exec_driver_sql(f"PRAGMA foreign_keys={foreign_keys}")
            if int(bind.exec_driver_sql("PRAGMA foreign_keys").scalar()) != foreign_keys:
                raise RuntimeError("无法恢复 SQLite 外键检查配置")


def _require_no_deleted(bind) -> None:
    if bind.execute(sa.text("SELECT 1 FROM writing_doc_templates WHERE status='deleted' LIMIT 1")).first():
        raise RuntimeError("存在已删除模板，不能降级状态约束；禁止自动恢复或物理删除模板")


def upgrade() -> None:
    _replace_check("status in ('active', 'archived', 'deleted')")


def downgrade() -> None:
    _replace_check("status in ('active', 'archived')", reject_deleted=True)
