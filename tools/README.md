# 项目工具说明

| 属性 | 内容 |
|---|---|
| 文档类型 | 工具说明 |
| 适用场景 | 开发与质量 |
| 维护状态 | 生效 |
| 最后整理 | 2026-08-27 |
| 文档导航 | [文档中心](../docs/README.md) |

`tools/` 保存可重复执行的项目检查、构建辅助、真实页面取证和数据库维护工具。开发规则见 [开发与质量](../docs/development/开发与质量.md)。

## 常用工具

| 工具 | 用途 |
|---|---|
| `verify-project.mjs` | 文档审计、前端生产构建和后端 Maven 打包 |
| `docs-audit.mjs` | 当前文档的编码、结构、链接和导航检查 |
| `architecture-audit.mjs` | 检查通用面板核心不反向依赖 MES/PLM 业务适配器 |
| `action-support-audit.mjs` | 校验前端按钮动作支持表与后端 `PxService.callButton` 的 switch 分支一致（漂移即失败） |
| `entry-audit.mjs` | 校验自定义页面与 [入口清单](../docs/frontend/面板入口清单.md) 互相覆盖、站内跳转目标都有路由（死链即失败） |
| `docs-format.mjs` | 统一当前文档元信息和换行格式 |
| `detect-jdk.bat` | 查找本机可用的 JDK 17+ |
| `apache-maven-3.9.9/`、`settings.xml` | 固定 Maven 版本和镜像配置 |
| `tplus-grab/` | 真实 T+ 页面结构、截图和交互取证 |
| `e2e-*.cjs`、`regression-*.cjs` | 本地回归脚本，**MySQL 时代产物**（直连 `mysql` 客户端、默认后端端口 3308），复用前需按 PostgreSQL 改写 |
| `mysql2pg-schema.mjs` | 从 MySQL 运行库 `information_schema` 生成 PostgreSQL 结构基线 `db/migration/V1__baseline.sql` |
| `mysql2pg-copy.sh` | 一次性把 MySQL 数据搬进 PostgreSQL（`mysqldump --tab` + `COPY`），逐表核对行数与序列 |
| `pg-dialect-audit.mjs` | 方言门禁：运行路径出现 MySQL 专有语法即失败 |
| `pg-parity.mjs` | 迁移对账：MySQL 与 PostgreSQL 逐表行数、关键金额合计、序列健康 |
| `repair-db-question-marks.cjs` | 检查或修复错误文本管道造成的数据库中文 `??` 污染；应用修复前自动备份 |

常用命令：

```powershell
node tools/docs-audit.mjs
node tools/architecture-audit.mjs
node tools/action-support-audit.mjs
node tools/verify-project.mjs
```

历史：数据库 UTF-8 检查与修复（仅对 MySQL 有效，连接 `127.0.0.1:3306/light_mes`，密码从 `MES_DB_PASSWORD` 读取）：

```powershell
node tools/repair-db-question-marks.cjs --check
node tools/repair-db-question-marks.cjs --apply
```

## 面板资料

`tools/panels/` 只保留真实页面提取的结构化 JSON，例如表头和工具栏证据。历史逐面板 Markdown 笔记只保留在不入库的本地 `docs/archive/panels/`。

运行时面板配置的权威源是 MySQL `panel_config`，初始化基线是 `backend/src/main/resources/db/init.sql`。不得把 `tools/panels` 中的提取结果直接当作运行配置，也不得重新生成前端 Mock 种子。

## 增量和一次性脚本

`gen-*`、`patch-*`、`sync-*` 和 `update-*.sql` 多数服务于特定历史变更。使用前必须检查：

1. 输入文件和目标表是否仍存在。
2. 脚本是否会覆盖运行库数据或初始化 SQL。
3. 是否已有同名变更在当前代码中落地。
4. 是否准备了目标数据库备份和回滚方案。

完成一次性迁移后，不应继续把该脚本当作日常开发入口。重复能力应整合进正式初始化、后端逻辑或校验工具。

## 本地代码图谱

项目可使用 Milens 在本机生成 `.milens/milens.db`。该目录已忽略，不上传源码或索引数据。Node.js 20/22 与其原生 SQLite 依赖兼容性更稳定。

```powershell
milens analyze -p . -f
milens status -p .
milens search PxService -p .
milens impact PxService -p .
```

`docs/ref/`、`docs/archive/`、内置 Maven 和浏览器 profile 不属于项目源码，代码分析时应排除。
