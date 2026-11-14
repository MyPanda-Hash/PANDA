# AI 助手主动监控 MES

> 代码：`backend/app/services/mes_client.py`、`backend/app/services/assistant_watch.py`、
> `backend/app/api/assistant_watch.py`、`backend/app/models/assistant_watch.py`
> 前端：`frontend-group/`（顶栏铃铛 + 事件抽屉）
> 迁移：`alembic/versions/20261113_0104_assistant_events.py`

## 1. 它做什么

AI 助手不再只等着人说话。它每 60 秒读一次 MES，把「该有人管的事」找出来，
按单据和角色派给具体的人：

```
MES 面板数据 ──► 规则判定 ──► 事件（去重） ──► 待办（派人）+ 群通知（AI 调度台）+ 铃铛弹窗
```

**只发现，不代劳**：不会在 MES 里点审批、改单据。动作仍然由人在 MES 完成，
AI 负责「让该看见的人看见」。

## 2. 现在能发现什么

| 规则 | 面板 | 触发条件 | 派给 | 严重度 |
|---|---|---|---|---|
| `so_unstarted` | SO_ORDER | 已审核且审批已过，但没有生产工单引用 | 计划员、车间 | warning |
| `so_draft` | SO_ORDER | 销售订单还是草稿 | 单据上的业务员 | info |
| `manu_due` | MANU_ORDER | 未完工，预完工日已过（critical）或 2 天内到期（warning） | 车间 | warning / critical |
| `manu_no_report` | MANU_ORDER + PROCESS_REPORT | 已开工/生产中，连续 N 天没有报工（默认 3） | 车间、计划 | warning |
| `manu_missing_material` | MANU_ORDER + MATERIAL_REQ + MATERIAL_OUT | 有领料申请但没出库、已开工超 2 天（可能是"黑料"生产） | 仓管、车间 | critical |
| `so_shipping_overdue` | SO_ORDER + SALE_OUT | 已排产但下单超 N 天仍未出库（默认 7） | 业务员 | critical / warning |
| `stock_low` | STOCK_STATUS | 数量低于阈值（全局 `MES_WATCH_LOW_STOCK_THRESHOLD`，可按物料覆盖） | 仓管、计划 | warning |
| `pu_draft` | PU_ORDER | 采购订单仍是草稿 | 制单人 | info |
| `pu_unshipped` | PU_ORDER | 已审核、未发货，交期已过或 N 天内到期（默认 3） | 仓管 | critical / warning |
| `arrival_uninspected` | ARRIVAL_IN | 已审核、检验状态=未检，到货超过 N 天（默认 2） | 质检 | warning |
| `finish_inspect_overdue` | FINISH_INSPECT | 下游未审批/未完成，单据超过 N 天（默认 2） | 质检 | warning |
| `pending_approval` | 39 个审批面板 | 开放审批接口返回「该审批人的待审单据」 | 该审批人本人 | warning |

面板默认处理角色（`DEFAULT_PANEL_OWNERS`）：

```python
{
    "SO_ORDER": ["planner", "worker"],
    "MANU_ORDER": ["worker", "planner"],
    "STOCK_STATUS": ["keeper", "planner"],
    "PU_ORDER": ["keeper"],
    "ARRIVAL_IN": ["qc"],
    "FINISH_INSPECT": ["qc"],
}
```

**低库存阈值的来源**：MES 没有「安全库存」字段，所以阈值放在 AI 侧配置：
`MES_WATCH_LOW_STOCK_THRESHOLD`（全局下限，0=不启用）+ `MES_WATCH_LOW_STOCK_ITEMS`
（按物料覆盖，格式 `CP004:150,CL005:50`）。这是刻意的：库存规则要能随料号调整，
不该写死在代码里。

### 2.1 审批待办：精确到人

配了 MES 开放审批接口（`MES_OPEN_API_ENABLED=1`）时，AI 不再猜审批人：

```
GET /api/open/approvals/approvers        → 哪些账号能审哪些面板
GET /api/open/approvals/pending?approver=<账号>
        → 该账号的待审单据：panelCode / panelName / formNo / submitter / submittedAt
```

于是事件标题是「销售订单 SO-xxx 待 planner 审批」，待办只派给 planner 本人，
去重键带人名：`pending_approval:<账号>:<面板>:<单号>`。

两个刻意设计：

- **接口只给元数据**，单据正文（客户、金额、明细）不出去，要办事回 MES 打开单据。
- **审批人对不上本地账号时不乱派**：事件照常记录，但不会退化成面板默认角色
  （否则会把别人的审批派给你），这类事件只有人工兜底。

### 2.2 待办分组：一人多类收 N 条

**决策（2026-11-12 用户确认）**：同一个人有多类事情，就收 **N 条**待办，不合并成一条。

理由：不同类别的处理方式不同——补货要走采购、催货要联系供应商、审批要打开单据。
合成一条会导致「处理完一半」时无法逐类勾选，也没法逐类说清进展。

分组键是 `(用户, 类别)`：

| 情况 | 结果 |
|---|---|
| 张仓管：2 项低库存 + 1 张采购未发货 | **2 条**待办：「2 项存货低于下限」「采购订单 PO-x 已过交期仍未发货」 |
| 李计划：3 张销售订单未排产 + 2 项低库存 | **2 条**待办：「3 张销售订单待安排生产」「2 项存货低于下限」 |
| 王销售：1 张草稿单 | 1 条 |

同类多张单据仍然合并（「3 张销售订单待安排生产（SO-A、SO-B、SO-C）」），
每条待办只关联自己那类的事件，所以关闭其中一类不影响另一类。

群通知里按人一行，多类用分号分段：

```
- @keeper：2 项存货低于下限（CP004、CL005）；采购订单 PO-2026-08-0001 已过交期仍未发货
```

### 2.3 时间口径

数据库里存 UTC。接口序列化时统一补成带偏移的 ISO（`2026-11-12T05:04:48Z`）；
不补的话前端 `new Date()` 会按本地解析，11:36 的消息会显示成 03:36（实测踩过）。

### 2.4 反复提醒、自动升级、静默时段

**反复提醒**：事件没处理，就按间隔再喊一次，不会石沉大海。

| 配置 | 默认 | 含义 |
|---|---|---|
| `MES_WATCH_REMIND_HOURS` | 4 | 隔几小时再提醒 |
| `MES_WATCH_MAX_REMINDERS` | 3 | 最多提醒几次（0=不提醒） |

- 只有 `open` 状态会催；点了「我认领了」变成 `handled` 就**不再打扰**
- 本轮刚建的事件不参与当轮提醒，避免一次扫描连发两条

**自动升级**：拖过 `MES_WATCH_ESCALATE_HOURS`（默认 24 小时）还没处理，
严重度自动提到 `critical`，事件上标「已升级」，并保留原始严重度便于回溯。

**静默时段**：`MES_WATCH_QUIET_HOURS`（如 `8-18`、`22-6`，按**本地时间**）。
不在时段内照常扫描、照常记录事件，但**不派待办、不发群消息**，
避免半夜把人吵醒；到了时段内下一轮扫描就会补上。

**直达链接**：每条事件带 `monday_url`，形如

```
http://localhost:4173/#/panelx/form/ARRIVAL_IN?code=DH-20261109-001
```

点「去 MES 处理」直接打开那张单据。注意 `MES_WATCH_UI_URL` 要填**前端地址**
（默认 4173），填成 8080 会 403 —— 后端端口不带前端页面。

### 2.5 认领备注与撤回

点「我认领了」会展开一个备注框（可留空，回车提交），备注存进事件的 `note`。
页面显示「管理员 已认领」和备注内容，便于交接。

认领后按钮变成「撤回认领」：误点或情况有变可以退回 `open`，
**备注保留**，方便追溯上次处理到哪一步。

### 2.6 归档

`resolved` / `handled` 超过 `MES_WATCH_ARCHIVE_DAYS`（默认 30 天）的事件打上
`archived_at` 标记，不再出现在列表和统计里。**不删数据**，需要回溯可以查库。
后台每天随扫描跑一次；`open` 的事件永远不会被归档。

手动触发：`POST /assistant/archive?days=30`

### 2.7 每日汇总

`GET /assistant/digest?days=1` 返回汇总数据；`POST /assistant/digest:post` 发到群里。
后台每天自动发一次（进程内用日期去重，重启不会重复发）。

```
今天的 MES 待办汇总：
- 新发现 9 条，处理 0 条，还剩 9 条未处理
- 待处理分布：@planner 5 条、@worker 3 条、@keeper 3 条、@wangfang 1 条…
- 主要类型：销售订单未排产 3、库存低于下限 2、销售订单草稿 1…
```

### 2.8 认领人筛选

事件抽屉顶部两排筛选：

**第一排（认领状态）**

| 选项 | 含义 |
|---|---|
| 全部 | 不筛 |
| 未认领 | `acked_by_user_id IS NULL` |
| 我认领的 | 我点了「我认领了」的 |
| 他人认领 | 别人认领的，避免重复处理 |

**第二排（按人）**：`所有人` / `派给我的` / `@每个被指派的人`（带条数）。

**一致性**：第二排的计数会跟随第一排变化——点了「未认领」后，
`@planner` 显示的是「未认领里派给 planner 的条数」，和列表实际条数一致。
（一开始按全量统计，出现过「标 5 条实际 4 条」，已修。）

接口：

```
GET /assistant/events?acked=none|me|others|any&assignee=<用户名|me>
GET /assistant/facets?acked=<同上>     返回计数与按人分布
```

`acked` 有白名单校验，传未知值返回 422。

### 2.9 巡查健康度与响应时效

**每轮巡查留痕**：`assistant_watch_runs` 记录每次扫描的开始/结束、耗时、
拉了几个面板、建/关了几条事件、发了几条提醒、失败原因。
失败的扫描也记一条，否则健康度上会显得「这段时间没数据」。

```
GET /assistant/health?hours=24
{
  "runs": 9, "ok": 8, "failed": 1, "success_rate": 0.889,
  "avg_duration_ms": 185, "seconds_since_last": 30,
  "interval_seconds": 60, "stale": false, "last_error": ""
}
```

`stale=true` 表示距上次巡查超过 2 个周期 —— **说明轮询停了**，这是最容易
被忽略的故障（页面照常打开，只是永远不更新）。

**响应时效**：

```
GET /assistant/sla?days=7
{"events": 9, "acked": 2, "avg_ack_hours": 0.5,
 "ack_within_4h": 1.0, "ack_within_24h": 1.0, "open_over_24h": 0}
```

- `avg_ack_hours`：从发现到被认领的平均耗时（认领时间用 `updated_at` 近似）
- `ack_within_4h` / `ack_within_24h`：4 小时/24 小时内认领的比例
- `open_over_24h`：还压着超过一天没动的条数

事件抽屉顶部显示一行状态：

```
● 近 9 轮成功 8 次、失败 1 次，平均耗时 185 ms   平均 0.5 小时认领 · 100% 在 4 小时内
```

绿点=全部成功，黄点=有失败但有成功，红点=从没跑过或已停摆。

### 2.10 定时报表：日报 / 周报 / 月报

**发到管理员个人聊天框**（不是群）——报表是管理视角，含全局数字，
在个人会话里能追溯、能引用、能被 AI 继续追问。

| 报表 | 何时发 | 覆盖区间 |
|---|---|---|
| 日报 | 每天 `ASSISTANT_REPORT_HOUR`（默认 9 点） | 最近 24 小时（滚动） |
| 周报 | 每周一 | 本周一 00:00 到现在 |
| 月报 | 每月 1 号 | 本月 1 号 00:00 到现在 |

日报用**滚动 24 小时**而不是"昨天 0 点到今天 0 点"——后者会漏掉今天 0 点后
新发现的事，也解释了为什么报表里"新发现"会比预期多。

**内容分区**：概览（新发现/处理/积压/认领时效/巡查健康）→ 新增分布（按类型）
→ 已处理 → 待办分布（按人，含无人负责数）→ 重点关注（按严重度+时效排序 top 8）。

**会话复用**：同一天/同一周的报表写进同一个会话（标题为 `MES 日报 2026-11-11`），
不会刷出一堆一次性会话。

**错过时间会补发**：11 点才启动也会发当天的日报，不会整天不发。进程内用
`(类型, 日期)` 去重，重启不会重复发。

接口：

```
POST /assistant/reports/{daily|weekly|monthly}?deliver=true  立即生成并投递
GET  /assistant/reports/preview?kind=daily                   只看内容不投递
```

配置：

```bash
ASSISTANT_REPORT_ENABLED=1
ASSISTANT_REPORT_HOUR=9
```

**模型不可用时只发数字**：报表内容由 `render_report()` 纯模板生成，
不依赖大模型，所以断网、没配 key 也照常出报表，不会编造结论。

### 2.11 报表里的大模型解读

数字永远由代码算（`render_report`），模型只负责**归因 + 给建议**，
追加在数字之后，分三段：`### 分析` / `### 建议` / `### 风险`。

实际输出示例：

```
### 分析
本期新增待办 9 条、已处理 0 条，积压 7 条，处理环节完全停滞；
积压集中在李计划（4 条）…认领快但无人闭环

### 建议
1. ⚠️ 李计划：尽快为 SO-2026-08-0002、SO-2026-08-0001 排出生产计划并关闭待办…
2. ⚠️ 张仓管：尽快对减速箱体 A（120/下限150）、包装木箱（40/下限50）提交补货…
3.   管理员：尽快确认 SO-2026-08-0003、PO-2026-08-0002 两张草稿是否有效…
```

**防编造机制**（三道）：

| 手段 | 做法 |
|---|---|
| 事实清单 | 把算好的数字/单号/人名拼成清单给模型，禁止它自己算 |
| 数字白名单 | 模型输出里每个数字都必须出现在清单中，否则整条丢弃 |
| 单号核对 | 引用的单号必须是清单里真实存在的，编的单号直接丢弃 |

**日期软化**：模型爱写「11-13 前」这类未来日期。直接丢弃会让建议整条消失，
所以把日期替换成「尽快」保留建议，同时堵住编造日期的口子。

**降级**：模型不可用、超时、返回不可解析，都安静跳过解读段，
报表照样发数字，不报错、不留空。

接口参数：`POST /assistant/reports/{kind}?insight=false` 可关闭解读。

**统计**：`GET /assistant/stats`

> 员工职能卡片与项目经验资料见 [员工知识库](员工知识库.md)（仅管理员可见）。 返回按类型/严重度的计数和超过 24 小时的条数，
事件抽屉顶部显示「共 9 条待处理：需要处理 6 · 紧急 1 · 提示 2」。

## 3. 去重与自动消解

- 每条事实有唯一 `dedupe_key`，例如 `so_unstarted:SO-2026-08-0001`
- 同一个 key 只留一条事件；重复扫到只更新标题/详情，不重复建待办
- **条件消失自动 resolved**：比如生产工单建好之后，`so_unstarted` 事件自己关掉
- 事件状态：`open` → `handled`（人点了「我已经处理」）→ 或 `resolved`（条件消失）
- **条件消失时待办会跟着关**：例如给 SO-xxx 建了生产工单，下一轮扫描（≤60 秒）
  把该事件置 resolved，并从相关待办里摘掉
- 汇总待办按**剩余单据**重算：3 张单处理 1 张 → 变成「2 张…」，全部处理完才关闭

## 4. 通知怎么到人

1. **待办**：每个目标人一条 `group_todos`（`origin=ai`），进他的「我的待办」
2. **群消息**：自动建 **AI 调度台** 群（6 人：管理员 + 5 个 MES 账号），
   发一条 AI 消息列出本轮新发现，`@planner`、`@worker` 这样点名
3. **铃铛**：群聊页顶栏红点数字，点开是事件列表，可逐条确认
4. **弹窗**：只有 warning / critical 才主动弹，避免打断输入

## 5. 配置（`doc-nexus-ai/.env`）

```bash
# 审批待办走精确接口（推荐）
MES_OPEN_API_ENABLED=1
MES_OPEN_API_BASE_URL=http://127.0.0.1:8080
MES_OPEN_API_TOKEN=<与 MES 侧 MES_OPEN_API_TOKEN 一致>

MES_WATCH_ENABLED=1
MES_WATCH_BASE_URL=http://127.0.0.1:8080
MES_WATCH_USERNAME=admin
MES_WATCH_PASSWORD=123456          # MES 账号密码，只用于只读轮询
MES_WATCH_INTERVAL_SECONDS=60
MES_WATCH_GROUP_NAME=AI 调度台
```

手动触发一次（管理员）：

```bash
curl -X POST http://127.0.0.1:18091/api/assistant/scan \
  -H "Authorization: Bearer <token>"
# {"evaluated":4,"created":0,"resolved":0,"open":4}
```

## 6. 接口

```
GET  /assistant/events?status=open     我该处理的（admin 看全部）
POST /assistant/events/{id}:ack        我已经处理
GET  /assistant/status                 开关、间隔、我的待处理数
GET  /assistant/stats                  按类型/严重度的统计
GET  /assistant/digest?days=N          周期汇总数据
POST /assistant/digest:post            把汇总发到群里（管理员）
POST /assistant/archive?days=N         归档旧事件（管理员）
POST /assistant/events/{id}:reopen     撤回认领
GET  /assistant/health?hours=24        巡查健康度
GET  /assistant/sla?days=7             响应时效
POST /assistant/reports/{kind}         生成报表并投递到管理员聊天
GET  /assistant/reports/preview        预览报表内容
GET  /assistant/todos                  AI 派给我的待办
POST /assistant/scan                   立即扫一轮（仅管理员）
```

## 7. 加一条新规则

在 `assistant_watch.evaluate()` 里追加一段，返回 `Candidate` 即可：

```python
out.append(Candidate(
    kind="stock_low",
    dedupe_key=f"stock_low:{material}",
    title=f"{material} 低于安全库存",
    detail=...,
    suggested_action="补采购申请",
    targets=["keeper"],
    severity="warning",
    panel_code="STOCK",
    doc_no=material,
))
```

新面板要读的话，在 `MesClient.fetch_snapshot()` 的 panel 列表里加上面板编码。

## 8. 验证

```bash
cd doc-nexus-ai/backend
./.venv/bin/python -m pytest tests/test_assistant_watch.py tests/test_assistant_watch_api.py -q
```

覆盖：规则判定（已审核未排产、已排产不报、草稿、临期/逾期、待审批）、
目标账号解析与兜底、去重、条件消失自动 resolved、按人过滤、确认权限。

真实数据实测（本机 MES，2026-11-12）：

```
扫描: {"evaluated":4,"created":0,"resolved":0,"open":4}
事件:
  [info]    销售订单 SO-2026-08-0003 仍是草稿            目标=[王芳]
  [warning] 销售订单 SO-2026-08-0001 已审核，尚未安排生产 目标=[planner, worker]
  [warning] 销售订单 SO-2026-08-0002 已审核，尚未安排生产 目标=[planner, worker]
  [warning] 销售订单 SO-2026-08-0004 已审核，尚未安排生产 目标=[planner, worker]
```

## 9. 已知限制

1. ~~审批队列只能靠面板字段推断~~ —— 已由 MES 侧开放审批接口解决（见 §2.1），
   精确到人，令牌+IP 白名单+限流+字段白名单，接口只有 GET。
2. **只读**：AI 不会替你审批/建单，只提醒。
3. **轮询间隔 60 秒**，不是事件驱动；MES 侧没有 webhook。
4. **目标人靠用户名/姓名匹配**，匹配不上就退回面板默认角色；多工厂/多角色细分
   需要把 `DEFAULT_PANEL_OWNERS` 改成可配置。
5. 目前只监控 `SO_ORDER`、`MANU_ORDER` 两个面板，采购、库存、质检还没接。
