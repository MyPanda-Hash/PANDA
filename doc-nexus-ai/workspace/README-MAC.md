# light-mes + AI 数字员工 · macOS 复现配置

> 从 Windows 环境迁移过来的完整源码与复现步骤。两个项目：
> **light-mes**（轻 MES：Vue 3 + Spring Boot + PostgreSQL）与 **doc-nexus-ai**（AI 数字员工：FastAPI + React）。
>
> 本包**不含**：`node_modules`、`target`、`dist`、Python 的 `.env`、数据库文件、运行日志。
> 这些都在本文档里给出重新生成的方法。

---

## 0. 一分钟开始

```bash
# 1) 装依赖（见第 2 节），起 PostgreSQL（见第 3 节；表由 Flyway 自动建）
# 2) 设置三个环境变量（见第 4 节）
export SPRING_DATASOURCE_PASSWORD='你的PostgreSQL密码'
export MES_JWT_SECRET="$(openssl rand -base64 48)"
export MES_AI_SSO_SECRET="$(openssl rand -base64 48)"

# 3) 一键启动
bash start-local.sh
```

打开 <http://localhost:4173>，账号 `admin` / `123456`。

> ⚠️ **首次必须导入数据库**，否则界面上没有任何面板。见第 3 节。

---

## 1. 目录结构

```
light-mes-ai-bundle/
├── start-all.sh              macOS 一键启动脚本（本包新增）
├── README-MAC.md             本文档
├── light-mes/                轻 MES
│   ├── backend/              Spring Boot 3.5.3（Java 17）
│   ├── frontend/             Vue 3 + Element Plus + Vite
│   ├── docs/                 四份主题文档 + 设计资料
│   ├── tools/                校验脚本（docs-audit / verify-project 等）
│   └── GLOSSARY.md           术语表
└── doc-nexus-ai/             AI 数字员工
    ├── backend/              FastAPI（Python ≥3.12）+ ToolGateway
    ├── frontend/dist/        这里只有占位，需自行构建或用 nginx 镜像
    ├── deploy/               Dockerfile.backend + nginx.conf
    ├── docker-compose.yml    后端 18000 + nginx 18091
    └── .env.example          环境变量模板（值全空，需自己填）
```

---

## 2. 安装依赖

```bash
# Homebrew（若未装）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

brew install openjdk@17 node@20 maven postgresql@16
brew install --cask docker          # AI 服务需要 Docker Desktop

# Java 17 设为默认
sudo ln -sfn "$(brew --prefix openjdk@17)/libexec/openjdk.jdk" /Library/Java/JavaVirtualMachines/openjdk-17.jdk
java -version   # 应显示 17.x
```

> **注意**：`light-mes/tools/apache-maven-3.9.9/` 是 Windows 版，macOS 上**不要用**，请用 `brew install maven`。
> `light-mes/backend/mvnw` 在本包里不存在，用系统 `mvn`。

---

## 3. 数据库（关键一步）

数据库是 **PostgreSQL 16**（2026-11-12 由 MySQL 迁移）。表结构由 **Flyway** 在应用启动时执行
`light-mes/backend/src/main/resources/db/migration/V1__baseline.sql` 自动创建，不需要手工导入建表 SQL。

### 3.1 起库（二选一）

```bash
# 方式 A：Docker（本工作区当前用法）
docker run -d --name light-mes-pg \
  -e POSTGRES_PASSWORD="$SPRING_DATASOURCE_PASSWORD" -e POSTGRES_DB=light_mes \
  -e POSTGRES_INITDB_ARGS="--encoding=UTF8 --locale-provider=icu --icu-locale=zh-CN" \
  -p 5432:5432 postgres:16

# 方式 B：Homebrew
brew services start postgresql@16
createdb light_mes
```

会话时区设成本地，否则触发器写入的 `update_time` 会是 UTC：

```bash
psql -U postgres -d light_mes -c "ALTER DATABASE light_mes SET timezone TO 'Asia/Shanghai';"
```

> **端口**：默认 5432，`MES_DB_PORT` 覆盖。

### 3.2 结构与数据从哪来

| 场景 | 做法 |
|---|---|
| 新环境空库 | 直接启动后端，Flyway 建 26 张表（空业务数据，面板另行导入） |
| 从 MySQL 搬 | `set -a && . ./.env.local && set +a` 后跑 `bash light-mes/tools/mysql2pg-copy.sh`（逐表核对行数与序列） |
| 从备份恢复 | `pg_restore --no-owner --no-privileges -d light_mes <light_mes.dump>` |
| 换库后对账 | `node light-mes/tools/pg-parity.mjs` 与 `node light-mes/tools/pg-dialect-audit.mjs` |

校验（面板数应 > 100）：

```bash
psql -U postgres -d light_mes -c "SELECT COUNT(*) AS panels FROM panel_config;"
```

> 结构改动只能新增 `db/migration/V2__xxx.sql`；`db/*.sql` 里的 MySQL 脚本是回滚资产，见 `db/README.md`。

回滚到 MySQL（只改环境变量，不动代码）：

```bash
export SPRING_DATASOURCE_URL='jdbc:mysql://localhost:3306/light_mes?useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai'
export SPRING_DATASOURCE_USERNAME=root
export MES_FLYWAY_ENABLED=false
```

## 4. 环境变量

light-mes **所有凭据只走环境变量**，配置文件里没有明文。

```bash
# 加到 ~/.zshrc 以便长期生效
export SPRING_DATASOURCE_PASSWORD='你的PostgreSQL密码'
export MES_JWT_SECRET="$(openssl rand -base64 48)"      # 至少 64 位
export MES_AI_SSO_SECRET="$(openssl rand -base64 48)"   # 必须与 AI 侧一致
```

| 变量 | 必填 | 用途 |
|---|---|---|
| `SPRING_DATASOURCE_PASSWORD` | ✅ | PostgreSQL 口令 |
| `MES_JWT_SECRET` | ✅ | JWT 签名，≥64 位随机串 |
| `MES_AI_SSO_SECRET` | AI 免密登录需要 | 与 AI 的 `DOC_NEXUS_MES_SSO_SECRET` **必须相同** |
| `MES_DB_PORT` | 否（默认 5432） | PostgreSQL 端口 |
| `MES_HTTP_PORT` | 否（默认 8080） | 后端端口 |
| `SPRING_DATASOURCE_USERNAME` | 否（默认 postgres） | PostgreSQL 用户 |
| `ALIBABA_CLOUD_ACCESS_KEY_ID/SECRET` | 否 | 仅 OCR/机翻用到 |
| `MES_FLYWAY_ENABLED` | 否（默认 true） | 回滚到 MySQL 时设 false |
| `MES_PG_DUMP_CMD` | 否 | 本机无 `pg_dump` 时设成 `docker exec -i -e PGPASSWORD light-mes-pg pg_dump` |
| `MES_BACKUP_DIR` | 否 | 备份落盘目录，默认 `/opt/light-mes/backup` |

---

## 5. 构建与运行

### 5.1 后端

```bash
cd light-mes/backend
mvn clean package -DskipTests
java -jar target/light-mes-backend-0.1.0.jar
```

验证：`curl -s http://127.0.0.1:8080/api/auth/login -H 'Content-Type: application/json' -d '{"userName":"admin","password":"123456"}'`

### 5.2 前端

```bash
cd light-mes/frontend
npm ci          # 有 package-lock.json，用 ci 保证版本一致
npm run build
npx vite preview --port 4173
```

开发模式（热更新）：`npm run dev` → <http://localhost:5173>

### 5.3 AI 数字员工

```bash
cd doc-nexus-ai
cp .env.example .env
# 编辑 .env，至少填：
#   DOC_NEXUS_JWT_SECRET=<随机串>
#   DOC_NEXUS_BOOTSTRAP_PASSWORD=<管理员密码>
#   ALIYUN_DEEP_API_KEY=<你的百炼 key>
#   DOC_NEXUS_PI_CHAT_MODEL=<模型名>
#   DOC_NEXUS_MES_SSO_SECRET=<与 MES 的 MES_AI_SSO_SECRET 相同>
#   MES_BASE_URL=http://host.docker.internal:8080   ← macOS 上容器访问宿主要用这个

docker compose up -d
curl http://localhost:18091/api/health   # {"status":"ok",...}
```

> **macOS 容器访问宿主机**：Linux 用 `172.17.0.1`，macOS/Windows 必须用 `host.docker.internal`。
> 所以 `.env` 里 `MES_BASE_URL` 要写 `http://host.docker.internal:8080`，不是 `localhost`。

MES 顶栏点「AI 数字员工」会走后端 `/api/ai/sso-ticket` → AI `/api/auth/mes-sso`。若返回 503「未配置 AI 单点登录密钥」，说明后端没读到 `MES_AI_SSO_SECRET`。

---

## 6. 与 Windows 环境的差异（踩坑清单）

| 项 | Windows | macOS 上要改成 |
|---|---|---|
| Maven | `tools/apache-maven-3.9.9/bin/mvn.cmd` | `brew install maven`，用 `mvn` |
| 启动脚本 | `start-project.bat`（cmd） | `bash start-local.sh`（本包新增） |
| 数据库 | MySQL，手工建库 + 11 个 `migrate_*.sql` | PostgreSQL 16，Flyway 自动建表 |
| 容器访问宿主 | `host.docker.internal` | 同（Linux 才是 `172.17.0.1`） |
| 行尾 | 部分文件 CRLF | 无需处理，git 会自动转换 |
| 端口 | 曾因保留端口段（3293–3392）导致 3306 绑不上 | 数据库用 5432 |

---

## 7. 验证清单

```bash
# 文档与架构门禁
node light-mes/tools/docs-audit.mjs
node light-mes/tools/architecture-audit.mjs
node light-mes/tools/action-support-audit.mjs

# 前端单测（预期 166 passed）
cd light-mes/frontend && npm test

# 后端测试（预期 711 tests, 0 failures）
cd light-mes/backend && mvn test

# 完整校验（文档+架构+动作表+前端构建+后端打包）
node light-mes/tools/verify-project.mjs
```

浏览器验收：1440 / 768 / 375 三档，`document.documentElement.scrollWidth === clientWidth`（无横向滚动）。

---

## 8. 已知未完成项

1. **`docs/ref/`（24.8 MB T+ 抓取资料）未打包** —— 是本地参考，不影响运行。
2. **AI 的 `frontend/dist/` 是占位** —— 仓库里没有可用的 React 构建产物；`docker-compose.yml` 的 frontend 服务用的是 nginx 镜像挂载 dist。要跑 AI 界面需自行构建，或直接用后端 API（18000）。
3. **`doc-nexus-ai/backend/app/builtin_skills/dashi-ppt`（37 MB）** 是 GPL 第三方 PPT 工具，随包带上了。
4. **`tools/tplus-grab`（1.24 GB）未打包** —— T+ 页面抓取产物，已 gitignore。

### 本包包含的提交（light-mes，共 4 个）

```
c8fff4c chore: 移除顶栏更新公告 + 补财务/退货链路文档
ee5ea39 feat(finance): 退货冲抵链路 + 收付款核销隔离 + 库存台账方向源
068f853 feat(backend): 面板自定义字段（px_custom_field）+ INV 明细补品牌列
8445c4e feat: 面板交互改造（单据栏/列头筛选/字段管理/动作支持表/入口收敛）
```

> 财务/退货那批改动原本是 Windows 工作区的未提交修改，已在本包里提交（`ee5ea39`、`c8fff4c`），
> 包含 `FinancePaymentService` 的往来单位核销隔离、`StockTxnService` 的出入库方向源、4 个新测试与
> `migrate_sales_flow_20261103.sql`。工作区里只剩 3 个无关文件未提交（一张截图、一个 0 字节空文件、
> 一个未被引用的 `ProcessGuide.vue`），都不影响运行。

---

## 9. 账号

| 系统 | 账号 | 密码 |
|---|---|---|
| light-mes | `admin` | `123456` |
| AI 数字员工 | `DOC_NEXUS_BOOTSTRAP_USERNAME` | `DOC_NEXUS_BOOTSTRAP_PASSWORD`（你自己在 .env 里设） |

> 上线前务必改掉 `123456`。
