"""AI 侧增加账套维度：待办与主动发现按 MES 账套隔离。

MES 是账套的权威源（factory 表），AI 只镜像：
MES 点「AI 数字员工」时把当前账套放进 SSO 票据（payload.factory），
AI 验票后写入用户的当前账套，业务查询按它过滤。

存量数据统一归到默认账套 F01，与 MES 侧 V8 迁移的回填口径一致，
否则历史待办会在切换账套后凭空消失。
"""

from __future__ import annotations

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

DEFAULT_FACTORY = "F01"


def upgrade(db) -> None:
    """加列、建索引、回填存量。幂等：重复执行不报错。"""
    cols = {
        "group_todos": "factory_code",
        "assistant_events": "factory_code",
    }
    for table, column in cols.items():
        db.execute(
            text(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} VARCHAR(20)"
            )
        )
        db.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_factory "
                f"ON {table} ({column})"
            )
        )
        # 存量归默认账套：没有归属的记录不能因为隔离而消失
        db.execute(
            text(
                f"UPDATE {table} SET {column} = :code "
                f"WHERE {column} IS NULL OR {column} = ''"
            ),
            {"code": DEFAULT_FACTORY},
        )
    logger.info("ai factory scope ready: %s", ", ".join(cols))