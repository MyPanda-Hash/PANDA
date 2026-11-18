# 新版 AI 数字员工前端 · 开发契约（CONTRACT）

> 给并行开发用的硬约束。**先读这一页再动手**；与本文冲突的实现视为返工。
> 目录：`doc-nexus-ai/frontend-desk/`（无构建步骤，浏览器直接跑 ES 模块）

## 0. 背景与边界

- 旧界面（`frontend/dist` + `frontend-group` + `serve-ai-web.mjs` :18091）是**厂商产物 + 我们后注入的群聊/待办/语音**，现在**原样保留只当参照物**，不改、不删、不当作实现依据。
- 新界面只依赖后端 API（FastAPI :8000）与我们自己的 `frontend-group/` 资产（语音窗、录音编码）。
- 厂商的东西**不复制进新项目**：不抄 `frontend/dist` 的任何代码/样式；可以照抄的是**我们自己的** `frontend-group/app.js` 里的业务逻辑（待办明细解析、状态机、MES 链接处理）和 `pod/` 的语音协议实现。

## 1. 设计硬规则（来自 Ant Design v6 设计库）

| 项 | 值 |
|---|---|
| 颜色 | 只用 `--ant-*` 变量；主色由 `css/theme-mes.css` 覆盖成 **制造绿 #116a5b**（与 light-mes 同一套），不要再引用 antd 默认蓝 |
| 间距 | 8pt 网格：8/16/24/32；卡片内边距 16/20 |
| 控件高 | 32（小 24 / 大 40），命中区 ≥40px（移动端） |
| 圆角 | 6（小 4 / 大 8） |
| 正文 | 14/22；辅助 13/20；标题 15–20 |
| 卡片 | **0 层：白底 + 1px 边框，禁止阴影**；只有浮层/下拉/抽屉有阴影 |
| 数字 | 右对齐 + `tabular-nums`（`.num`） |
| 按钮 | 一个操作区最多一个 `.btn--primary`；危险操作只用 `.btn--danger` 文字按钮 |
| 空态 | 必须给主行动（`.empty` + 一个 `.btn--primary`） |
| 图标 | 只用 `/icons/*.svg`（21 个已就位），经 `util.icon(name)` 生成，**不自造 SVG** |
| 装饰 | 禁止装饰渐变、大色块、Emoji、卡片套卡片 |
| 响应式 | 1440 / 768 / 375 三档，任何一档 `scrollWidth > clientWidth` 即不合格 |

## 2. 已有骨架（不要改这些文件）

| 文件 | 内容 |
|---|---|
| `index.html` | 外壳：左侧 `.rail` + `.topbar` + `.view`；已引用所有 css 与 `js/main.js` |
| `css/tokens.css` | 设计库 536 个 `--ant-*` 变量（明/暗） |
| `css/desk.css` | 设计系统 + 布局 + 通用组件类（见 §3） |
| `js/util.js` | `el/icon/timeAgo/formatMoment/parseTodoDetail/toast/priorityTone/statusTone/alignMesHost/...` |
| `js/api.js` | 所有后端调用（见 §4） |
| `js/main.js` | 登录门、hash 路由、导航角标、顶部动作；按路由 `import('./<view>.js')` |
| `serve-ai-desk.mjs` | :18092 静态 + `/api` 反代 + `/pod` 同源 + WS 隧道 |

**你只改自己的两个文件**：`js/<你的视图>.js` 与 `css/<你的视图>.css`。
需要新增通用类 → 在自己 css 里用 `.<视图前缀>-xxx` 命名，不要改 `desk.css`（会和其他人冲突）。

## 3. 可直接用的通用类

`btn / btn--primary / btn--ghost / btn--text / btn--danger / btn--sm / btn--block`、
`icon-btn`、`tag + [data-tone=danger|warn|accent|muted]`、`card / card__head / card__body(+--flush) / card__foot`、
`stats / stat / stat__label / stat__num`、`list / list__row / list__main / list__title / list__meta / list__time`、
`tabbar / tabbar__btn[aria-selected]`、`seg / seg__btn[aria-selected]`、
`empty / empty__icon / empty h3 / empty p`、`avatar / avatar--sm`、
`drawer / drawer__mask / drawer__panel / drawer__head / drawer__body / drawer__foot`、
`input / textarea / select`、`toast`、工具类 `row / col / wrap / grow / muted / secondary / num / mono / stack-16 / stack-24 / sr-only / skeleton`。

按钮语义：`<button type="button">`；可点击列表行用 `<button class="list__row">` 并在选中项加 `aria-current="true"`。

## 4. 模块契约

```js
// js/todos.js
export async function mount(root, ctx) { /* 把界面挂到 root（已经是 .view__inner） */ }
export async function unmount() { /* 清定时器、关抽屉、解绑 window 事件 */ }
```

`ctx` 提供：
- `ctx.api` — 见 §5；`ctx.util` — util.js 全部导出
- `ctx.user` — `{id, username, display_name, role}`
- `ctx.navigate(hash)` — `#/todos` `#/chat` `#/voice`
- `ctx.emit(name, detail)` / 监听 `window.addEventListener('desk:search', ...)`（全局搜索框输入，detail 是字符串）
- `ctx.refreshCounts()` — 刷新左侧角标
- `ctx.state.counts` — `{todos, events, unread}`

路由固定的三页：`todos`（待办工作台，首页）、`chat`（对话）、`voice`（语音助手）。
跨页跳转用 `ctx.navigate`；同一模块内**不要**再整页刷新。

## 5. API 契约（前缀见 §8，凭证是 MES 的登录态）

### 待办（核心数据）
`GET /api/todos?limit=200&status=` → `Todo[]`

```jsonc
{
  "id": "gtodo_xxx",
  "title": "核查两张未检到货单并补检",
  "detail": "· 子项（补充说明）\n· 子项2",       // 用 util.parseTodoDetail() 拆
  "priority": "urgent|high|normal|low",
  "status": "pending|accepted|doing|done|dismissed",
  "origin": "ai|human",
  "assignee_display_name": "管理员",
  "group_name": "AI 调度台",
  "source_message_id": "msg_xxx",              // 有值时说明来自群聊
  "created_at": "2026-11-16T09:00:00+08:00",
  "due_at": null,
  "nudge_count": 2, "nudged_at": "...", "escalated_at": "...",
  "refs": [{"panel":"PU_ARRIVAL","docNo":"DH-20261112-001","label":"到货单","url":"http://localhost:4173/#/panelx/form/PU_ARRIVAL?code=DH-..."}]
}
```

`PATCH /api/todos/{id}` body `{"status": "pending|accepted|doing|done|dismissed"}` → 返回更新后的 `Todo`
（状态机：`pending → doing → done`，任何非终态可 `dismissed`；接口只校验枚举，不校验流转顺序）。

### AI 发现（主动监控 MES 产生的问题，不是人派的活）
`GET /api/assistant/watch/events?status=open&limit=50` → `Event[]`

```jsonc
{
  "id": "aev_xxx", "kind": "so_unstarted", "severity": "critical|warning|info",
  "status": "open|resolved|acked", "title": "销售订单 SO-... 已审核，尚未安排生产",
  "detail": "客户：西部材料", "suggested_action": "按这张销售订单生成生产工单并排产",
  "panel_code": "SO_ORDER", "doc_no": "SO-2026-08-0004",
  "targets": ["planner","worker"], "created_at": "...", "age_hours": 12.5,
  "reminders": 3, "escalated": false, "monday_url": "http://localhost:4173/#/panelx/...",
  "acked_by": null
}
```
`POST /api/assistant/watch/events/{id}:ack` → 认领（无 body）

### 生产概况（AI 主动汇报，语音窗也会念这段）
`GET /api/assistant/briefing` → `{data: {text, ok, note, lines: string[], items: [{label, value}], skipped, generatedAt}}`

### 通知
`GET /api/notifications?unread=true&limit=20` → `{data:{unread, items:[{id,title,body,createdAt,read}]}}`
`POST /api/notifications/read` body `{ids: []}`

### 对话
- `GET /api/chat/sessions` → `Session[]`：`{id,title,message_count,created_at,updated_at,pinned,archived}`
- `GET /api/chat/sessions/{id}?message_limit=60` → `{...session, messages: Message[], has_more_messages}`
- `POST /api/chat/sessions` body `{title}` → session
- `POST /api/chat/messages` **SSE 流**，body `{message, local_session_id?, session_id?, client_message_id?}`

SSE 事件（每行 `data: {json}`）：
| type | 处理 |
|---|---|
| `session` | `event.session.id` 记下会话 id（新会话可能在这里才拿到） |
| `assistant_message` + `phase:"start"` | 先放一个空的助手气泡（打字中） |
| `event` + `item.type==="text_delta"` | `item.delta` 追加到当前气泡（增量渲染） |
| `assistant_message` + `phase:"complete"` | 用 `message.content` 覆盖该气泡（最终态以此为准） |
| `error` | 气泡内显示 `detail`，停止加载态 |
| `done` | 结束加载态、刷新会话列表 |

用 `api.streamChatMessage(body, {onEvent, signal})` 即可，已封装解析。消息 `Message`：`{id, role:"user"|"assistant", content, parts, references, created_at}`。

### 语音
- `GET /api/assistant/voice/status` → `{data:{available, reason, model, voiceId, maxSeconds, writeEnabled, activeSessions}}`
- `POST /api/asr/transcribe`（multipart `file`，WAV 最稳）→ `{text}`
- 实时双向语音：同源 iframe `/pod/?embed=1`（MOSS-Live WS `/api/assistant/voice/ws`，由 `frontend-group/pod` 实现）


### 9.x 会话正文的 Markdown 支持范围（`js/chat.js` 的 `renderMarkdown`）

模型输出的是 Markdown，正文必须按结构渲染；只当纯文本会把 `### 概览`、`| 列 | 列 |` 原样显示出来。当前支持：

| 结构 | 语法 | 说明 |
| --- | --- | --- |
| 标题 | `#` ~ `######` | `#`/`##` → h3，`###` → h4，更深 → h5（会话里不出现 h1/h2） |
| 列表 | `-` / `1.` | 有序/无序；任务列表 `- [ ]` / `- [x]` |
| 行内 | `**粗体**`、`` `代码` `` | |
| 代码块 | 三个反引号 + 可选语言 | 深色底 + 右上角复制按钮 |
| 引用 | `>` | 左侧竖线 + 浅底 |
| 分隔线 | `---` / `***` | |
| 表格 | `| a | b |` + 分隔行 | 窄屏横向滚动，不撑破气泡 |

**安全底线**：仍然只用 `createTextNode` / `textContent` 拼节点，模型输出永远不当 HTML 解析。

**改这段必须跑一次真会话**：正则写错会让整个对话页抛 `Invalid regular expression` 并白屏（2026-11-17 踩过——`\s` 在字符串里被吞成 `s`）。

## 6. 本地运行与验收

```bash
cd /Users/panda/Projects/_light-m-ai
python3 doc-nexus-ai/mock/serve_mock.py &        # :18094 假数据（真库 183 条待办里导出的）
node serve-ai-desk.mjs --mock &                  # :18092 界面（走 mock）
open http://127.0.0.1:18092/?theme=light#/todos

# 三档截图 + 横向滚动检查（ego-browser 必需，不能用 chrome）
~/.local/bin/ego-browser nodejs < doc-nexus-ai/mock/shot.mjs
# 产物 /tmp/dsh-aidesk/shots/<route>-<width>.png
```

真后端（需要 MES 起来才有登录态）：`node serve-ai-desk.mjs`（不带 `--mock`），:18092 反代到 :8000。

**验收必须覆盖**：加载中（骨架或占位）、空数据、请求失败、窄屏 375、深色主题（`?theme=dark`）。

## 7. 写作要求

- 中文文案：中性业务语言，不用 Emoji、不用「亲」这类口语。
- 代码注释写「为什么」，不写「做了什么」；不要留 `console.log`。
- 不用 `innerHTML` 拼用户数据（用 `util.el` + `textContent`）；MES 链接 `target="_blank" rel="noopener noreferrer"`。
- 每个异步请求都要有失败分支（`ApiError.status===401` → 提示重新登录；`0` → 提示后端未启动）。
## 8. 同源部署与路径规则（2026-11-17 起，硬约束）

AI 助理已并入 MES，**同一份代码有两种运行形态**，路径写错会在其中一种下静默失效：

| 形态 | 页面 URL | 接口前缀 | 说明 |
|---|---|---|---|
| 同源（用户实际使用） | `http://<host>:4173/ai/#/todos` | `/ai-api` | MES 前端反代到 AI 进程；浏览器带 MES 登录态 |
| 独立站点（内部直连） | `http://127.0.0.1:18092/#/todos` | `/api` | `serve-ai-desk.mjs` 自身反代 |

### 8.1 三条硬规则

1. **禁止写以 `/` 开头的内部路径**。`/group/`、`/pod/`、`/css/x.css` 这类绝对路径**会绕过 `<base href="/ai/">`**，同源形态下直接落到 MES 上——历史上群聊 iframe 因此把 MES 自己嵌了进来（2026-11-17 踩过）。
   - HTML 里：用相对路径（`href="app.css"`、`src="app.js"`）。
   - JS 里：用 `util.appPath('group/')` 解析成当前部署下的真实地址。
2. **接口一律走 `js/api.js` 的 `API_ROOT`**，不要自己拼 `/api/...`：它会按 `location.pathname` 自动在 `/api` 与 `/ai-api` 之间切。非 desk 的页面（如 `frontend-group/app.js`）同样按此判断。
3. **反代顺序与 rewrite 写死不要动**（`light-mes/frontend/vite.config.js`、生产 nginx 同理）：
   - `/ai-api` 必须排在 `/ai` **之前**，并 `rewrite` 去掉前缀；
   - `/ai` 必须 `rewrite` 去掉 `/ai` 前缀（工作台按根路径组织资源）。

### 8.2 身份：不再有 AI 自己的登录

- 浏览器只登 MES，登录态在 `localStorage.mes_token`；desk 的请求自动带 `Authorization: Bearer <mes_token>`。
- AI 后端用共享密钥 `MES_JWT_SECRET` 验签（**MES 侧 jjwt 用 HS512**，不要写死 HS256）。
- 权限每次向 MES 取（`/api/auth/userinfo`、`/api/auth/perms`），**不要在 AI 侧缓存角色或写死管理员判断**。

### 8.2.1 三个独立页面都要自带身份（否则会「MES 套 MES」）

AI 侧有三份**独立文档**（不是 SPA 内路由），它们各自发请求，各自要带 MES 的令牌：

| 页面 | 文件 | 身份写法 |
|---|---|---|
| 工作台 | `js/api.js` | `API_ROOT` + `Authorization: Bearer` |
| 群聊 | `frontend-group/app.js` | `api()` 里注入 Bearer |
| 语音 | `frontend-group/pod/pod.js` | fetch 注入 Bearer；WS 用 `?token=` |

**漏了会怎样**（2026-11-17 群聊踩过第二次）：`/auth/me` 拿到 401 → 群聊页执行 `location.replace('/chat')` → 这个旧地址打到 MES 上被 SPA fallback 成**整个 MES 页面** → 群聊 iframe 里套进一层完整的 MES（顶栏 + 页签 + 工作台），看起来像页面嵌套出错。

所以两条一起守：**带上令牌**，且**401 的兜底跳转只能指向工作台自己的地址**（`/ai/#/chat`），绝不能是 `/chat` 这类旧门户路径。



### 8.8 权限边界：对话能查什么，由账号决定（2026-11-17 修复）

**曾经的漏洞**：AI 侧 MES 工具用系统级账号取数（`MesClient()` 不带用户身份），等于任何能提问的人都能看到全部面板。
实测：`worker` 在 MES 里只有 10 个面板、不含销售订单，却能通过工具查到销售订单。

修复分两层，缺一不可：

| 层 | 做法 |
| --- | --- |
| 权限判定 | `app/services/mes_permissions.py`：用调用者自己的 token 调 MES `/api/auth/userinfo`，取 `visiblePanels` + `isAdmin`（60s 缓存） |
| 取数身份 | `MesClient(access_token=...)` 用调用者 token 查数，MES 自己再裁一次数据范围；不传 token 一律拒绝，**绝不退回系统账号** |

四条越权路径与对应拦截：

| 路径 | 拦截方式 |
| --- | --- |
| `query_panel` 直接查无权面板 | 查数前 `require_panel` |
| `doc_detail` 传空 panel 绕过 | 用 MES 返回的 `panelCode` 事后校验 |
| `trace_doc` 销售订单也带生产路线 | 用「归属探测」：在无权面板里按单号搜，搜到即拒绝（**不能靠「是否生产路线」判断**） |
| 无 token 调用 | `_client("")` 直接抛错 |

**归属探测为什么用系统身份**：用户无权查该面板，用他的 token 探测只会失败，从而误判成「不属于该面板」而放行（踩过）。探测只判断归属，不返回数据。

测试锁死：`tests/test_mes_permission_gate.py`（7 项，覆盖上述路径与「有权不受影响」）。

### 8.9 服务端工具循环（对话真的能查数据）

`anthropic_compat._run_with_tools()`：chat-demo 是纯浏览器客户端，不会自己执行工具，所以工具循环在服务端完成，对前端透明。

- 工具集：`app/services/chat_tools.py`（`mes_query_panel` / `mes_doc_detail` / `mes_trace_doc` / `mes_stock` / `generate_period_report`），全部**只读**；
- 最多 4 轮；每轮把 `tool_calls` 执行结果作为 `role: tool` 回灌；
- 最终文本再切块走 Anthropic SSE 吐给前端（前端仍看到逐字输出）；
- 工具执行失败（含无权限）作为文本返回模型，由模型如实转告用户。

### 8.10 会话归档（管理员审计）

`app/models/chat_archive.py` + `app/api/chat_archive.py` + `frontend-desk/js/archive.js`：

| 项 | 说明 |
| --- | --- |
| 上报 | chat-demo 侧 `chatArchive.ts` 在活跃会话变化时 `POST /chat-archive/sync`（节流 5s，失败静默） |
| 归档 id | `用户id:前端会话id`，同一机器换人登录不会互相覆盖 |
| 内容 | 标题、开始/最后活动时间、消息数、最后一次提问摘要、完整消息快照 |
| 谁能看 | 列表与详情都走 `admin_user`；普通账号 403 |
| 入口 | 对话侧边栏「会话归档」（`isMesAdmin()` 才渲染）→ `#/archive` 视图 |
| 时区 | 后端返回 `...Z` 显式 UTC，前端按本地时区渲染（漏了 Z 会差 8 小时） |

**边界**：归档是旁路，不改变 chat-demo 自己的浏览器本地存储；对话正文仍各自独立。

### 8.11 新会话顶部「今天」数据卡（2026-11-17）

`frontend-group/chat-deepseek-src/src/components/session/TodayPanel.tsx`：新会话欢迎页在问候语下方插一块数据面板，第一眼就能看到要拍板的事、逾期、待办。

| 项 | 做法 |
| --- | --- |
| 数据源 | `GET /assistant/briefing/board?role=<账号等级映射>` + `GET /todos?limit=200`（都带 MES token） |
| 取哪些数 | 简报 `stats` 里**不是「暂无样本」**的前两项 + 本地算出的「待处理待办」（含紧急数） |
| 一行摘要 | 简报 `headline` 原样展示 |
| 底部一行 | 第一条 `decisions.title`，前缀「接下来：」 |
| 取不到数据 | 整块 `return null`，不留占位、不报错 |
| 字体 | **与对话正文同一套**：字体族 `inherit`（body 的 system-ui 栈）、摘要 14/22、辅助 13/20、数字 22/30（实测三者 `fontFamily` 与输入框完全一致） |
| 配色 | **不依赖主题变量**（本包部分 `--dsw-*` 运行时解析不到）：一律 `color: inherit` + `rgba(128,128,128,·)` 半透明描边/底色，明暗两套都稳 |
| 上色规则 | 只有 `data-tone="warn"` 的数字用暖橙，其余保持正文色——避免满屏彩色 |
| 宽度 | 与输入框同宽（实测均 776px），视觉成一条竖列 |

**踩过的坑**：`--dsw-alias-label-primary` 在本包里 `getPropertyValue` 返回空，导致数字颜色回落到蓝色品牌色；改成 `inherit` 后正常。

### 8.12 报表就地查看（2026-11-17）

报表（日报/周报/月报）生成后投递到**后端对话会话**；但对话页已换成 chat-demo（浏览器本地存储），从对话界面翻不到这些报表。

所以简报页自己给出口：

| 项 | 做法 |
| --- | --- |
| 入口 | 简报「本期报表」每行，已生成时多一个「查看」（在「重新生成」左侧） |
| 取数 | `GET /chat/sessions/{sessionId}?message_limit=5`，取最后一条 `assistant` 消息正文 |
| 展示 | `js/report-view.js` 抽屉 + `js/markdown.js` 渲染（标题/列表/粗体/代码块/表格/引用） |
| 无正文兜底 | 提示「可以点重新生成」，不显示空白 |

**`js/markdown.js` 是从被覆盖的对话页里恢复出来的渲染器**（之前 `/tmp` 备份），独立成模块供报表复用。
恢复时踩到：备份文件里的 `\s` 在字符串处理中被吞成 `s`，整个模块正则失效；另外 `inlineNode`/`appendInline` 原本在原文件的后续位置，备份里没有，要一并补。

### 8.13 能力边界：能聊任何话题，工具只是附加能力（2026-11-17）

**问题**：身份提示词只写了「你是系统里的助手」，模型自己推理成「我只连着这套系统的数据」，连「今天天气怎么样」都整类拒答（用户截图反馈）。

`anthropic_compat._identity_prompt()` 现在同时讲清两件事：

| 维度 | 内容 |
| --- | --- |
| 身份 | 以助手名自称；不自称 DeepSeek；不提模型厂商/训练来源 |
| 能力 | **可以回答任何话题**（常识/写作/翻译/计算/编程等，不需要工具）；系统工具是**附加能力**，不是能力边界 |
| 数据 | 涉及本系统业务数据时才用工具，且只返回当前账号有权限的部分 |
| 无实时数据 | 直接说「我没有实时数据」并给出获取途径，**不要因此拒绝整类问题** |

实测（改后）：

```
「今天天气怎么样」→ 我没有实时天气数据…可以用天气 App / 中国天气网；顺手问要不要按天气看排期
「翻译成英文：今天要交付三张订单」→ 正常翻译 + 三个语域版本
「你是谁」→ 我是小智…我能做两件事：聊（任何话题）、查（业务数据，按账号权限）
```

### 8.14 报表导出 PPTX（2026-11-17）

报表此前只有 Markdown 文本，看着像日志；企业里日报/周报是拿去过会的，需要版式。

| 项 | 做法 |
| --- | --- |
| 生成 | `app/services/report_deck.py`：`build_pptx(db, kind, session_id)` 复用 `presentation_deck` 的 `build_deck_spec` + `render_deck_spec` |
| 正文来源 | **优先读会话里那份**（`session_id`）——用户看到的除了系统口径数字，还有模型追加的「分析」「建议」；读不到才回落到 `render_report` |
| 章节切分 | 按 `### ` 小节切成页（概览/新增分布/已处理/待办分布/重点关注/分析/建议/风险） |
| 封面/结尾 | `_restyle()` 把模板默认的「基于引用资料整理的方案汇报」「谢谢」换成本报表口径；封面抬头用 `brand_kicker` 覆盖成 `PRODUCTION / MES <日报>` |
| 接口 | `GET /assistant/briefing/reports/{kind}/pptx?session_id=…`，`Content-Disposition` 用 RFC 5987 编码中文名 |
| 入口 | 报表抽屉右上「导出 PPTX」（`js/report-view.js` → `downloadReportPptx`，fetch 成 blob 再触发保存） |

`presentation_deck._render_cover` 的 `brand_kicker` 是**向后兼容**的覆盖点：不传仍是原来的 `PROPOSAL / MANAGEMENT BRIEF`，写作项目的历史导出字节不变。

实测：日报导出 10 页（封面 + 8 个内容页 + 下一步），44 KB，`python-pptx` 可读回。

### 8.15 资料库（2026-11-17）

`app/models/library.py` + `app/api/library.py` + `frontend-desk/js/library.js`：

| 项 | 做法 |
| --- | --- |
| 分类 | 固定四类：报表模板 / 制度文件 / 工艺文件 / 其他（自由文本会让列表没法筛） |
| 存储 | 文件本体在 `data/library/`，库里只存 `stored_name`（相对名，带 id 前缀防同名覆盖） |
| 上限 | 单文件 20 MB |
| 权限 | 列表/下载：登录即可；删除：仅上传者本人或管理员 |
| 入口 | 工作台 `#/library`，列表 + 分类筛选 + 上传 + 下载 + 删除 |

**关于「从网上下载模板」**：执行时确认免费可商用的中文商务 PPTX 几乎没有可靠直链（多数站点是登录墙/积分墙），
且第三方配色与本系统不一致仍要逐套改。因此用系统版式引擎**生成**了 3 套同族模板入库（藏青金 / 制造绿 / 石墨灰），
真 PPTX、可编辑、版权干净；资料库同时支持自行上传外部模板，两种来源并存。

### 8.16 修复：点「重新生成」整页白屏（2026-11-17）

**现象**：简报页点「重新生成」，整页空白，角落红字 `Cannot read properties of undefined (reading 'state')`。

**根因**：`brief.js` 里 `load()` 的签名是 `load(role, ctx)`，但生成成功后的回调写成 `await load(ctx)` ——
把主机壳当成了 `role` 传进去，真正的 `ctx` 变成 `undefined`，随即 `ctx.state.role` 就炸了。
（同目录 `floor.js` / `my.js` 的 `load(ctx)` 是单参数，调用一致，所以只有简报页中招。）

**修复**（两层，防再犯）：

| 层 | 内容 |
| --- | --- |
| 纠正调用 | `await load((ctx.state && ctx.state.role) || 'manager', ctx)` |
| 签名防御 | `load(role, ctx)` 开头：第一个参数若是对象，视为 `ctx` 并从中取 `role`；`role` 为空回落 `manager` |

验证：修复后点「重新生成」→ 正常提示「已生成」、报表列表与正文都在（`reports: 3`，不再白屏）。

### 8.17 报表「文档版式」渲染：网页里也能看到 PPT 观感（2026-11-17）

**反馈**：重新生成后看到的还是纯文本，没有 PPT 那种版式。导出 PPTX 是对的，但「查看」这一屏也该有层级。

`frontend-desk/js/report-deck.js` 把报表正文按小节语义换版式，**不再整篇当 Markdown 排**：

| 小节 | 版式 |
| --- | --- |
| （整篇） | 深色封面条：金色 kicker + 标题 + 周期，和导出的 PPTX 同一套语气 |
| 概览 | **指标卡**——从「新发现 **9** 条」里抽 (标签, 数值)，一排大数字 |
| 新增分布 / 已处理 / 待办分布 | **横向条形**——按最大值归一，右侧带数值；支持「甲 14 条、乙 13 条」这种一行多项 |
| 重点关注 / 风险 | **警示条目**——重点关注用暖色底 + 圆点，风险用中性底 |
| 建议 | **责任人卡片**——`责任人：做什么` 拆成两行，左侧带 01/02 编号 |
| 其余 | 段落 + 列表 |

解析规则依赖 `render_report` 的固定结构（`##` / `###` / `-` / `1.`），**换结构要同步改这里**。

两个踩过的细节：
- 责任人提取要先剥掉 `⚠️`/`🔥` 等前缀符号，否则会跟着进责任人字段；
- 冒号前的名字放宽到 20 字符，`生产计划负责人（李计划）` 这种长抬头才认得出来。

### 8.18 新会话数据卡可点击（2026-11-17）

数据卡原来只是展示数字，点了没反应。现在整块都是可点区域，点了切到对应视图看明细。

| 元素 | 点击后 |
| --- | --- |
| 顶部摘要行 | `#/brief`（今日简报） |
| 需要你拍板 / 逾期未完成等指标 | `#/brief` |
| 待处理待办 | `#/todos`（待办工作台） |
| 底部「接下来：…」 | `#/brief` |

跳转逻辑与 `WorkbenchNav` 同一套：嵌在工作台 iframe 里时改**父窗口** hash（外层会换掉本 iframe），单独打开时整页跳转。

**踩到的坑**：一开始「逾期未完成」指向 `#/floor`，但 `#/floor` 的 `roles` 只有 `supervisor`/`staff`，
管理者点了会被路由挡回来、停在对话页什么都看不到。改成统一指向 `#/brief` 后三种角色都能跳。

可访问性：三个区域都是真正的 `<button>`（不是 div 加 onclick），有 `:focus-visible` 描边，悬停只做轻微底色不做位移。

### 8.19 修复：会话「更多」菜单跑到左上角（2026-11-17）

**现象**：在会话列表点 `...`，重命名/删除菜单出现在侧边栏左上角，而不是按钮旁边。

**根因**：`.action` 是 **只有 hover 才显示**（`display: none` → hover 时 `flex`）。菜单用 `createPortal` 挂到 `document.body`，
鼠标从会话项移到菜单上时，会话项失去 hover，`.action` 收起、其外包的 `.anchor` 塌成 **0×0**；
定位算法读到空矩形，`clampLeft` 把 left 夹到视口内边距，于是落到 `(8, 4)`。

实测证据（修复前）：

```
菜单 style  top: 4px; left: 8px        ← 视口内边距
anchor rect x:0 y:0 w:0 h:0            ← 塌成空矩形
```

**修复**：把 `.action` 移进 `Menu` 的 children 里，用 `open` 状态强制保持可见 —— `Menu` 的 children 回调本来就提供 `{ open, toggle }`。

```css
.actionOpen { display: flex !important; }   /* 菜单打开期间锚点不能塌 */
```

实测证据（修复后）：

```
菜单 style  top: 423.879px; left: 204.913px   ← 跟着被点的会话项走
```

**教训**：用 hover 才显示的元素当浮层锚点时，必须保证「浮层打开期间锚点仍有尺寸」，否则定位算法拿到空矩形，
表现出来就是「菜单跑到角落」而不是报错，很容易被当成样式问题去调 CSS。

### 8.20 视角切换重做：按账号查看（仅管理员，2026-11-17）

**旧实现的根本问题**：三个固定角色（管理者/车间主任/一线员工）切换后，`role` 参数在后端**只用于回显，不影响取数**，
数据永远按当前登录人取。于是切换后「视图可见性变了、数字没变」，看着像权限混乱。

**新做法**：选**具体账号**，后端按那个账号的身份取数。

| 项 | 做法 |
| --- | --- |
| 前端选择器 | 顶栏操作区最右（设置之后），`js/view-as.js`；选中项存 `localStorage.dnx-view-as` |
| 请求传递 | `api.js` 统一带 `X-View-As` 头；chat-demo 的 `TodayPanel` 也单独带（它不走 api.js） |
| 后端解析 | `app/services/view_as.py`：管理员才允许，非管理员传了直接 **403**（不静默忽略） |
| 账号清单 | `GET /view-as/accounts`（管理员），只列系统里真实存在的 AI 账号 |
| 视图可见性 | `currentRole()` 优先用被查看账号的角色，与本人口径分开（`ownRole()`） |

实测：

```
admin 本人               需要你拍板 2
admin 以 worker 视角     需要你拍板 8   ← 数据真的换了
worker 传 view_as=admin  403
```

**顺带修的**：`.pill-btn` 缺 `justify-content: center`，按钮有固定宽度时内容靠左，看着像「字体没居中」；
`line-height` 从 `1` 调到 `1.2`，中文上下留白更均衡。

### 8.21 群聊铺满 + 进群直接到最新（2026-11-17）

**问题一：群聊被挤在中间**

`.view__inner` 实际宽度只有 1080px，`max-width: none` 没生效。原因：

```css
body:not([data-view="chat"]) .view__inner { max-width: 1080px }   /* 特异度 0,2,1 */
.view__inner:has(> .group-stage)   { max-width: none }            /* 特异度 0,2,0 → 输 */
```

`refine.css` 那条带 `body` 前缀的规则赢了。补一条同前缀的覆盖：

```css
body:not([data-view="chat"]) .view__inner:has(> .group-stage) { max-width: none; width: 100% }
```

实测：`.view__inner` / `.group-frame` 宽度 1080 → **2030**，与 `.view` 同宽；左右 padding 也清零。

**问题二：进群从最早一段段滑到最新**

`app.css` 的 `.stream { scroll-behavior: smooth }` 让 `scrollTop = scrollHeight` 变成**平滑动画**：
进群先渲染 80 条，再平滑滚 11207px，用户看到的就是「从头慢慢滑下来」。

修复：`jumpToBottom(node)` —— 临时把 `scrollBehavior` 设成 `auto`，赋值后立刻还原，只影响这次跳转。
两处赋值都换掉（渲染后、发送后）。

实测：进群 0.6 秒时 `scrollTop` 已是 `11207`（= 可滚动最大值），4 秒后保持不变——**一次到位，没有动画**。

### 8.22 群聊消息流性能改造（2026-11-17）

**为什么改**：原实现每次轮询到新消息就 `innerHTML = ''` 全量重建，且 `state.messages` 只增不减。
实测 DOM 节点到 1.3 万时单次重建已 60ms+，每天几十条消息、几个月就会进入明显卡顿区间。

改造两条：

| 项 | 做法 |
| --- | --- |
| **窗口化** | 只渲染最近 `WINDOW_SIZE = 200` 条；更早的用顶部「加载更早消息」按钮按 `PAGE_SIZE = 50` 分页补（后端 `?before=` 已支持） |
| **增量追加** | 轮询到新消息走 `appendMessages()`，只 append 新节点，不碰已渲染的；超过窗口 2 倍才整体重排一次兜底 |

实测（267 条消息的群）：

```
改造前  每次轮询重建全部 267 条（约 1.3 万 DOM 节点）
改造后  渲染 130 行 / 1,166 个 DOM 节点，滚动布局 0ms
```

**已知局限**：在列表中间点「加载更早」，内容会整体下移（视口数值不变），需要用户自己往上滚一下。
在底部时行为正确（跟着最新消息走，同微信）。

这个视口锚定试过 `scrollHeight` 差值、`scrollIntoView`、`rAF` 延迟、`insertBefore` 前插 + `overflow-anchor` 五种写法都没稳定生效，
最终保留「同步滚到顶部」——在底部场景表现正确，中间场景留作后续用虚拟列表一并解决。
（教训：`scroll-behavior: smooth` 会让 `scrollTop` 赋值变异步，同步读回拿到的还是旧值；
调试时要在 `auto` 模式下单独验证滚动本身是否可控，否则会把「赋值没生效」误判成「代码没执行」。）

### 8.23 群聊铺满后「自己的消息不靠右」（2026-11-17）

**现象**：群聊改成铺满容器后，自己的消息气泡停在中右位置，离右边还差一大截，看着不像「我发的」。

**根因**：`app.css` 宽屏媒体查询里有这条：

```css
@media (min-width: 1025px) {
  .stream > * { max-width: 880px; }   /* 限的是整行，不是气泡 */
}
```

本意是限制长行可读性，但它限的是 **`.msg-row` 整行**。行被压到 880px 且左对齐，
`row-reverse` 的气泡只能贴到 880px 的右边，距真正的容器右边缘还有 (1430-880)/2 ≈ 275px。

**修复**：取消整行限宽，只保留气泡自身限制（`.msg-col` 本来就有 `max-width: min(560px, 76%)`）：

```css
@media (min-width: 1025px) {
  .stream > * { max-width: 100%; }
}
```

实测：行宽 880 → **1373**（撑满容器）；自己的气泡距行右边缘 36px（头像位），AI 气泡距行左边缘 36px，左右对称。

**教训**：给「整行」加 max-width 会影响对齐类的布局（`row-reverse`、`justify-content: flex-end`）——
要限阅读宽度就该限气泡，不要限行。

### 8.24 身份泄漏修复 + 联网检索（2026-11-17）

#### ① 模型自曝「该助手为DeepSeek」，用户从没说过

**根因**：chat-demo 的 `services/chat/store.ts` 里有一条固定 SYSTEM_PROMPT：

```ts
const SYSTEM_PROMPT = '该助手为DeepSeek，由深度求索公司创造。'
```

它每次请求都放在 Anthropic 的 `system` 字段里，而 `anthropic_compat._to_openai_messages()` 会把它原样转进对话。
后端身份提示词又说「不要自称 DeepSeek」——两条对冲，模型自己「圆」出了
「上一轮对话里夹了一句…」这种用户根本没说过的话。

**修复三处**：

| 位置 | 改法 |
| --- | --- |
| `store.ts` | `SYSTEM_PROMPT` 置空，身份由后端统一注入 |
| `_to_openai_messages` | **不再收 `body.system`**，只用 `_identity_prompt` |
| `_identity_prompt` | 不点名任何厂商：一提名字，模型就反复解释「我不是 XX」 |

#### ② 联网检索

新增 `app/services/web_search.py` + `chat_tools` 的 `web_search` 工具。

| 项 | 做法 |
| --- | --- |
| 源 | 百度为主、搜狗备用（DuckDuckGo 回 202，Bing cn 站解析不出结果节点） |
| 取数 | 只取标题/摘要/链接，不抓正文 |
| 缓存 | 同 query 5 分钟内复用结果 |
| 冷却 | 某源失败后 180 秒内跳过它，换下一个源 |
| 失败 | 抛 `WebSearchError`，作为工具文本回给模型，由它如实说明 |

提示词同步改成：「需要实时信息或站外资料时**先调 web_search**，不要凭记忆回答，也不要声称自己没有联网能力。」

**已知限制**：百度对连续请求限得很紧（实测连打两次就回 1438 字节的空壳）。
有缓存与双源兜底，但高频使用仍可能遇到「检索暂时不可用」——这时模型会说明失败并给替代路径，不会编造。

### 8.25 联网检索（重做，2026-11-17）

#### 为什么重做

第一版用百度 + 搜狗爬 HTML，**交付时没做长时间稳定性验证**：
实测连打两次就回 1438 字节空壳，用户看到的是「联网检索暂时不可用」。这不可接受。

#### 各源实测结论（都跑过，不是猜的）

| 源 | 结果 |
| --- | --- |
| **360 搜索** | ✅ **主源**。中文相关度高、摘要能解析、连续 4 个不同查询全部成功（0.3–1.1s） |
| 百度 | ⚠️ 备用。结构稳定但限流极紧，连打两次就空壳 |
| 搜狗 | ❌ 同样限流 |
| DuckDuckGo | ❌ 直接回 202（反爬验证） |
| Bing 国际站（走代理） | ❌ **更糟**：能返回 10 条结果，但内容是反爬降级后的无关结果（搜「装修」返回「ANSYS」「比例的意义」），会误导模型 |
| SearXNG 公共实例 | ❌ 超时或返回空壳 |

#### 现在的实现

| 项 | 做法 |
| --- | --- |
| 源 | 360 为主、百度备用；失败源冷却 180s |
| 缓存 | 同 query 5 分钟内复用（第 2 次 0.0s） |
| 解析 | `<li class="res-list">` 块取 `h3` 标题 + 其后文本作摘要 |
| 失败 | 抛 `WebSearchError` → 工具文本 → 模型如实说明 |

#### 端到端验证（对话里）

```
问「2026 年光伏新增装机容量大概多少」
→ 模型调 web_search
→ 回答：中国 180–240 GW（官方口径）；行业内部估计 150–200 GW
        户用 30 + 工商业分布式 60 + 大基地 60；来源：新浪财经 2025-11-29
```

问「2026 年装修行业市场规模」→ 答「博研咨询口径：2025 年约 5.53 万亿、2026 年约 5.8 万亿」，标注来源。

#### 教训

爬 HTML 做搜索本质上是不可靠的：来源会限流、会降级、会回无关内容。
**能用正经 API 就别爬**；真要用爬虫，必须做「连续多次请求」的稳定性验证，而不是一次成功就交付。

### 8.26 联网检索改为百炼搜索增强（2026-11-17）

#### 思考过程：能不能用 DeepSeek 的 key 做搜索

**不能。实测证据**（不是猜的）：

```
POST api.deepseek.com/v1/chat/completions
  tools: [{"type": "web_search"}]   → 422 unknown variant `web_search`, expected `function`
  enable_search: true               → 200，但模型自述「无法实时访问」，参数被静默忽略
```

DeepSeek 的「联网搜索」只存在于官方 App / 网页版，**API 层不开放**。它的 key 只能用来跑模型，不能用来搜。

#### 最终方案：阿里云百炼搜索增强

用户已有的 `LOCAL_VLM_API_KEY`（原本给组织架构图视觉解析用）**可直接用于 qwen 的搜索增强**：

```python
parameters = {"enable_search": True, "search_options": {"forced_search": True, "search_strategy": "pro"}}
```

| 项 | 做法 |
| --- | --- |
| 主通道 | 百炼 qwen-plus + enable_search，**直接返回带来源的结论**（不是标题列表） |
| 兜底 | 360 抓取（标题/摘要/链接），百度备用 |
| 配置 | `SEARCH_API_KEY`（回落到 `LOCAL_VLM_API_KEY`）、`SEARCH_API_URL`、`SEARCH_API_MODEL` |
| 缓存 | 同 query 5 分钟 |

#### 端到端验证

```
问「2026 年装修行业市场规模」
答：家居家装整体约 21000 亿、装修服务约 3000 亿、局改约 5400 亿
    来源：中国家居家装行业白皮书 2026（2026-09）

问「2026 年光伏新增装机容量」
答：中国 180–240 GW（CPIA 路线图第十版，2026-02）
    全球 612 GW（SolarPower Europe 2026-07）
    2025 年国内 316.57 GW，2026 同比回落是大概率
    并提醒「2026 全年数字现在都是预测」
```

#### 教训

「用 A 家的 key 做 B 家的事」要先验证 A 到底有没有这个能力。
DeepSeek 的 API 没有搜索工具，但它的 key 在模型调用上完全正常——两者不能混为一谈。

### 8.27 联网检索改用 AnySearch（2026-11-17）

#### AnySearch 是什么

`https://api.anysearch.com/mcp` —— **MCP 协议**（JSON-RPC），不是 REST。
工具：`search` / `batch_search` / `extract` / `get_sub_domains` / `score`。
鉴权：`Authorization: Bearer <key>` + `X-Anysearch-Client`。

调用要点：**每次请求前先 `initialize` 握手**，否则 `tools/call` 会被拒。
返回是 SSE 或纯 JSON，要按 `data:` 行取。

#### 接入后的效果（3.1 秒，6 条带 URL + 日期）

```
问「2026 年光伏新增装机容量」
→ 大全能源：全球 500–667 GW，中国 180–240 GW（2026-06-18）
→ 新浪财经：CPIA 预测 180–240 GW；BNEF 直流 321 / 交流 264 GW（2026-02-05）
→ 人民网：截至 2026-05 新能源装机 19.2 亿千瓦（2026-07-27）
```

源优先级现在是：**AnySearch → 360 → 百度**；百炼搜索增强作为 `answer()` 的第二级兜底。

#### 顺带修的一个严重问题：公开数据被脱敏毁掉

第一版接上后模型回答「具体金额被脱敏成了占位符〔MONEY_1〕」——`data_guard` 的 MONEY 规则
（`\d+元|万元|万|块|%`）把公开网页里的「2.68万亿」当业务金额打码了。

**修法**：`plain_llm._mask_payload_keeping_tools()` —— 脱敏 payload 但**保留 `role=tool` 的消息原样**。

理由：工具结果要么是公开网页信息，要么是已经按账号权限裁剪过的 MES 数据，
再套一层 PII/金额脱敏只会把可用信息毁掉。系统内部数据的安全性由 MES 工具链自己保证（按 token 裁面板），不依赖这层。

修复后同一问题拿到：**2.68–3.15 万亿元**（《2026 年中国家装行业市场分析报告》，搜狐 2026-04-20）。

### 8.28 搜索引擎配置进「模型配置」页（2026-11-17）

联网检索原来只能改 `.env.local`，换服务商要重启进程。现在和语音助手一样进管理界面。

#### 后端

| 文件 | 作用 |
| --- | --- |
| `app/services/search_config.py` | 照 `voice_config` 的模式：库里覆盖值 → 回落 settings；写入时落库 + **立刻回写 settings 单例**（不用重启） |
| `app/api/search_config.py` | `GET/PATCH /admin/search`、`POST /admin/search:test`，全部 `admin_user` |
| `main.py` lifespan | 启动时 `search_settings.apply_overrides(db)` |

配置项：`anysearch_api_key`、`anysearch_api_url`、`search_api_key`、`search_api_model`、`search_enabled`。
密钥类接口**只回「是否配置」+ 前 6 位**（`as_sk_…`），永不回明文。

#### 前端

`#/models` 页现在三张卡：**模型端点 / 联网检索 / 语音助手**。
联网检索卡有：启用开关、服务地址、API Key（留空表示不改）、备用模型、保存、连通自检。

#### 踩到的坑

`from app.services import search_config` 和 `from app.api import ... search_config` **同名冲突**，
`app.include_router(search_config.router)` 会找到 services 那个模块报 `no attribute router`。
改成 `from app.services import search_config as search_settings`。

#### 验证

```
GET  /admin/search        → anysearch_api_key: configured, hint "as_sk_…"
POST /admin/search:test   → 真实检索成功，返回 6 条结果
PATCH search_enabled=false → 落库 search_search_enabled=1，改回 true 生效
```

### 8.29 AI 发现分类芯片改成筛选器（2026-11-17）

原来那排芯片（生产 9 / 仓储 3 / 销售 5 / 采购 2 / 质量 1 / 审批 6）是纯展示的 `span`，点不动。
现在每个都是 `<button>`，点一次只看该域，再点回到全部。

| 项 | 做法 |
| --- | --- |
| 状态 | `state.domainFilter`（空串 = 全部） |
| 选中态 | `.is-on` + `aria-pressed`，底色 `--ant-color-primary-bg` + 主题色描边 |
| 重置 | 有筛选时才出现「全部」按钮（虚线边框，弱化一档） |
| 计数 | 未筛选显示「共 N 条」，筛选后显示「已筛出 N 条」 |
| 失效保护 | 筛选的域没数据了，自动回落到全部（否则会看到空列表） |
| 命中区 | 26px → **32px**（可点之后按项目控件标准抬高） |
| 窄屏 | `.todo-events__summary` 本来就 `flex-wrap: wrap`，768 以下「共 N 条」独占一行 |

实测：点「仓储」→ 只剩 `warehouse` 域、芯片高亮、出现「全部」、总数变「已筛出 3 条」。

#### 踩到的坑

生成代码时多带了一个 `',`（`},',`），页面直接白屏报 `Invalid or unexpected token`。
**`node --check` 没能拦住它** —— 所以前端改动必须真的在浏览器里跑一次，语法检查不等于能执行。

### 8.30 芯片选中态边界线（2026-11-17 用户反馈）

> 「这样子在浅色模式下观感不是很好，有点费眼睛，因为没有明显的一个边界线」

#### 根因：两个 token 都是浅色，叠在白底上等于没有线

| 状态 | 原来用的 token | 浅色实际值 | 问题 |
| --- | --- | --- | --- |
| 未选中 | `--ant-color-border-secondary` | `#f0f0f0` | 白底上几乎不可见 |
| 选中 | border `--ant-color-primary-border` + bg `--ant-color-primary-bg` | `#b8d3ce` / `#e8f1ef` | 两个浅绿叠在一起，没有轮廓 |

#### 改法

| 状态 | 现在 | 浅色实际值 |
| --- | --- | --- |
| 未选中 | `border-color: --ant-color-border` | `#d9d9d9`，立得住线但不抢眼 |
| 选中 | border + `box-shadow: inset 0 0 0 1px` 同色 | `#116a5b` **等效 2px 实线**（不改布局） |
| 选中文字 | name / count 转 `--ant-color-primary` | `#116a5b` |

另外：tone 的边框色（accent/warn/danger）改成只在 `:not(.is-on)` 时生效，
否则会盖掉选中态的主色 —— 原来 `[data-tone]` 的优先级和 `.is-on` 一样，后写的赢。

暗色下 `--ant-color-primary: #58b09e`、`--ant-color-border: #424242`，同一套规则自然成立，已截图确认。

### 8.31 简报/待办字号与 chat 对齐（2026-11-17）

> 「这些简报待办的字体和 chat 会话字体对齐，要清晰」

#### 先测，再改

字体族两边**本来就一致**（都是 `system-ui / -apple-system / PingFang SC`），差异全在字号：

| 位置 | 改前 | 改后 | chat 参照 |
| --- | --- | --- | --- |
| 简报条目描述 | 13px/21px | **14px**/21px | 正文 16px，小字 14px |
| 待办条目标题 | 13px/20px | **14px**/22px | — |
| AI 发现域标题 | 13px/20px | **14px**/22px | — |
| 各类辅助字 | 11px / **10px** | **13px**/22px | — |
| 最小字号 | 10px | **12px** | 最小 14px |

用户选定档位：**正文 14，辅助 13，最小不低于 12**（不做 16px —— 工作台是密集列表，
放大到 16 会让一屏可见的待办少一半）。

#### 一并修的可读性问题

`--ant-color-text-quaternary` 在浅色下是 `#b3bfba`，白底上对比度约 **1.9:1**，
远低于 WCAG AA 的 4.5:1 —— 那些「看着费劲」的小字不少是这个造成的。
正文场景一律降级到 `text-tertiary`（`#8b9893`，约 3.1:1）。

#### 范围

`brief.css` / `todo.css` / `events.css` / `refine.css` 四个文件。

**`refine.css` 是后加载的覆盖层**，一开始只改了前三个，`.todo-stat__foot` 还是 12px ——
因为 `refine.css` 里有同名规则且加载更晚。改样式时必须连带检查覆盖层。

#### 验证

```
浅色 1440：简报正文 14/24、条目备注 13/22、待办标题 14/22、
          统计脚注 13/22、发现总计 12/22；文档无横向溢出
浅色  375/768/1440：无溢出、无裁切
四个文件已无 10px / 11px
暗色：配色未改动（只改字号与文字 token 引用），视觉复核见下方说明
```

#### 暗色的结论（已验到能验为止）

**字号**：暗色下与浅色完全相同 —— 实测 `theme=dark` 时待办标题 / 发现域标题 / 芯片都是 14px/22px，
且 `tokens.css` 的暗色块里只有 `--ant-font-size: 14` 这类 token 定义，没有任何 `font-size` 覆盖。

**配色**：暗色截图里统计卡与待办列表的**文字仍是深色**，在深色背景上对比度很低。

这不是本次改动引入的 —— 本次只动 `font-size` / `line-height` / `--ant-color-text-quaternary` → `tertiary`，
没有碰任何 `background` 或深色字面量；三个文件里也没有硬编码的深色文字（只有 `refine.css` 两处
`rgba(0,0,0,.02)` 的 hover 底色）。

**但也要说清楚**：`frontend-desk/css/` 是未跟踪目录，没有 git 基线可比，
所以「这是既有问题」是我的推断，不是用 diff 证明的事实。

想查清需要单独一轮：定位那块浅底容器是谁（探针在 `.stat` / `.todo-stat` 上取元素失败，
说明实际类名不同），再决定是补 token 还是改容器。**没做，等你决定要不要修。**

### 8.29 每轮耗时（隐式显示，2026-11-17）

回答末尾一个灰色小字 `9.4s`，点开看明细：`模型 5.8s · 检索 3.6s · 2 轮 · 检索 2 次`。

#### 数据怎么走

```
后端 _run_with_tools 计时
  → usage.timing { total_ms, model_ms, tool_ms, rounds, searches }
  → SSE message_delta
  → 前端 parseEvent（isUsage 透传）
  → message.usage
  → <TurnTiming usage={message.usage} />
```

**为什么塞进 usage**：`parseEvent.isUsage` 只校验 4 个必需字段是数字，**不裁剪多余键**，
所以自定义的 `timing` 能原样透传，不用改协议、不用加接口。
`server_tool_use.web_search_requests` 是协议原生字段，顺手把检索次数填上。

#### 踩到的坑（两个）

1. **usage 必须四字段齐全**。`isUsage` 要求 `input_tokens` / `cache_creation_input_tokens` /
   `cache_read_input_tokens` / `output_tokens` 都存在且是数字，**少一个整段 usage 被丢弃**，
   表现为「小字永远不出现」。第一版只给了 `output_tokens`，白调了两轮。
2. **重构函数体时误删了 `tools = _openai_tools()`**。后果很隐蔽：模型调用抛 `NameError`，
   被 `except Exception` 吞成「模型调用失败：…」，前端当成正常回答显示（只有 17 token）。
   **教训**：整段替换函数体后必须跑一次真实请求，看回答内容而不只看 HTTP 200。

#### 设计取舍

- 默认只显示总秒数：诊断信息不该盖过回答本身；
- 点开才给明细：需要排障时够用，不需要时不打扰；
- 数字用 `tabular-nums`：多轮对比时列不跳。

### 8.32 对话里的报表可点击查看（2026-11-17）

> 「这个对话让生成的周报应该是可以在会话中点击查看的」

#### 为什么原来点不了

`chat_tools.generate_period_report` 只返回纯文本「已生成《MES 周报 …》，可在对话里查看。」
——**标题是普通文字**，没有任何可点入口。

#### 三层链路

| 层 | 做法 |
| --- | --- |
| 后端 | 工具返回 `已生成《[标题](nxs-report:weekly)》，点击标题即可查看。` + 「请原样输出」 |
| chat-demo | `Link.tsx` 在 `sanitizeUrl` **之前**拦 `nxs-report:` 前缀，渲染成 `<button>`，点击 `window.parent.postMessage({type:'nxs-report', kind}, '*')` |
| 工作台 | `chat.js` 监听 `message` → `BRIEFING.board()` 找到该 kind 的 sessionId → `openReport()` 打开抽屉 |

#### 三个设计取舍

**为什么用伪协议不用 http 链接**：报表正文存在后端会话里（按 sessionId 取），
不是独立页面；做成链接只会跳 404。

**为什么拦在 `sanitizeUrl` 之前**：那个函数只放行 `http/https/mailto`，
`nxs-report:` 会被清成 `undefined`，链接变成不可点的纯文本。

**为什么监听要能卸载**：`mount` 可重复调用，不卸载会叠加多次监听，
点一次开好几个抽屉。所以 `installReportBridge` / `uninstallReportBridge` 配对。

#### 验证

```
后端：POST /anthropic/v1/messages「生成本周周报」
     → 回复含 nxs-report: 链接（模型原样保留）✓

渲染：对话里标题变成 <button>，文本「《MES 周报 2026-11-16 周》」
     nxs-report 锚点残留 0 ✓

打开：从 chat iframe 向父窗口 postMessage({type:'nxs-report',kind:'weekly'})
     → 工作台右侧滑出 .drawer.report-view
     → 显示周报正文（概览 14 新发现 / 8 处理 / 25 积压 / 1.2 平均关闭耗时、
       新增分布、已处理、待办分布）✓
```

#### 一处没说满的

「点击按钮」这个动作本身没有单独回放过 —— 按钮渲染和 postMessage 接收端各自验过，
中间那段就是 `onClick={() => window.parent?.postMessage(...)}` 一行。
画面里的抽屉是从 chat iframe 真实发出 postMessage 打开的，不是直接调 `openReport` 造的。

### 8.30 AI 用量与额度（2026-11-17）

管理员看「每个账号每月花了多少钱」，并给账号设月度额度；超额后对话被拒。

#### 口径（与用户确认）

| 项 | 决定 |
| --- | --- |
| 统计范围 | **只算对话**（chat）。群聊/语音/报表暂不计入 |
| 超额处理 | **拒绝并提示**，管理员可调高或临时放开 |
| 单价 | DeepSeek 公开价，界面可改 |

#### 数据链路

```
plain_llm.complete_with_tools  现在返回 (text, calls, usage)   ← 以前把上游 usage 丢了
  → _run_with_tools 逐轮累加 tokens_in / tokens_out / tokens_cache_read
  → ai_usage.record_usage(...)  按 pricing.calculate_cost 算钱、存单价快照
  → ai_usage_log（user_id + biz_month 索引）
```

**为什么不用现成的 `budget_*` 表**：那套是给视觉/记忆/日程做的「operation 级预留-结算」
（按 operation_id 记账、带 hold_until 租约），天生为并发保护设计；
而「每人每月花了多少」要的是能按月分组求和、一把查全员，用它既重又绕。
计价仍然复用 `pricing.calculate_cost()` 和 `model_prices`，不另造规则。

#### 额度语义

| `monthly_limit_cny` | 含义 |
| --- | --- |
| 没有该账号的行 / NULL | 不限 |
| 0 | **禁止使用**（提示「暂未开通 AI 使用权限」，和「花完了」分开说） |
| > 0 | 每月上限，花到即拒 |

校验在 `/anthropic/v1/messages` 开头，未超额才继续——超额不浪费一次模型调用。
记账失败不影响回答（`try/except` 包住），用户不该因为统计出错看不到回复。

#### 两个真实 bug（都是这次排查出来的）

1. **`_error_frame` 对非流式请求返回 SSE**。规则拒绝时，`stream:false` 的调用方拿到一段
   解不开的文本。已改成按 `want_stream` 分别返回 JSON / SSE。
2. **`serve-ai-desk.mjs` 的 `<base href="/ai/">` 写死**。desk 直接访问 `:18092/` 时，
   所有相对路径变成 `/ai/js/*` → 404 → **模块根本不加载 → 白页**。
   表现极具欺骗性：页面骨架在、接口全 200、控制台无报错，因为 `<script type="module">` 压根没执行。
   已改成按请求前缀动态给 base；同时把静态文件的 `cache-control` 从 `no-cache` 改成 `no-store`
   （没有 ETag 时 `no-cache` 等于强制用旧文件，改了代码刷新看不到效果）。

#### 界面

`#/ai-usage`（`js/ai-usage.js`，菜单「AI 用量」，管理员专属）：账期选择、合计、
每账号「调用/输入 token/输出 token/花费/额度」，点「设额度」弹窗改（留空＝不限，0＝禁止）。

**颜色用 `--ant-color-text`**（`theme-mes.css` 里浅色 #1e2b27 / 深色 #e2e8e5），
不要用中性灰——深色主题下对比度不够，看着像没内容。

### 8.31 外观统一跟随 chat（2026-11-17）

#### 问题

工作台自己有「浅色/深色」开关，chat 会话也有一套，两边各记各的，
在对话页和工作台之间切换就会出现「聊天是深的、待办是浅的」，看着像两个产品。
侧栏配色也和 chat 不一致（工作台是制造绿，chat 是中性灰）。

#### 做法

**① 去掉工作台的外观开关**：顶栏那个圆角按钮已删除，`cycleTheme()` 一起删。

**② 只认 chat 的偏好**：chat 存在同源的 `chat-demo:theme-preference`（light / dark / system），
工作台直接读，并监听 `storage` 事件实时跟随；`system` 时再跟系统配色变化。

**③ 侧栏抄 chat 的实际取值**（浏览器里量的，不是猜的）：

| | 深色 | 浅色 |
| --- | --- | --- |
| 侧栏底色 | `#1b1b1c` | `#f9fafb` |
| 画布 | `#151517` | `#ffffff` |
| 右边框 | `rgba(255,255,255,.06)` | `rgba(0,0,0,.04)` |
| 正文 | `#f2f3f5` | `#0f1115` |

落成 `--desk-rail-bg / --desk-rail-text / --desk-rail-hover / --desk-canvas` 一组变量，
按 `[data-theme="dark"]` 切换。只换颜色，侧栏宽 260px、内边距 16px 12px 不动。

工作台自有的制造绿主题仍作用于内容区，侧栏跟随 chat 的中性色。

#### 验证

```
深色  rail rgb(27,27,28)  border rgba(255,255,255,0.06)  body rgb(21,21,23)
浅色  rail rgb(249,250,251) border rgba(0,0,0,0.04)     body rgb(242,245,244)
切 chat 为 dark → desk 立即 dark，rail 同步变 #1b1b1c
顶栏不再有外观按钮（btnTheme 从 index.html 与 main.js 移除）
```

#### 踩到的坑

批量替换 `setTheme(themeChoice());` 时把**函数体内**那一行也换了，
导致 `followChatTheme()` 自己调自己（无限递归）。
教训：整段替换前先确认目标字符串在文件里出现几次，别用全局替换改函数体。

### 8.32 侧栏入口顺序（2026-11-18）

「AI 用量」原来只在工作台（desk）的 rail 里，chat 会话侧栏没有入口。
现在加在 chat-demo 的 `WorkbenchNav`，顺序为：

```
简报 → 待办 → 群聊 → 员工画像 → 模型配置 → AI 用量 → 会话归档
```

成本管理紧挨着模型配置，符合「先配模型、再看花了多少」的使用顺序。

入口与 `archive` 一样受 `isMesAdmin()` 控制：非管理员看不到（后端也 403）。
图标用柱状图意象（`ICONS.usage`），不跟「模型配置」的齿轮混。
文案 `wbAiUsage` 中英都补了（`TranslationKey = keyof typeof zh`，en 缺键会报类型错）。
### 8.4 对话页 = DeepSeek 官方 chat-demo（2026-11-17 起）

`js/chat.js` 现在只是一个 iframe 壳，真正的界面是 `frontend-group/chat-deepseek/`
（官方 `deepseek-ai/chat-demo` 的构建产物，MIT，保留 LICENSE）。

| 关注点 | 说明 |
| --- | --- |
| key 从哪来 | 浏览器**不填 key**。chat-demo 请求头 `x-api-key` 带的是 MES 登录 token（`index.html` 里注入），后端 `POST /anthropic/v1/messages` 验它，再用「模型配置」里保存的真 key 调上游 |
| 协议 | chat-demo 用 Anthropic Messages 协议；后端 `app/api/anthropic_compat.py` 做 Anthropic↔OpenAI 转换（SSE 事件：message_start / content_block_delta / message_stop） |
| 会话存储 | chat-demo 自己存浏览器 IndexedDB，**与后端会话列表不互通**（2026-11-17 确认按此执行：先各自独立）。将来要共享，改它的 `src/services/chat/db.ts` 即可 |
| 身份 | 模型默认会自称 DeepSeek。后端在 `anthropic_compat.py` 里注入身份提示词（名字读 `brand_assistant_name`，默认「小智」），并明确要求不以 DeepSeek 自称、不提模型厂商 |
| 重新构建 | 源码在 `frontend-group/chat-deepseek-src/`（见其 README-local.md）：`rspack build -c build-local.ts`，`apiOrigin` 注入 `/ai-api`；Node 24 下原生模块加载失败，用 Node 22 |

### 8.7 非对话视图：无外侧导航 + 返回按钮 + 对话风格

侧边栏入口搬进对话页之后，其余视图（简报/待办/群聊/员工画像/模型配置）的定位变了：**它们是被对话唤起的子页面，不是独立导航目的地**。

| 项 | 做法 |
| --- | --- |
| 外侧导航 | `body[data-view] .rail { display: none }` —— 所有视图都不再显示 rail |
| 返回入口 | 顶栏左侧 `.back-chat`「← 返回对话」（`main.js` 的 `renderBackToChat()` 注入，对话页自身不渲染） |
| 顶栏 | 对话页隐藏（它有自己的侧边栏头部）；其它页显示 56px 窄顶栏，副标题隐藏 |
| 视觉 | 页面底 `#f7f8fa`、卡片圆角 16 / 无阴影 / 极淡边框、指标数字 30px、标签改淡底无边框、列表行高放松 |

覆盖层都写在 `css/desk.css` 末尾，只改观感、不动业务结构；深色主题继承 `--ant-*` 变量。
### 8.6 对话页是全屏首屏（2026-11-17 起）

进工作台**直接落在 `#/chat`**，且对话页**不显示外侧 rail** —— 简报/待办/群聊/员工画像/模型配置已经搬进对话自己的侧边栏。

| 机制 | 说明 |
| --- | --- |
| 落点 | `VIEWS` 里只有 `chat` 带 `landing: ['manager','supervisor','staff']`，其余视图不再抢落点 |
| rail 隐藏 | `render()` 把当前视图写到 `document.body.dataset.view`，CSS 用 `body[data-view="chat"] .rail{display:none}` 并让 `.desk` 变单列 |
| 返回其它视图 | 其它视图仍保留 rail（实测切到 `#/todos` 后 rail 恢复 `flex`），作为返回导航 |
| 内边距 | 对话视图下 `.view{padding:0}`，会话界面贴边 |
### 8.5 对官方 chat-demo 做了哪些改动（重建必读）

`frontend-group/chat-deepseek/` 是构建产物；**源码在 `frontend-group/chat-deepseek-src/`**（含本文改动，附 `README-local.md` 重建步骤）。改动清单：

| 改动 | 文件 | 作用 |
| --- | --- | --- |
| 页头替换为「时间 · 账号名」 | `src/components/sider/WorkbenchHeader.tsx` + `Sider.tsx` | 原产品名「API 使用 demo」改为 `HH:mm · 账号名`（读 `localStorage.mes_user` 的 realName/displayName/userName），右侧小字是 `MM-DD`；每 30 秒刷新 |
| 新增工作台入口组件 | `src/components/sider/WorkbenchNav.tsx` | 侧边栏顶部放「简报/待办/群聊/员工画像/模型配置」（仿 DSH 侧边栏）；点击时若在 iframe 内则改父窗口 hash 切视图，独立页则跳 `/ai/#/xxx` |
| 接入侧边栏 | `src/components/sider/Sider.tsx` | 在「开启新对话」按钮之上渲染 `<WorkbenchNav>` |
| 样式 | `src/components/sider/Sider.module.css` + `.d.ts` | `.wbNav/.wbNavItem/.wbNavIcon/.wbNavLabel`，复用 `--dsw-*` 变量 |
| 文案 | `src/i18n/zh.ts`、`en.ts` | `wbNavLabel/wbBrief/wbTodos/wbGroups/wbStaff/wbModels` |
| 登录态注入 | 构建后的 `index.html` | 把 `chat-demo:api-key` 预置为 MES token（**构建会覆盖，部署后要重新注入**） |

构建命令（Node 22；Node 24 下 rspack 原生模块加载失败）：

```bash
cd chat-demo
NODE_ENV=production ~/.local/node/bin/node node_modules/@rspack/cli/bin/rspack.js build -c build-local.ts
# build-local.ts: createConfig({ apiOrigin: '/ai-api' })
```
**事故记录（必读）**：替换 `js/chat.js` 时，这个目录**从未进入 git**（`frontend-desk/` 整体 untracked），
备份命令 `git show HEAD:...` 返回空，导致 0 字节的 `chat-classic.js`，原 1204 行对话页被覆盖且无法恢复
（APFS 快照 0 个、Time Machine 未配置、无编辑器历史），当时生成的 0 字节 `chat-classic.js` 也已按用户决定删除，
不再保留回退副本。**教训：覆盖源码前先 `git status` 确认跟踪状态；未跟踪的文件必须复制成真实副本再改。**

### 8.3 嵌入模式（`?embed=1`）：避免同一屏出现三套头部

同源之后页面会层层嵌套（MES 外壳 → 工作台 → 群聊页），每层各带一条顶栏就会「标题出现三次、搜索框两个」。约定：

- 被外层承载时，内层 URL 带 `embed=1`；内层据此**隐藏自己与外层重复的那条头部**，其余内容不动。
- 工作台：`html[data-embed="1"] .main > .topbar { display: none }`（**保留左侧 rail**——视图切换要靠它）。
- 群聊页：`html[data-embed="1"] header.topbar { display: none }`。
- 参数由承载方负责加：MES 的 `/assistant` → `/ai/?embed=1`；工作台群聊视图 → `group/?embed=1`。

### 8.4 自检命令（改完跑一遍）

```bash
cd doc-nexus-ai/frontend-desk
# 应为 0 命中：JS/HTML 里不应存在以 / 开头的内部路径
# （API_ROOT 的定义与注释里的反例说明属正常，用 -v 排除）
rg -n "['\"]/(group|pod|api|icons|css|js)|href=\"/|src=\"/" js/ index.html \
  | rg -v 'API_ROOT =|为什么不能直接写|<base href' || echo 'OK：无绝对内部路径'

# 同源三连（都应 200）
for p in /ai/ /ai/css/desk.css /ai/js/main.js /ai/group/ ; do
  printf '%-22s ' "$p"; curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:4173$p"
done
```

