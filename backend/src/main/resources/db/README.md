# db 目录说明（2026-11-12 起）

| 路径 | 角色 | 现状 |
|---|---|---|
| `migration/V1__baseline.sql` | **PostgreSQL 结构唯一权威源** | 由 `tools/mysql2pg-schema.mjs` 从 MySQL 运行库 `information_schema` 生成；Flyway 启动时执行/登记基线 |
| `init.sql`、`init_full.sql`、`init_local.sql`、`init_little_mes.sql`、`little_mes_import.sql` | MySQL 初始化基线 | **仅回滚窗口内保留**，不再演进 |
| `migrate_*.sql`、`seed_stock.sql`、`panel_report_config.sql`、`fix_role_dept.sql` | MySQL 历史迁移脚本 | 已全部落地，只作取证；换库后删除 |

规则：

1. 新增结构改动 → 追加 `migration/V2__xxx.sql`（Flyway 命名），不改 `V1`。
2. 想从 MySQL 重新生成结构基线：`set -a && . ./.env.local && set +a && node light-mes/tools/mysql2pg-schema.mjs`，生成后跑 `tools/pg-dialect-audit.mjs`。
3. 回滚到 MySQL 时把 `MES_DB_PORT` 改回 3306、`MES_FLYWAY_ENABLED=false`，结构仍来自这些 `*.sql`。
4. 回滚窗口结束（2 周）后，删除本目录除 `migration/` 与 `README.md` 外的全部文件，并移除 `pom.xml` 里的 MySQL 驱动与 `DbBackupService` 的 MySQL 分支。
