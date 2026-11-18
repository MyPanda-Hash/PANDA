# AGENTS.md — light-mes-ai 项目启动与运行

> 本工作区（`_light-m-ai`）的开工手册：**怎么启动、改完怎么生效、怎么验证、哪些坑别再踩**。
> 业务规则与面板/权限纪律见 `light-mes/AGENTS.md`；迁移背景见 `README-MAC.md`。
> 「写代码怎么不翻车」一节对 `light-mes` 与 `doc-nexus-ai` 两个子项目都适用。
>
> **仓库结构（2026-11-18 起）**：整个工作区是**一个 git 仓库**，远端 `PANDA`。
> 根目录有 `light-mes/`（MES 主系统）与 `doc-nexus-ai/`（AI 数字员工）两个子目录，
> 它们不再是独立仓库。历史以 `legacy/light-mes`（153 提交）和 `legacy/doc-nexus-ai`（5 提交）
> 两个分支保留在同一个远端里，需要考古时 `git log legacy/light-mes` 即可。

## 项目一句话

双系统本地工作区：`light-mes`（Spring Boot + Vue 3 + PostgreSQL 的轻 MES）与
`doc-nexus-ai`（FastAPI 的 AI 数字员工），由 `start-local.sh` 一并拉起。

---

## 1. 开工前（先花 3 分钟）

### 1.1 进会话第一件事：跑一次自愈

```bash
cd /Users/panda/Projects/_light-m-ai && bash ensure-services.sh
```

它体检 5432/8080/4173/8000/18092（配了 `MES_INIT_AI_TOKEN` 再加 18093），**全通就 0.2 秒静默退出**，
缺哪个补哪个；加锁 `/tmp/dsh-ensure.lock`，结果写 `/tmp/dsh-ensure.log`；PostgreSQL 没起时会先 `limactl start colima`。
只看状态用 `bash ensure-services.sh --check`（有端口没起时退出码 1）。

**已经在跑的服务不要重启**——这个脚本就是重入安全的入口，别再叠加 `start-local.sh`。

> 「必须跑一次」和「不要手工补」不冲突：跑脚本是**确认状态**（幂等），
> 「手工补」指看到端口没起就自己 `nohup xxx &` 另起一份——那会跟 launchd 守护抢同一个端口。
> 守护层说明见 §2.3。

### 1.2 本机环境事实（别猜，按这张表来）

| 项 | 事实 |
| --- | --- |
| node / npm | **没有系统版本**，在 `~/.local/node/bin/`；裸写 `npm` 会 command not found |
| maven | 在 `~/.local/maven/bin/mvn`，且要配 `JAVA_HOME=$HOME/.local/jdk/home` |
| lima | `limactl` 在 `~/.local/lima/bin/`，不在默认 PATH |
| MySQL | **2026-11-16 停用**。容器 `light-mes-db` 保留不删，回滚才 `docker start light-mes-db` |
| 旧门户 | **:18091 已停用**（`serve-ai-web.mjs`）。群聊、语音现在都走 :18092 |
| 应用起法 | 只用 `start-local.sh`；`start-all.sh` 会检查系统 java/node，本机起不来 |
| GitHub 推送 | **直连超时**（`Recv failure: Operation timed out`），须走本机代理 `127.0.0.1:7890`。本仓库已配 `git config http.proxy`，但 `.git/config` 不随仓库分发，**换机器/重新 clone 后要重配**：`git config http.proxy http://127.0.0.1:7890`；代理不可用时 `git config --unset http.proxy` 关掉 |

`start-local.sh` 自己会设好 `JAVA_HOME` 与 `PATH`（含 `~/.local/{jdk,node,lima,pi-runtime}`）并 source `.env.local`，
**只有手动敲构建命令时**才需要按上表补路径。

### 1.3 哪些区域别碰

| 路径 | 说明 |
| --- | --- |
| `_scratch/` | 浏览器实测产物（截图、探针），不是源码 |
| `doc-nexus-ai/frontend/dist/` | **厂商 React 构建产物，没有源码**；主应用界面要改回原仓库 |
| `*.bak-*` | 历史备份（如 `menu.js.bak-150409`），不是现行实现 |
| `run/`、`runtime-logs/`、`docs/archive/`、`docs/ref/`、`target/`、`dist*`、`node_modules/` | 运行产物或归档，不索引 |

**AI 侧界面分三处，「能不能改」取决于改哪处**：

| 目录 | 性质 | 改完怎么生效 |
| --- | --- | --- |
| `frontend/`（只有 `dist/`） | 厂商构建产物 | 改不了，回原仓库 |
| `frontend-desk/` | 自己的工作台源码（原生 ES 模块 + CSS） | 无构建，刷新即生效 |
| `frontend-group/` | 群聊页源码（原生 ES 模块 + CSS） | 无构建，刷新即生效 |
| `frontend-group/chat-deepseek-src/` | chat-demo **源码**（React + rspack） | **要构建 + 复制**，见 §4 |

凭据在根目录 `.env.local` 与 `doc-nexus-ai/.env`，不进 git、不回显。

---

## 2. 启停与服务常驻

### 2.1 启动（顺序不能反）

```bash
cd /Users/panda/Projects/_light-m-ai
export PATH="$HOME/.local/lima/bin:$PATH"
limactl start colima          # ① 先起 Docker 运行时
bash start-local.sh           # ② 再起应用
```

**第 ① 步必须单独做**：`start-local.sh` 里只执行 `docker start light-mes-pg`，lima VM 没起时 Docker 不可用，
这一步**静默失败**，脚本随后打印「5432 未就绪」但其余服务照常启动——表现为 MES 能开、登录全 500。

必须用 `limactl start colima`，**不要用 `colima start`**：后者在无实例时会去 GitHub 下载 VM 镜像，
当前网络下 30 秒超时报 `error getting qcow image: ... request timed out`。

`start-local.sh` 依次拉起：PostgreSQL → MES 后端 :8080 → MES 前端 :4173 → AI 后端 :8000 →
AI 工作台 :18092 → 组织架构图解析 :18093（可选）。

### 2.2 停止

```bash
bash start-local.sh stop      # 停应用进程 + 置 hold（防止被守护立刻拉起）；PostgreSQL 容器保留
```

停 Docker 运行时（一般不需要）：`limactl stop colima`。

### 2.3 服务常驻（两层，缺一不可）

**第一层：进程脱离**。每个服务由 `run_detached` 用 `setsid` 起在独立进程组（`pgid == pid`、`ppid == 1`），
关终端、退出 DSH 时按进程组发信号打不到它们。

> 但这层挡不住「按 pid 列表杀」：2026-11-16 实测 DSH 重启那轮，6 个服务 + lima VM 全被 SIGKILL，setsid 没救回来。

**第二层：launchd 守护** `~/Library/LaunchAgents/local.lightmes.ai.ensure.plist`：
登录跑一次（`RunAtLoad`），之后每 60 秒跑 `ensure-services.sh`；健康时 0.2 秒退出。
它和 DSH 无关，所以挡得住「按 pid 杀」——**实测杀掉后不干预，81 秒自动恢复**。

| 想做什么 | 命令 |
| --- | --- |
| 看守护状态 | `launchctl print gui/$(id -u)/local.lightmes.ai.ensure \| head -20` |
| 暂停自愈（保留登录自启） | `touch /tmp/lightmes.hold`；恢复 `rm /tmp/lightmes.hold` |
| 停服务（同时置 hold） | `bash start-local.sh stop` |
| 恢复服务（自动清 hold） | `bash start-local.sh` |
| 彻底卸载守护 | `launchctl bootout gui/$(id -u)/local.lightmes.ai.ensure` 再删 plist |
| 守护日志 | `/tmp/lightmes-ensure.launchd.log` |

---

## 3. 端口、账号与日志

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| PostgreSQL | `127.0.0.1:5432` | 容器 `light-mes-pg`，主库 |
| MES 后端 | `http://127.0.0.1:8080` | `/api/px/*` 是唯一数据链路 |
| MES 前端 | <http://localhost:4173> | `admin` / `123456` |
| AI 后端 | `http://127.0.0.1:8000` | 接口文档 <http://127.0.0.1:8000/docs> |
| AI 工作台 | <http://localhost:18092> | 待办/群聊/语音都在这。**MES 顶栏「AI 数字员工」指向这里**，SSO 落 `/#/todos` |
| 语音悬浮窗 | <http://localhost:18092/pod> | MOSS-Live 实时语音；`?embed=1` 供工作台内嵌 |
| 组织架构图解析 | `127.0.0.1:18093` | **可选件**，只在配了 `MES_INIT_AI_TOKEN` 时启动；视觉模型走云端 `qwen3-vl-plus` |

> **数据边界**：组织架构图会经 18093 发给阿里云百炼解析（原「图片不出网」方案已放弃）。
> 如需恢复本机模型：`LOCAL_VLM_URL=http://127.0.0.1:11434/v1`、`LOCAL_VLM_MODEL=qwen2.5vl:7b`、清空 `LOCAL_VLM_API_KEY`。

| 日志 | 内容 |
| --- | --- |
| `/tmp/lightmes-backend.log` | MES 后端（Spring Boot） |
| `/tmp/lightmes-frontend.log` | MES 前端（vite preview） |
| `/tmp/doc-nexus.log` | AI 后端（FastAPI） |
| `/tmp/desk-web.log` | AI 工作台静态服务 + 反代（:18092），群聊与语音也在这里 |
| `/tmp/orgchart-local.log` | 组织架构图解析（仅启用时） |
| `/tmp/lightmes-ensure.launchd.log` | 自愈守护 |

> `/tmp/ai-web.log` 是旧门户 :18091 的**历史文件**，停用后不再产生；老机器上可能还留着，别当当前日志看。

### 3.1 定时任务

| 任务 | 计划 | 改法 |
| --- | --- | --- |
| 数据库备份 | 每周一 02:00（Asia/Shanghai），保留最近 8 份 | `MES_BACKUP_CRON` / `MES_BACKUP_KEEP` / `MES_BACKUP_AUTO=false` 关 |

实现见 `light-mes/backend/.../service/BackupScheduler.java`，走「立即备份」同一链路，操作人记 `system`；
只在后端进程运行时触发，日志关键字 `[backup]`，页面「基础设置 → 数据库备份」顶部显示当前计划。

---

## 4. 改完怎么生效（构建）

**`start-local.sh` 只跑现成产物，不会自动打包**，改了源码必须自己构建。下面每条都已带全路径
（本机 node/npm/mvn 不在 PATH，见 §1.2）：

```bash
# ① MES 后端 Java / SQL
cd light-mes/backend && JAVA_HOME="$HOME/.local/jdk/home" \
  ~/.local/maven/bin/mvn -q package -DskipTests

# ② MES 前端 Vue
cd light-mes/frontend && ~/.local/node/bin/npm run build

# ③ AI 后端 Python：不用构建，重启即可（venv 在 doc-nexus-ai/backend/.venv）

# ④ chat-demo：构建 → 复制（注入脚本已随源码构建，无需手工插入）
cd doc-nexus-ai/frontend-group/chat-deepseek-src && \
  NODE_ENV=production ~/.local/node/bin/node \
  node_modules/@rspack/cli/bin/rspack.js build -c build-local.ts
cd /Users/panda/Projects/_light-m-ai && \
  cp -R doc-nexus-ai/frontend-group/chat-deepseek-src/dist/. \
        doc-nexus-ai/frontend-group/chat-deepseek/
```

> 注入脚本（`localStorage.mes_token` → `chat-demo:api-key`）已固化在
> `chat-deepseek-src/src/index.html`，构建会自动带上，**不再需要手工重插**。
>
> `frontend-desk/` 与 `frontend-group/` 其余页面**无构建步骤**，改完刷新即生效。
>
> `frontend-group/chat-deepseek/`（构建产物，约 4.5 MB）**已在 .gitignore 里**，
> 所以新克隆下来必须跑一次第 ④ 步，否则对话页 404。

---

## 5. 怎么确认活着（验证）

```bash
# 端口：前五个必须 OPEN；18093 是可选件，closed 不代表故障
for p in 5432 8080 4173 8000 18092 18093; do
  printf '%s ' $p; nc -z 127.0.0.1 $p && echo OPEN || echo closed
done

# MES 登录（应 code:200 且 realName=管理员）
curl -s http://127.0.0.1:8080/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"userName":"admin","password":"123456"}'

# AI 健康（应 {"status":"ok","build":"dev"}）
curl -s http://127.0.0.1:8000/health
```

**只看端口 OPEN 不够**：VM 没起时 4173/8000 也可能先起来（它们不依赖 Docker），
必须再验一次登录，才能确认 PostgreSQL 链路真的通了。

---

## 6. 写代码怎么不翻车

> 来自反复出现的工具调用失败的复盘。失败本身不致命，但都消耗时间，
> 其中两条差点把真 bug 带进线上。按下面做能避开九成。

### 6.1 改源码优先 `edit`；要写脚本就用「不含目标代码的极简脚本」

**别这么做**：在 TypeScript 字符串里拼一段 Python，再用 `python3` 执行它去改第三个文件。
三层嵌套（TS 字符串 → Python 源码 → 目标文件里的引号）会出现这些错：

| 报错 | 真正的原因 |
| --- | --- |
| `SyntaxError: Expected ',', got 'string literal'` | Python 里相邻字符串漏了 `+`，或换行拼接断了 |
| `SyntaxError: EOL while scanning string literal` | 内层引号把外层字符串提前闭合 |
| `SyntaxError: invalid character '。' (U+3002)` | 用三引号包中文注释，嵌套又出现三引号 |
| `ReferenceError: chr is not defined` | 在 **JS** 字符串里写了 Python 的 `chr(34)`，那是 JS 上下文 |
| `ReferenceError: xxx is not defined` | JS 模板字符串里的 `${...}` 被插值了，不是想要的目标文本 |

**按这个顺序选工具**：

1. **单点小改** → `edit` 工具，`old_string` 带足够上下文保证唯一。
2. **整段替换** → 先把新内容 `write` 到 `/tmp/xxx.txt`，再写一个**不含任何目标代码**的极简脚本：
   读 old.txt、读 new.txt、`s.replace(old, new, 1)`、写回、`ast.parse` 自检。
   脚本里只出现文件路径，不出现业务代码，转义问题归零。
3. **改 JS/TS/Vue** → 优先 `edit`；批量替换**先数目标字符串出现几次**（`grep -c`），见 6.3。

### 6.2 整段替换函数体，必须回头确认「关键行」还在

真实事故：重写 `_run_with_tools` 函数体时，把 `tools = _openai_tools()` 一起吞掉了。
后果极隐蔽——`tools=tools` 抛 `NameError`，被 `except Exception` 吞成
**「模型调用失败：name 'tools' is not defined」当成正常回答返回**：HTTP 200、轮数统计正常、
前端正常显示，只是回答只有 17 token。

**规矩**：
- 替换函数体后 `grep` 一遍原来每一条赋值与调用是否都还在；
- **跑一次真实请求，看回答内容**，不能只看状态码 200；
- `ast.parse` / `node --check` 只能证明语法，证明不了行为。

### 6.3 全局替换前先数出现次数

真实事故：把 `setTheme(themeChoice());` 批量替换成 `followChatTheme();`，连**函数体内**那行也换了，
于是 `followChatTheme()` 自己调自己，无限递归。

**规矩**：`grep -c` 先看命中几处；多于 1 处就用带上下文的 `edit` 逐处改。

### 6.4 `node --check` 通过 ≠ 能跑

`ai-usage.js` 里两个相邻字符串字面量漏了 `+`，`node --check` 报 OK，
但浏览器里是 `SyntaxError: Unexpected string`，整页渲染不出来（只剩「暂时打不开」）。

**规矩**：ES module 用 `node --input-type=module --check < file.js`；更重要的是**在浏览器里真的打开一次**。

### 6.5 页面空白，先查「脚本有没有执行」

`serve-ai-desk.mjs` 的 `index.html` 曾写死 `<base href="/ai/">`，直接访问 `:18092/` 时所有相对路径变成
`/ai/js/main.js` → 404 → **`<script type="module">` 压根不执行**。表现极具欺骗性：
骨架在、接口全 200、控制台零报错。现已按请求前缀动态给 base。

**排查顺序**（别一上来怀疑业务代码）：
1. `document.querySelector('script[src]').src` —— 路径对不对；
2. `import("/js/main.js?v=" + Date.now())` 手动导入 —— 能跑说明是加载/缓存问题；
3. 读 `getComputedStyle` / `document.body.dataset` —— 判断 render 有没有跑过。

### 6.6 浏览器验证的已知坑（ego-browser）

| 错误 | 正确做法 |
| --- | --- |
| `page.on is not a function` | 该封装无事件订阅；在页面里自建 `window.__ERRS__` 收集器再 `evaluate` 读 |
| `task.newPage("p1")` 报「不接受参数」 | `await task.newPage()`，标签页靠 taskSpace 隔离 |
| `page label not found: p1` | 上个脚本已 `task.finish()`，换新 taskSpace 名 |
| `xxx is not defined`（evaluate 里） | 回调在页面内执行，读不到 Node 侧变量；用第二个参数传 JSON |
| 访问 `/sys/console` 落到别的页 | MES 是 **hash 路由**，要写 `#/sys/console` |
| 改了源码刷新没反应 | 先查 `cache-control`；没有 ETag 时 `no-cache` 等于强制用旧文件，要 `no-store` |

---

## 7. 踩过的坑

### 7.1 环境与部署

| 现象 | 根因 | 处理 |
| --- | --- | --- |
| 登录全 500，5432 未就绪 | lima VM 没起，Docker 不可用 | `limactl start colima`（见 §2.1） |
| `colima start` 卡住后超时 | 它会去下载 VM 镜像 | 改用 `limactl start colima`（实例已存在） |
| `limactl: command not found` | 不在默认 PATH | `export PATH="$HOME/.local/lima/bin:$PATH"` |
| `docker pull` 超时 | Docker Hub 不可达 | 用 `docker.1panel.live/library/<镜像>` 拉取后 `docker tag` |
| `bash start-all.sh` 起不来 | 它检查系统 java/node，本机没有 | 用 `start-local.sh` |
| 改了代码但行为没变 | 跑的是旧 jar / dist | 按 §4 重新构建 |
| 打开 18091 打不开 | 旧门户已停用（见 §1.2） | 用 <http://localhost:18092>，群聊 `/group`、语音 `/pod` 都在它下面 |
| 组织架构图解析报 401/403/超时 | 云端视觉模型 key 失效或欠费 | 看 `.env.local` 的 `LOCAL_VLM_API_KEY`；`curl :18093/health` 应显示 `mode: cloud, keyed: true` |
| 想回滚到 MySQL | 回滚窗口保留 | `docker start light-mes-db` + 按 `README-MAC.md` §3.2 设 `SPRING_DATASOURCE_URL` / `MES_FLYWAY_ENABLED=false` |

### 7.2 AI 侧改造（都已在代码里修掉，写下来防回退）

| 现象 | 根因 | 规矩 |
| --- | --- | --- |
| 模型说「上一轮对话里夹了一句：该助手为DeepSeek…」 | chat-demo 源码里的固定 `SYSTEM_PROMPT` 每次都塞进 `system` 字段 | **客户端不要注入身份**，只由后端 `_identity_prompt` 给；`_to_openai_messages` 已忽略 `body.system` |
| 回答末尾的耗时小字不出现 | 前端 `isUsage` 要求 `input_tokens` / `cache_creation_input_tokens` / `cache_read_input_tokens` / `output_tokens` **四个都在**，少一个整段 `usage` 被丢弃 | 自定义字段（如 `timing`）可以带，但**四个必需字段必须齐全** |
| AI 用量全是 0、算不出钱 | `complete_with_tools` 把上游 `usage` 丢了 | 模型调用必须把 `usage` 带出来（`prompt_tokens` / `completion_tokens` / `cached_tokens`） |
| 模型回「金额被脱敏成〔MONEY_1〕」 | `data_guard` 的金额规则把**公开网页数据**也打了码 | 脱敏要保留 `role=tool` 消息原样：工具结果是公开信息或已按权限裁剪过的数据 |
| 一次问答要 30–50 秒 | 同一轮多个 `tool_call` 串行；轮数上限 4；检索超时 60s | 工具用 `asyncio.gather` + `to_thread` **并行**；`max_rounds=3`；检索超时压到 20s |
| 非流式调用拿到一段解不开的文本 | `_error_frame` 对 `stream:false` 也返回 SSE | 错误帧按 `want_stream` 分流：流式给 SSE、非流式给 JSON |
| `include_router(xxx.router)` 报 `no attribute router` | `app.services.search_config` 与 `app.api.search_config` **同名** | 同名模块分层时给其中一个起别名（`as search_settings`） |
| 侧栏/表格文字在深色下几乎看不见 | `--ant-color-*`、`--dsw-alias-*` 这类变量在某些 bundle/主题下解析为空 | 用**确定存在的主题变量**（desk 是 `--ant-color-text`，来自 `theme-mes.css`），或 `inherit` + 中性 `rgba` |
| 爬虫检索「一次成功、第二次就限流」 | 百度/搜狗对连续请求很快返回空壳 | **别用爬虫当主力**；真要爬必须做「连续多次请求」的稳定性验证 |
| 以为 DeepSeek 的 key 能用来搜索 | DeepSeek **API 没有内置搜索**（`tools[type=web_search]` 直接 422，`enable_search` 被静默忽略），联网只在官方 App/网页版 | 「用 A 家的 key 做 B 家的事」先实测能力边界 |
| 后端启动失败 `database is locked` | 旧进程没退干净，SQLite 被占 | 重启前 `pkill -f app.server` 并确认无残留；启动后 `/health` 确认活的是新进程 |
| 改了 `chat-deepseek-src` 但页面没变 | 产物在 `chat-deepseek/`，要复制过去 | 按 §4 第 ④ 条构建并复制（注入脚本已在源码里，无需重插） |
