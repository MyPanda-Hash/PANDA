# AI 角色权限审查（2026-11-17）

> 目的：核对「管理员 / 车间主任 / 部门负责人 / 普通员工」四类角色收到的待办与 AI 发现，
> 是否符合各自的岗位定位，作为后续「AI 角色权限配置」的实现依据。

## 一、现状：系统里有 7 个账号、7 类角色

| 账号 | 姓名 | 角色码 | 角色名 |
| --- | --- | --- | --- |
| admin | 管理员 | ADMIN | 系统管理员 |
| worker | 赵车间 | **FOREMAN** | 车间主任 |
| planner | 李计划 | PLANNER | 计划员 |
| sales | 王销售 | SALES | 销售员 |
| keeper | 张仓管 | KEEPER | 仓管员 |
| qc | 孙质检 | QC | 质检员 |
| **staff** | **周一线** | **WORKER** | 车间调度（2026-11-17 新增） |

**注意两个陷阱：**

1. 账号名 `worker` 的角色是 **FOREMAN 车间主任**，不是一线工人。
2. 系统原本**没有一线员工账号**，本次审查新建了 `staff`（WORKER 角色，7 个一线面板：
   加工单 / 工序报工 / 派工 / 领料 / 材料出库 / 库存 / 完工入库，无订单、无客户、无价格）。

## 二、实测：各角色实际收到的数据

| 角色 | 账号 | 待办 | AI 发现 | 发现域分布 |
| --- | --- | --- | --- | --- |
| 管理员 | admin | 5 | **26** | 生产9 审批6 销售5 仓储3 采购2 质量1 |
| 车间主任 | worker | 13 | 13 | 生产9 **销售3** 仓储1 |
| 计划负责人 | planner | 10 | 14 | 生产9 销售3 仓储2 |
| 销售负责人 | sales | 2 | **0** | 无 |
| 仓管负责人 | keeper | 8 | 6 | 仓储3 审批2 采购1 |
| 质检负责人 | qc | 4 | 1 | 质量1 |
| 普通员工 | staff | **0** | **0** | 无 |

## 三、问题的根因：角色名被当成用户名用

`assistant_watch.py` 里有一张写死的面板负责人表：

```python
DEFAULT_PANEL_OWNERS = {
    "SO_ORDER":       ["planner", "worker"],
    "MANU_ORDER":     ["worker", "planner"],
    "STOCK_STATUS":   ["keeper", "planner"],
    "PU_ORDER":       ["keeper"],
    "ARRIVAL_IN":     ["qc"],
    "FINISH_INSPECT": ["qc"],
}
```

然后 `_resolve_users()` 拿这些**字符串当用户名**去查：

```python
rows = db.scalars(select(User).where(User.username.in_(wanted))).all()
```

本机账号的用户名恰好等于这些词（planner / worker / keeper / qc），所以「碰巧能用」。
但这是**语义错误**：表里写的是岗位，解析时当成了账号。换个工厂用户名是 `zhangsan`，整张表就失效。

## 四、四个具体问题

### 问题 1：一线工人一条也收不到

`MANU_ORDER` 的负责人写的是 `worker`，本意是「车间执行的人」，但账号 `worker` 是**车间主任**。

实测 26 条事件里 `worker` 被指派 13 次（全部生产类），新建的 `staff`（真一线）**0 条**。

### 问题 2：销售员看不到任何销售异常（最严重）

销售类事件派给的是**业务员姓名**，不是账号：

```
so_draft            → target 王芳   （MES 里的业务员姓名）
so_shipping_overdue → target 陈强   （同上）
```

`_resolve_users` 先按 username 查、查不到再按 display_name 查，两个都落空 → **返回空列表** →
事件没有接收人 → **只有管理员看得见**。

结果：sales（王销售）AI 发现 = **0**，而系统里明明有 5 条销售类异常。

### 问题 3：车间主任看到了销售域的问题

`so_unstarted`（销售订单已审核未排产）派给 `["planner", "worker"]`。
计划员看合理，车间主任看**属于越界** —— 排产是计划岗的事，车间只接派工。

### 问题 4：审批全落在管理员头上

6 条 `pending_approval` 里 5 条 target = `admin`。
和既定口径「生产加工单审批归车间主任」不符 —— 审批人没有按单据类型分派，默认全给管理员。

## 五、按角色定位：期望 vs 现状

| 角色 | 应该看到 | 现状 | 判定 |
| --- | --- | --- | --- |
| 管理员（老板） | 全盘：进度、风险、金额、异常汇总 | 26 条全量 | 符合 |
| 车间主任 | 生产进度、工单临期、工序停滞、人员负荷 | 13 条，但混入 3 条销售 | 基本符合，越界 |
| 部门负责人 | 本部门专业口径（计划/销售/仓储/质量各管一摊） | 计划、仓储正常；**销售 0**；质量仅 1 | 销售失效 |
| 普通员工 | 只看到「派给我的活」：报工、领料、交检 | 0 条 | 断链 |

## 六、建议（后续配置化改造方向）

1. **把 `DEFAULT_PANEL_OWNERS` 从「字符串」改成「角色码」**，解析时按 `sys_role.role_code` 查人：
   `MANU_ORDER → ["WORKER", "PLANNER"]`、`SO_ORDER → ["SALES", "PLANNER"]`。用户名怎么变都不影响。
2. **业务员姓名 → 账号的兜底要显式**：姓名匹配不上时，落给 **SALES 角色全体**，而不是丢成空列表。
   宁可多人可见，也不要无人可见。
3. **审批人按单据类型分派**：生产加工单 → FOREMAN；采购、入库 → KEEPER 或其上级；不要统一给 admin。
4. **一线事件要真正下沉**：工序报工、领料、交检这类，target 应该是 WORKER 角色当班的人。
5. **加一个「按角色自检」入口**：管理员能一键看到每个角色各自收到多少条、都是什么域。
   本次审查是手工跑了一遍，这个应该做进界面。

## 七、本次改了什么

只新建了 `staff` 账号（周一线 / WORKER / F01 / dept 6），**没有改动任何派发逻辑**。
问题 1–4 全部原样保留，等确认口径后再动。
## 八、改造实施记录（2026-11-17）

### 后端四条：已完成并验证 ✅

| 条目 | 做法 | 验证 |
| --- | --- | --- |
| ① 负责人改角色码 | `DEFAULT_PANEL_OWNERS` 全部改成 `SALES`/`FOREMAN`/`WORKER` 等；`_resolve_users` 支持「角色码 → MES 绑定表 → 用户名 → 显示名」四级解析 | 实测七个角色数据全部合理 |
| ② 业务员兜底 | 姓名匹配不上时落给 `["SALES"]` 角色全体，不再丢成空列表 | 销售员从 **0 条 → 3 条** |
| ③ 审批按单据分派 | 新增 `APPROVER_BY_PANEL`，审批人解析不出来时按面板找对口岗位 | 兜底生效 |
| ④ 一线事件下沉 | `manu_no_report` 改派 `["FOREMAN", "WORKER"]` | 普通员工从 **0 条 → 1 条** |

顺带修的关键缺陷：**`dedupe` 命中时不刷新接收人**。
原来改完派发规则，存量事件仍锁在旧接收人手里，看起来像没改。现在会跟着刷新。

改造后实测：

```
角色      发现  待办  域分布
ADMIN      5     5   approval 4 · purchase 1
FOREMAN   10    13   production 9 · warehouse 1        ← 不再有销售
PLANNER   13    10   production 8 · sales 3 · warehouse 2
SALES      3     3   sales 3                            ← 原来 0
KEEPER     6     8   warehouse 3 · approval 2 · purchase 1
QC         1     4   quality 1
WORKER     1     1   production 1                       ← 原来 0
```

数据前提：补齐了 `mes_user_bindings.mes_role_code`（原来只有 admin 有绑定，
其余六个账号补上角色码；密码留空不影响 SSO，下次登录会刷新）。

### ⑤ 自检界面：已完成 ✅

`#/models` 页新增「角色权限自检」卡片（`api.js` 的 `ROLE_AUDIT` + `models.js` 渲染），
接口 `GET /assistant/role-audit`（管理员）按岗位汇总未关闭的发现与未完成的待办。

实测渲染结果：

```
ADMIN      5   5   approval 4 · purchase 1
FOREMAN   10  13   production 9 · warehouse 1
PLANNER   13  10   production 8 · sales 3 · warehouse 2
SALES      3   3   sales 3
KEEPER     6   8   warehouse 3 · approval 2 · purchase 1
QC         1   4   quality 1
WORKER     1   1   production 1
```

某行全是 0 会高亮成「收不到任何东西」——这正是本次审查发现销售员 0 条的那种断链。

### 顺带修掉的两个真问题

#### 一、工作台在 MES 里整体打不开（`/ai` 部署前缀）

排查中发现 `http://localhost:4173/ai/` 的页面 `<base href="/">`，于是 `<script src="js/main.js">`
解析成 `/js/main.js` —— 撞上 MES 的 SPA 回落拿到 HTML，**模块根本不加载**，整页只剩 index.html 的静态骨架。

根因链：`vite.config.js` 的 `/ai` 反代 `rewrite` 把前缀剥掉了 → `serve-ai-desk.mjs` 只能靠 `rel` 猜前缀 →
猜成 `/` → base 写错。而 `index.html` 里写死的是 `/ai/`（给同源部署用的）。

修法：反代显式注入 `X-Desk-Prefix: /ai/`，`serve-ai-desk.mjs` 优先信这个头，不再推断。
独立站点 `:18092/` 仍旧落 `/`，两种部署都成立。

#### 二、`boot()` 任何一步抛错都是白屏

`main.js` 的启动流程原来没有分步容错：`buildRail` 之后的任何一步（`bindChrome`、`bindUserMenu`、
`bootSession`）抛错，页面就停在静态 HTML 上，控制台只留一条没人看的堆栈。

现在每一步记 `step`，失败时把「坏在哪一步 + 错误消息 + 重试按钮」直接画进 `#view`，
并写 `window.__bootError` 供排查。
