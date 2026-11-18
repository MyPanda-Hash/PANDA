# 缔智元 AI 数字员工 · 新工作台（frontend-desk）

> 以待办为中心、对话与语音一体的自建前端。**无构建步骤**：源码直接由浏览器加载。
> 旧的融合界面（`frontend/dist` + `frontend-group` + `serve-ai-web.mjs` :18091）原样保留，只当参照物。
> 资产归属与取舍见 [`docs/AI数字员工-资产分离与重塑.md`](../docs/AI数字员工-资产分离与重塑.md)，开发约束见 [`CONTRACT.md`](CONTRACT.md)。

## 1. 跑起来

```bash
cd /Users/panda/Projects/_light-m-ai

# A. 假数据（不需要 MES，界面右上角会显示「演示数据」）
python3 doc-nexus-ai/mock/serve_mock.py &     # :18094 fixtures（从真实 SQLite 只读导出）
node serve-ai-desk.mjs --mock &               # :18092
open http://127.0.0.1:18092/?theme=light#/todos

# B. 真后端（需要 MES 在跑：DOC_NEXUS_MES_AUTH_ENABLED=1 时登录要经过 MES）
bash start-local.sh            # 起 MES :8080 / :4173 与 AI 后端 :8000
node serve-ai-desk.mjs &       # :18092 → 反代 /api 到 :8000
```

- 登录态与旧门户共用：Cookie 不区分端口，在 :18091 登录过，:18092 直接可用；从 MES 点进来会自动走 SSO 票据。
- 旧的 :18091 不受影响，两者可以并排开着对照。

## 2. 结构

```
frontend-desk/
├── index.html          外壳（左导航 + 顶栏 + #view + #overlayRoot）
├── CONTRACT.md         并行开发契约（设计规则 / API / 文件边界）
├── css/
│   ├── tokens.css      Ant Design v6.6.8 设计库 536 个 --ant-* 变量（明/暗）
│   ├── desk.css        设计系统 + 布局 + 通用组件类
│   ├── todo.css        ┐
│   ├── chat.css        ├ 三个功能区各自样式（前缀隔离：todo-/chat-/voice-）
│   └── voice.css       ┘
├── icons/              设计库 SVG 拷贝（21 个，已补 xmlns，用 mask 着色）
└── js/
    ├── main.js         登录门、hash 路由、导航角标、顶栏动作、模拟数据角标
    ├── api.js          所有后端调用（含 SSE 与录音上传）
    ├── util.js         DOM/时间/待办明细解析/提示条
    ├── todos.js        待办工作台（首页）
    ├── chat.js         对话（SSE 流式 + 录音转写 + 语音停靠面板）
    ├── voice.js        语音助手（整页 + mountPanel 供对话页复用）
    ├── groups.js       项目群聊（同源 iframe 嵌 frontend-group，不重写）
    └── staff.js        员工画像（仅管理员，AI 按周期蒸馏的职能卡片）
```

模块契约：`export async function mount(root, ctx)` / `export async function unmount()`；
`ctx = { api, util, user, state, navigate, emit, refreshCounts }`。

## 3. 三个页面

| 路由 | 页面 | 关键内容 |
|---|---|---|
| `#/todos` | 待办工作台 | 4 张统计卡 → AI 生产概况（可朗读）→ 待办清单（待处理/进行中/已完成 × 全部/AI 整理/高优先级）+ 详情（AI 拆好的「要做的事」、关联 MES 单据、状态流转）→ AI 发现（按**业务域**分组：生产/仓储/销售/采购/质量/审批；默认每域只展开最严重一条，可单域展开或整域收起） |
| `#/chat` | 对话 | 会话列表 + 消息流（SSE 增量、停止、失败重试）+ 输入区（Enter 发送 / Shift+Enter 换行 / 录音转文字）；「实时语音」在右侧开停靠面板，同一屏里边聊边说 |
| `#/voice` | 语音助手 | 语音通道状态 → 生产概况（朗读）→ 实时语音窗（同源 `/pod/?embed=1`）→ 打开独立窗口 |
| `#/groups` | 项目群聊 | 同源 iframe 嵌 `/group/`（3 个群、群内 AI 整理待办、组织架构、附件），不重写 2954 行 |
| 账套 | 顶栏「账套」切换：从 MES SSO 带入当前账套，可在工作台内切换；待办/发现/群聊按账套隔离 |
| `#/models` | 模型与 API | 仅 admin：① **模型端点**：列表（供应商/模型/地址/密钥状态/连通）+ 新增 + 一键测试；② **语音助手**：MOSS-Live 实时通话模型/地址/音色/人设/通话上限/写操作限额 + DashScope ASR 模型 + 两个密钥 + 三个开关 + 连通自检。改完保存立即生效，不用重启 |
| `#/staff` | 员工画像 | 仅 admin 可见，整行铺满：成员列表 + 画像详情（关键指标、摘要、优势/风险/关注点、能力标签）+ **AI 会话记忆**（会话蒸馏出「用 AI 干了什么」、沉淀经验、证据充分度）；<1180px 详情走抽屉 |

顶部「演示数据」角标只在 `--mock` 服务下出现，避免把演示待办当真任务处理。

## 4. 数据从哪来

| 用途 | 接口 |
|---|---|
| 待办 | `GET /api/todos`、`PATCH /api/todos/{id}`（`pending/accepted/doing/done/dismissed`） |
| AI 发现 | `GET /api/assistant/events`、`GET /api/assistant/status`、`POST /api/assistant/events/{id}:ack`；事件带 `domain`/`domain_label`/`kind_label`，由后端 `services/assistant_watch.py` 的 `KIND_DOMAIN` 映射（未知 kind 按面板前缀兜底） |
| 群聊 | 由 `/group/` 页面自己调 `/api/groups*`（iframe 同源共享 Cookie） |
| 员工画像 | `GET /api/admin/employees`（非 admin 返回 403，导航项按角色隐藏） |
| 会话记忆 | `GET /api/admin/employees/{id}/memories`、`POST /api/admin/employees/{id}:distill-sessions?days=30` |
| 模型端点 | `GET/POST /api/admin/model-endpoints`、`POST /api/admin/model-endpoints/{id}:test`；密钥走端点的 `secret_ref`（明文或 `env://变量名`） |
| 生产概况 | `GET /api/assistant/briefing` |
| 通知 | `GET /api/notifications`、`POST /api/notifications/read` |
| 对话 | `GET/POST /api/chat/sessions`、`GET /api/chat/sessions/{id}`、`POST /api/chat/messages`（SSE） |
| 语音 | `GET /api/assistant/voice/status`、`POST /api/asr/transcribe`、WS `/api/assistant/voice/ws`（由 `/pod/` 客户端负责） |

待办里的 `refs[].url` 指向 MES 表单（`/#/panelx/form/<PANEL>?code=<单据号>`），新窗口打开，只查不改。

## 5. 验收（2026-11-16 实测）

```bash
~/.local/bin/ego-browser nodejs < doc-nexus-ai/mock/shot.mjs
```

18 个组合（3 路由 × 3 宽度 1440/768/375 × 明暗）全部 `overflowX=0`，运行期异常 0、HTTP≥400 0。
产物：`/tmp/dsh-aidesk/shots/`。

| 项 | 结果 |
|---|---|
| 横向滚动 | 全部 0 |
| 卡片层级 | 0 层边框，无阴影（浮层/抽屉才有） |
| 控件/圆角/正文 | 32 / 6 / 14·22 |
| 图标 | 21 个来自设计库 SVG，`util.icon()` 统一生成 |
| 主按钮 | 每个操作区唯一 |
| 空态 | 三处空态都带主行动 |
| pod 两种形态 | 默认 `/pod/` 圆角 10、420 宽、pin/fold/close 可见；`?embed=1` 去壳铺满、隐藏三按钮，默认行为零变化 |

## 6. 已知限制

1. **麦克风链路没有真机实测**（无头浏览器无麦克风）：WAV 编码与错误映射按实现写好，需在 Chrome 里点一次确认。
2. **待办不做分页**：一次 `limit=200` 客户端过滤，真库 183 条时列表会很长（非性能问题）。
3. **语音主题不热跟随**：iframe 主题靠 URL 参数带入，页内切深浅色后要重进 `#/voice`（避免掐断通话）。
4. **对话 Markdown 只做最小集**：段落/列表/粗体/行内代码；表格、代码块、链接按纯文本。
5. **管理后台、文档库、写作、定时任务没有入口**：过渡期仍去旧站点 :18091。
6. **Safari 未验证**，`mask` 已用 `-webkit-` 前缀，但没有实测。
