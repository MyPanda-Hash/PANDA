# AI 数字员工 · 资产分离与重塑方案

| 属性 | 内容 |
|---|---|
| 文档类型 | 重构方案（分析 + 决策） |
| 适用场景 | 新版 AI 数字员工前端（待办工作台）开发与验收 |
| 维护状态 | 生效 |
| 最后整理 | 2026-11-16 |

## 0. 一句话结论

`doc-nexus-ai` 现在是**三层拼起来的**：厂商的 React 编译产物（不可维护）、厂商的 FastAPI 后端主体（可调用不可改）、我们自己带源码的群聊/语音/待办/MES 联动（可维护）。
所以不再改旧界面，新建 `frontend-desk/`（无构建、直接用 Ant Design 设计库取值）承载「**以待办为中心的对话 + 语音一体工作台**」，旧界面 `:18091` 原样留着当参照物，等新界面稳定后再退役。

## 1. 三层资产图

```
浏览器
 ├─ :18091 旧融合界面
 │   ├─ frontend/dist/          ← 厂商 React 产物（3.2 MB / 56 个 chunk，无 src）
 │   │   └─ assets/dnx-*.{js,css} ← 「翠花 AI 界面优化层 v5」运行时补丁，也躺在 dist 里（无源码）
 │   └─ frontend-group/         ← 我们写的原生 ES 模块（群聊/语音/通知/待办球/导航注入）
 │       └─ serve-ai-web.mjs     ← 我们的静态服务 + /api 反代 + 注入
 └─ :18092 新工作台（本次新建）
     └─ frontend-desk/           ← 无构建、原生 ES 模块、设计库 token
         └─ serve-ai-desk.mjs     ← 静态 + /api 反代 + /pod 同源 + WS 隧道 + 截图壳

FastAPI :8000
 ├─ 厂商主体：chat(10,259) documents(2,996) writing(2,130) schedules(622) admin(539) harness/pi_runtime…
 └─ 我们自研：群聊 / 待办 / AI 主动监控 / 语音 / 通知 / MES 身份（合计约 8,300 行，见 §4）
```

## 2. 归属清单 · 前端

| 界面/能力 | 实现位置 | 归属 | 证据 | 处置 |
|---|---|---|---|---|
| 主对话页 `/chat` | `dist/assets/ChatPage-*.js` | 厂商 | 编译产物、无源码 | 重写（新 `chat.js`），交互参考不抄代码 |
| 首页 `/` | `dist/assets/HomePage-*.js` | 厂商 | 同上 | 不做，被「待办工作台」取代 |
| 管理后台 `/admin` | `dist/assets/AdminPage-*.js` | 厂商 | 同上（含 LinkMind 计费字样） | 不做，需要时用旧站点 |
| 定时任务 `/schedules` | `dist/assets/SchedulesPage-*` 等 5 个 chunk | 厂商 | 同上 | 不做 |
| 写作 `/writing` | `dist/assets/Writing*` 6 个 chunk | 厂商 | 同上 | 不做 |
| 模板 / 产物 / 空间 | `TemplatesPage`、`ArtifactsPage`、`SpaceDetailPage` | 厂商 | 同上 | 不做 |
| 用量 `/usage` | `UsagePage-*.js` | 厂商 | 同上 | 不做 |
| 界面润色层 | `dist/assets/dnx-ui-polish.{js,css}`、`dnx-exec-motion.css` | 第三方补丁（「翠花 AI 界面优化层 v5」） | 文件内自述；被 `index.html` 第 48–50 行引用 | 丢弃：新界面自己就是源码，不需要运行时打补丁 |
| 项目群聊 `/group` | `frontend-group/app.js`(2,954) + `app.css`(82 KB) + `index.html` | **我们** | 全中文过程注释、无构建 | 保留运行；待办列表/抽屉逻辑迁进新界面后，群聊只留群内协作 |
| 实时语音窗 `/pod` | `frontend-group/pod/{pod.js,pod.css,index.html}` + `audio-wav.js` | **我们** | 自述协议与踩坑（webm 不被 ASR 接受） | **直接复用**：新界面用同源 iframe `/pod/?embed=1` 嵌进对话页与语音页 |
| 导航注入 | `frontend-group/nav-inject.js` | 我们 | 自述「原前端没有源码，只能克隆导航项」 | 丢弃：新界面有自己的导航 |
| 通知气泡 | `frontend-group/notify-bubble.js` | 我们 | 注入三处（18091/group/pod） | 逻辑保留，新界面用 `NOTIFICATIONS` 接口重画 |
| MES 待办悬浮球 | `frontend-group/mes-todo-pod.js` | 我们 | 自述「人在 MES 里不会切过去看」 | 保留（MES 页面内用），新界面是它的全量版 |
| 组织架构 | `frontend-group/org-view.js` + 群聊人力地图 | 我们 | 有 node 单测 `tests/org-view.test.mjs` | 保留 |
| 静态服务/反代 | `serve-ai-web.mjs` | 我们 | 自述复刻 nginx.conf | 复制思路，新写 `serve-ai-desk.mjs`（:18092） |

## 3. 归属清单 · 后端

判据：文件修改时间（`dist` 与大量厂商模块统一停在导入日 11-04 23:41，我们的增量在 11-09 ~ 11-16）+ 代码里的中文过程注释 + 是否只服务于群聊/语音/待办/MES。**时间戳是间接证据**，与注释冲突时以注释为准。

| 模块 | 行数 | 归属 | 作用 |
|---|---|---|---|
| `api/chat.py` | 10,259 | 厂商 | 对话主链路、SSE 流、媒体路由、引用 |
| `api/documents.py`、`api/writing.py`、`api/schedules.py`、`api/admin_users.py` | 6,287 | 厂商 | 文档库、写作、定时任务、后台 |
| `services/harness/*`、`services/pi_local.py` | ~1,400 | 厂商 | Agent 运行时 / 沙箱 / 技能网关 |
| `api/group_chat.py` + `services/group_chat.py` + `services/group_assistant.py` | 2,400 | **我们** | 群聊、群内 AI 分析出待办 |
| `services/todo_followup.py` | 398 | **我们** | 待办催办/升级/主题归并 |
| `api/assistant_watch.py` + `services/assistant_watch.py` | 2,219 | **我们** | AI 主动巡检 MES（事件、认领、提醒、整改） |
| `api/voice.py` + `services/moss_voice.py` | 1,701 | **我们** | MOSS-Live 实时语音、工具调用、简报朗读 |
| `api/asr.py`、`api/tts.py` + services | 286 | **我们** | 语音识别/合成 |
| `api/briefing.py` + `services/production_briefing.py` | 253 | **我们** | 按 MES 权限产生产概况 |
| `api/inapp_notifications.py` + service | 175 | **我们** | 站内通知（轮询，不用 WebSocket） |
| `api/internal_mes.py`、`services/mes_*.py`、`api/mes_identity.py` | ~900 | **我们** | MES 单点登录、身份镜像、能力探测 |

数据表同理：`project_groups` / `project_group_messages` / `group_todos` / `assistant_events` / `inapp_notifications` 是我们的；`chat_sessions` / `chat_messages` / `documents` 等是厂商模型。当前 SQLite 113 张表、183 条待办（`group_todos`，其中 16 条未完成）、32 条 MES 巡查事件。

## 4. 厂商到底限制了什么（为什么必须自己做前端）

1. **没有前端源码**：`dist` 是压缩后的 chunk，改一个字段要逆着编译产物打补丁；上层补丁 `dnx-ui-polish.*` 就是这个思路的产物，它自己都写着「CSS 改不动 `--home-col`，只能运行时塞内联变量」。
2. **信息架构是厂商的**：首页/对话/文档/写作/定时任务并铺，MES 待办只能塞进群聊页的一个页签，或者靠 `mes-todo-pod.js` 悬浮球外挂。
3. **交互改不动**：导航项是运行时克隆出来的（`nav-inject.js`）、会话卡死要靠 `session-guard.js` 兜底跳登录。
4. **品牌与计费绑定**：`dist` 里 `LinkMind` 出现 60+ 处，后台有计费入口，不适合当自有产品外壳。

结论：**界面层全部自建**；后端继续复用厂商的对话/模型/文档能力（我们是调用方，不 fork），把我们的待办、巡查、语音、MES 联动放到一等位置。

## 5. 新界面的信息架构（待办为中心）

| 一级 | 路由 | 内容 |
|---|---|---|
| 待办 | `#/todos`（首页） | ① 4 张统计卡（待处理/高优先级/AI 发现/已完成）② AI 生产概况（可朗读）③ 待办清单（待处理/进行中/已完成 × 全部/AI 整理/高优先级）+ 详情面板（要做的事、关联单据、状态流转）④ AI 发现（MES 巡检出的问题，可认领、可跳 MES 处理） |
| 对话 | `#/chat` | 会话列表 + 消息流（SSE 增量）+ 输入区；麦克风=录音转文字；「实时语音」= 右侧停靠语音面板（同一屏里边聊边说） |
| 语音 | `#/voice` | 生产概况朗读 + 实时语音主界面；可「打开独立窗口」沿用旧悬浮窗 |

**设计记忆点**：待办不是"消息列表里的一条"，而是**一张可执行的单**——点进去能看见「要做的事（AI 拆好的子项）」和「关联 MES 单据」，处理完直接回写状态。

## 6. 设计取值（Ant Design v6.6.8 设计库）

| 项 | 取值 | 来源 |
|---|---|---|
| 主色 | `--ant-color-primary` `#1677ff`（暗色 `#1668dc`） | `dsh-kit token colorPrimary` |
| 背景/容器 | `--ant-color-bg-layout` `#f5f5f5` / `--ant-color-bg-container` `#ffffff` | token |
| 文本 | `rgba(0,0,0,.88)` / `.65` / `.45` / `.25` 四级 | token |
| 语义色 | success `#52c41a`、warning `#faad14`、error `#ff4d4f` | token |
| 圆角 / 控件高 / 正文 | 6 / 32 / 14·22 | token |
| 间距 | 8pt：8/16/24/32 | DESIGN-GUIDE |
| 组件对应 | Card（0 层边框）、Statistic、Tabs、Segmented、Table→列表行、Drawer、Tag、Empty、Avatar、Badge | `dsh-kit component` |
| 页面模板 | 工作台（5–9 模块、首屏放最常用、空态给引导）+ 列表页（重要列靠左、操作列右侧）+ 详情页 | `dsh-kit template` |

实现方式：**不引入 antd npm**（本仓库无构建、要离线可跑）。把设计库的 `tokens/tokens.css`（536 个 `--ant-*`，含暗色）直接放进项目，组件样式按 antd 规范手写成 `.card/.btn/.tag/...`，图标用设计库 848 个 SVG 里的 21 个。这样视觉与 antd 对齐，又不需要构建链。

## 7. 落位与迁移路线

| 阶段 | 动作 | 回滚 |
|---|---|---|
| 1（本次） | 新建 `frontend-desk/` + `serve-ai-desk.mjs`(:18092)，旧 `:18091` 不动 | 关掉 :18092 即可 |
| 2 | 待办首页接入 MES 顶栏入口（替换/并列旧入口） | 改回旧链接 |
| 3 | 新界面接管登录门（现在借用旧门户的 Cookie，同源不同端口天然共享） | 保留旧的 `legacyLoginUrl()` |
| 4 | 群聊/语音并入新壳，`frontend/dist` 与 `dnx-*` 补丁退役 | 保留 dist 副本 |

**端到端登录现状**：`DOC_NEXUS_MES_AUTH_ENABLED=1` 时账号密码登录会走 MES 校验，MES 没起就登不进（本次剖析时 8080 未运行，所以新界面用 mock 数据做像素验收）。这不是新界面的问题，属于部署状态。

## 8. 已知边界

- 本次只做界面层，**不改厂商后端**（`chat.py` 等），也不 fork。
- 实时语音的音频链路复用 `pod/`（已验收的 MOSS-Live 客户端），新界面负责集成与布局，不重写音频栈。
- 厂商 `dist` 里的管理后台/写作/文档库在新界面**没有入口**；过渡期需要时仍去 `:18091`。
- 待办状态机目前由前端负责呈现（`pending→doing→done`，可 `dismissed`），后端只校验枚举。
