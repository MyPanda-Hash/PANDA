# AGENTS.md — 轻 MES 项目工作模式

> 本文件由 DSH 按项目自动加载，是代理在本仓库的执行纪律与项目事实。
> 规则正文以 `docs/` 四份主题文档为唯一来源，术语以 `GLOSSARY.md` 为准；本文件只写路径、命令、门禁和容易踩的坑，不复制正文。

## 项目一句话

配置驱动的轻 MES：Vue 3 + Element Plus 前端，Spring Boot 3 + MyBatis-Plus 后端，MySQL `light_mes` 存面板配置与业务数据。列表、表单、明细、参照、选单、导入、权限共用 `PanelRuntime`，面板/字段/按钮/状态由 `panel_config` JSON 驱动，前后端数据键是中文。

唯一数据链路：浏览器 → Spring Boot `/api/px/*` → MySQL。项目只保留 SQL 后端模式，Mock、PanelX 直连/代理、运行模式开关都不再存在。

## 开工顺序

每个任务按此顺序走，不跳步：

1. **定权威源**：面板字段/按钮/状态/选单 → 运行库 `panel_config` + `backend/src/main/resources/db/init.sql`；业务规则与事务 → 后端；展示与交互 → 前端通用组件。
2. **读主题文档**：改面板读 `docs/frontend/前端面板设计.md`，改接口/审批/生单读 `docs/backend/后端逻辑设计.md`，改流程/测试/安全读 `docs/development/开发与质量.md`，上线读 `docs/deploy/服务器部署.md`。`docs/archive/` 是本地历史，不入库、不作实现依据。
3. **判边界**：前端 `core/` 不得 import `business/`，只能经 `PanelRuntime`；MES/PLM 编码、菜单、领域动作留在 `business/` 或后端适配层。
4. **最小实现**：一次一个行为。字段名、明细键、`buttonName`、状态值是跨端契约，改动时数据库、后端、前端消费者一起改。
5. **按矩阵验证**：命令见下节，交付时说清改了什么、为什么、怎么验证。

## 验证矩阵

| 改动 | 最低验证 |
|---|---|
| Markdown / 文档 | `node tools/docs-audit.mjs` |
| 前端 JS/Vue | `cd frontend && npm test` + `npm run build` |
| 后端 Java/SQL | `node tools/verify-project.mjs`（含 JDK 探测与 Maven 打包） |
| 通用面板边界 | `node tools/architecture-audit.mjs` |
| 面板配置 | 列表、表单、参照、选单、审批、导入、窄屏和无数据状态 |
| 跨端 / 部署 | `node tools/verify-project.mjs`，再 `git diff --check` |

本机实测（2026-10-22）：Node 24.19、JDK 25、项目内 Maven 3.9.9 可用；`mysql` 客户端不在 PATH，需要连库时先确认命令是否可用，或用 Node 脚本走 API。

## 代码地图

| 位置 | 职责 |
|---|---|
| `frontend/src/core/` | 通用面板视图、请求、按钮组、已审批判据（`doc-status.js`） |
| `frontend/src/business/` | MES 接口适配（`api.js`/`engine.js`）、菜单、权限动作（`permissions.js`） |
| `frontend/src/views/`、`layout/`、`stores/` | 门户壳、专属页面、用户/页签/工作台状态 |
| `backend/.../controller/` | HTTP 协议层 |
| `backend/.../panel/` | 通用面板运行时与 `PanelActionHandler` 扩展契约 |
| `backend/.../service/`、`mapper/`、`entity/` | 业务规则、事务、数据访问 |
| `backend/src/main/resources/db/init.sql` | 新环境初始化基线；运行库改动要回写这里防漂移 |
| `tools/` | 校验、构建、抓取、数据库维护工具；说明见 `tools/README.md` |

本地端口：MySQL 3308（`MES_DB_PORT`）、后端 8080（`MES_HTTP_PORT`）、前端开发 5173、预览 4173；换机器只设环境变量，不改文件。构建产物齐全时可跑 `start-project.bat`。

## 数据与 SQL 门禁

- **结构化业务表必须预留 30 个扩展列**：`ext_str1-10`（VARCHAR 200）、`ext_num1-10`（DECIMAL(18,4)）、`ext_date1-5`（DATETIME）、`ext_flag1-5`（TINYINT）。普通面板字段写 `panel_config` JSON，不改表；确需结构化字段时占用空闲 `ext_*` 列并登记「列 ↔ 业务字段」，新表建表时同样预留。存量库用 `tools/update-2026-10-22-reserved-columns.sql` 幂等补齐。规则正文见 `docs/backend/后端逻辑设计.md` §2。
- 初始化用 `mysql -uroot -p --default-character-set=utf8mb4 --execute="source backend/src/main/resources/db/init.sql"`。不要用 `Get-Content init.sql | mysql`，Windows 文本管道会把中文不可逆替换成 `?`；初始化后跑 `node tools/repair-db-question-marks.cjs --check`。
- `form_data` 业务唯一键是 `(panel_code, form_no)`；明细保存完整 `detail`，页面未加载完明细时不保存，后端不用缺失 `detail` 清空原数据。
- 单号取最大序号 + 查重 + 唯一键兜底，不用 `COUNT + 1`。
- 库存以 `STOCK_STATUS` 实时聚合为唯一口径，不复制静态“现存量”快照。
- 审批状态与审批记录同一事务推进；审批通过/驳回校验最近一条待审批提交记录；已生成下游单据的来源单不得直接弃审或删除。
- 质量链路按 `ARRIVAL_IN/FINISH_INSPECT/FIRST_INSPECT/PROCESS_INSPECT_APPLY → INSPECTION/PROCESS_INSPECTION → PURCHASE_IN/FINISH_IN` 组织；`DISPATCH` 只当报检来源。

## 界面与交互门禁

- 密集、安静、可扫描的制造业工作界面；文案用中性业务语言，无 Emoji、卡通提示、装饰动画、无意义渐变。
- 显示层中文走 `tt()`，数据键（`buttonName`/`dataName`/状态存储值）永不翻译；已审批判定统一用 `core/doc-status.js` 的 `isDocApproved()`，不各处手写状态集合。
- 禁用按钮同时阻止事件和请求；页签身份是 `path + query`，切换/恢复不丢单据号。
- 数量、单价、金额右对齐，日期/编号列宽完整可读；图标按钮给 tooltip；空状态、失败、权限不足用中性文案。
- 改 UI 后按 375 / 768 / 1440 检查：无横向滚动、无裁切、命中区 ≥40px。

## 权限与审批

- 11 项动作在 `frontend/src/business/permissions.js`：view / query / add / edit / delete / export / print / audit / price / review / adjust；角色-面板按动作授权，按钮经映射表校验，admin 全权。
- 改用户、角色、审批、删除逻辑时，同时验证未授权路径。
- 选单是拉式（`selectConfig`/`selectConfigs`，来源须已审核），生单是推式（后端按钮动作落库后返回 `gotoPanel` + 新编号）。

## 文档与提交

- 现行规范只维护在 `docs/` 四份主题文档：单一 H1、元信息表、相对链接、文件末尾换行；新规则放进对应主题文档，其他位置用链接引用。
- 代码旁的 `README.md` 只说明该目录；`GLOSSARY.md` 记术语与已敲定决策，成型一条记一条。
- 不自行 `git commit` / `git push`，除非用户明确要求；交付时展示 `git status` 与 `git diff --check` 结果。

## 谨慎使用的区域

- 已忽略目录不是源码，不要改、不要索引：`run/`、`runtime-logs/`、`docs/archive/`、`docs/ref/`、`tools/tplus-grab/profile*`、`dist*`、`backend/target/`、`.milens/`。
- `tools/gen-*`、`patch-*`、`sync-*`、`update-*.sql` 多是历史一次性脚本：先用之前确认输入文件与目标表仍在、不会覆盖运行库、变更是否已落地、有无备份回滚。
- 凭据只走环境变量（`SPRING_DATASOURCE_PASSWORD`、`MES_JWT_SECRET`、`ALIBABA_CLOUD_ACCESS_KEY_ID/SECRET`），示例一律写占位符；不入库、不回显。
- 禁止对源码做无限定全局替换；批量改动后必须构建并检查差异。

## 完成标准

1. 行为与对应主题文档一致，跨端契约三端同步。
2. 验证矩阵中对应命令实跑通过，输出作为证据；跑不了的明确说“未验证”和限制。
3. 无残留临时脚本、旧模式分支或重复配置源。
4. 成功、失败、无数据、权限受限状态都已覆盖到的最低限度验证。
5. 未覆盖或回退工作区里的其他未提交改动。