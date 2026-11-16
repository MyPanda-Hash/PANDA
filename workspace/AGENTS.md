# AGENTS.md — light-mes-ai 项目启动与运行

> 本文件只写**本工作区（`_light-m-ai`）专属**的启动方式、端口、日志和踩过的坑。
> 业务规则与代码纪律见 `light-mes/AGENTS.md`；迁移背景见 `README-MAC.md`。

## 项目一句话

双系统本地工作区：`light-mes`（Spring Boot + Vue 3 + PostgreSQL 的轻 MES）与
`doc-nexus-ai`（FastAPI 的 AI 数字员工），由 `start-local.sh` 一并拉起。

## 启动（按顺序，别跳步）

```bash
cd /Users/panda/Projects/_light-m-ai

# ① 先起 Docker 运行时（PostgreSQL 主库 + MySQL 回滚库都在 lima VM 里）
export PATH="$HOME/.local/lima/bin:$PATH"
limactl start colima

# ② 再起应用
bash start-local.sh
```

### 第 ① 步为什么必须单独做

`start-local.sh` 里只执行 `docker start light-mes-pg` / `docker start light-mes-db`。**lima VM 没起来时 Docker 不可用，
这一步会静默失败**，脚本随后打印「5432 未就绪」，但其余服务照常启动——表现为
MES 能开、登录却全部 500。

必须用 `limactl start colima`，**不要用 `colima start`**：后者在无实例时会去 GitHub
下载 VM 镜像（`abiosoft/colima-core` 的 raw.gz），当前网络下 30 秒超时，报
`error getting qcow image: ... request timed out`。

`limactl` 不在默认 PATH 里（在 `~/.local/lima/bin/`），命令要带路径或先 export。

### 第 ② 步做什么

`start-local.sh` 依次：起 PostgreSQL / MySQL 容器 → MES 后端 :8080 →
MES 前端 :4173 → AI 后端 :8000 → AI 前端 :18091 → 组织架构图解析 :18093（可选）。
脚本自己会设 `JAVA_HOME`、`PATH`（含 `~/.local/{jdk,node,lima,pi-runtime}`），
并 source 根目录 `.env.local`，所以不需要额外 export。

## 停止

```bash
bash start-local.sh stop     # 只停应用进程；PostgreSQL / MySQL 容器保留
```

停 Docker 运行时（一般不需要）：`limactl stop colima`。

## 端口与账号

| 服务 | 地址 | 说明 |
|---|---|---|
| PostgreSQL | `127.0.0.1:5432` | 容器 `light-mes-pg`，主库（2026-11-12 起） |
| MySQL | `127.0.0.1:3306` | 容器 `light-mes-db`，回滚窗口内保留 |
| MES 后端 | `http://127.0.0.1:8080` | `/api/px/*` 是唯一数据链路 |
| MES 前端 | <http://localhost:4173> | `admin` / `123456` |
| AI 后端 | `http://127.0.0.1:8000` | 文档 <http://127.0.0.1:8000/docs> |
| AI 前端 | <http://localhost:18091> | 标题「缔智元数字员工」 |
| 语音悬浮窗 | <http://localhost:18091/pod> | MOSS-Live 实时语音，独立 380×600 小窗口；门户导航「语音助手」可开 |
| 组织架构图解析 | `127.0.0.1:18093` | 只在配了 `MES_INIT_AI_TOKEN` 时启动，走本机视觉模型 |

## 定时任务

| 任务 | 计划 | 改法 |
|---|---|---|
| 数据库备份 | 每周一 02:00（Asia/Shanghai），保留最近 8 份 | `MES_BACKUP_CRON` / `MES_BACKUP_KEEP` / `MES_BACKUP_AUTO=false` 关 |

- 实现：`light-mes/backend/.../service/BackupScheduler.java`，走「立即备份」同一条链路，操作人记 `system`。
- 只在后端进程运行时触发；后端没起就不会备份。
- 日志关键字：`[backup]`；页面「基础设置 → 数据库备份」顶部显示当前计划。

## 日志

| 文件 | 内容 |
|---|---|
| `/tmp/lightmes-backend.log` | MES 后端（Spring Boot） |
| `/tmp/lightmes-frontend.log` | MES 前端（vite preview） |
| `/tmp/doc-nexus.log` | AI 后端（FastAPI） |
| `/tmp/ai-web.log` | AI 前端静态服务 + 反代 |
| `/tmp/orgchart-local.log` | 组织架构图解析（仅在启用时） |

## 改动后要重新构建（脚本只跑已有产物）

`start-local.sh` 直接用现成的 jar / dist / venv，**不会自动打包**：

```bash
# 改了 light-mes 后端 Java 或 SQL
cd light-mes/backend && ~/.local/maven/bin/mvn -q package -DskipTests

# 改了 light-mes 前端
cd light-mes/frontend && npm run build

# 改了 AI 后端 Python：不用构建，重启即可（venv 在 doc-nexus-ai/backend/.venv）
```

## 验证（重启后按此确认活着）

```bash
# 端口
for p in 5432 8080 4173 8000 18091; do
  printf '%s ' $p; nc -z 127.0.0.1 $p && echo OPEN || echo closed
done

# MES 登录（应 code:200 且 realName=管理员）
curl -s http://127.0.0.1:8080/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"userName":"admin","password":"123456"}'

# AI 健康（应 {"status":"ok","build":"dev"}）
curl -s http://127.0.0.1:8000/health
```

只看「端口 OPEN」不够：VM 没起时 4173/8000 也可能先起来（它们不依赖 Docker），
必须再验证一次登录，才能确认 PostgreSQL 链路真的通了。

## 本工作区专属的坑

| 现象 | 根因 | 处理 |
|---|---|---|
| 登录全 500，5432 未就绪 | lima VM 没起，Docker 不可用 | `limactl start colima` |
| `docker pull` 超时（registry 不可达） | Docker Hub 被墙 | 用 `docker.1panel.live/library/<镜像>` 镜像源拉取后 `docker tag` |
| `colima start` 卡住后超时 | 它会下载 VM 镜像 | 改用 `limactl start colima`（实例已存在） |
| `limactl: command not found` | 不在默认 PATH | `export PATH="$HOME/.local/lima/bin:$PATH"` |
| 改了代码但行为没变 | 脚本跑的是旧 jar/dist | 按上节重新构建 |
| `bash start-all.sh` 起不来 | 它检查系统 java/node，本机没有 | 用 `start-local.sh` |

## 谨慎区域

- `_scratch/` 是浏览器实测产物（截图、探针脚本），不是源码，不要索引或改动。
- `doc-nexus-ai/frontend/` 只有构建产物 `dist/`，**没有前端源码**；改 AI 界面要回原仓库。
- 凭据在根目录 `.env.local` 与 `doc-nexus-ai/.env`，不进 git、不回显。
