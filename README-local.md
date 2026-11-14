# doc-nexus-ai「缔智元数字员工」本地运行

来源：2026-10-28 从一份 WSL2 整机快照（`DMBox-Runtime.tar`）的 containerd 镜像层中提取；该快照已于 2026-10-30 删除，本目录是唯一副本，请勿依赖外部归档。

| 本目录 | tar 内原始位置 |
|---|---|
| `backend/` | `var/lib/containerd/.../snapshots/809/fs/app/backend` + `snapshots/766/fs/app/backend/pyproject.toml` |
| `frontend/dist/` | `var/lib/containerd/.../snapshots/609/fs/dist`（2026-10-12 构建） |
| `deploy/Dockerfile.backend` | `snapshots/809/fs/app/backend/Dockerfile`，只删掉缺失的 `scripts/writing-skills` / `scripts/builtin-skills` 两段 |

## 结构

```
docker-compose.yml        backend(18000) + frontend nginx(18091)
.env                      本地环境变量（JWT / 初始管理员 / 关闭 MinIO、LinkMind）
deploy/nginx.conf         SPA 静态 + /api 反代 + cookie path 重写
deploy/Dockerfile.backend 后端镜像（pi-runtime + chromium + libreoffice + tesseract）
backend/                  FastAPI 后端源码
frontend/dist/            React 构建产物
data/                     运行数据（SQLite / pi-runtime / uploads），已在 .env 指向这里
```

## 命令

```bash
cd /c/INCER/doc-nexus-ai
docker compose build backend      # 首次约 15–40 分钟（apt 装 chromium/libreoffice + npm + pip）
docker compose up -d
docker compose logs -f backend
docker compose down               # 停；数据留在 data/
```

访问 <http://localhost:18091/chat>。首次进入是管理员初始化页：

- 账号 `admin`
- 初始密码取自 `.env` 的 `DOC_NEXUS_BOOTSTRAP_PASSWORD`，页面要求改成一个不同的新密码

## 配置模型（不配就只能看界面，发消息无回复）

后端默认不预置模型端点（`DOC_NEXUS_LINKMIND_ENABLED=0`）。登录后在后台「模型端点」新增，或直接调 API：

```bash
# 先登录拿 access_token
curl -s -X POST http://localhost:18091/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<新密码>"}'

curl -s -X POST http://localhost:18091/api/admin/model-endpoints \
  -H "Authorization: Bearer <access_token>" -H 'Content-Type: application/json' \
  -d '{
    "provider":"deepseek","adapter":"openai_compatible","api_protocol":"chat_completions",
    "model":"deepseek-chat","capability":"chat",
    "endpoint_url":"https://api.deepseek.com/v1","secret_ref":"<API Key>",
    "is_default":true
  }'
```

## 从 light-mes 免密进入（MES 单点登录）

light-mes 顶栏「AI 数字员工」按钮不再只是打开对话页，而是带着一张 60 秒票据直接落到已登录的 `/chat`。

- 协议与实现见 `backend/app/services/mes_sso.py`（票据校验）与 `backend/app/api/auth.py` 的 `GET /api/auth/mes-sso`；light-mes 侧的签发实现是 `AiSsoService`。
- 两端必须配置同一个密钥：`.env` 的 `DOC_NEXUS_MES_SSO_SECRET` ↔ light-mes 的 `MES_AI_SSO_SECRET`。**留空即关闭该入口**（返回 401），此时仍可用账号密码登录。
- 验签通过后走 `mes_auth.sync_user`：MES 是账号与角色的权威源，本服务只镜像用户，不存第二份密码；`redirect` 由 `DOC_NEXUS_MES_SSO_REDIRECT`（默认 `/chat`）决定。
- 前端 `dist/` 无需改动：会话靠 `doc_nexus_refresh` cookie，SPA 启动即 `POST /api/auth/refresh` 恢复。
- 测试：`backend/tests/test_mes_sso.py`（9 例，覆盖验签、过期、时钟漂移、格式与未配置密钥）。镜像内没装 pytest，跑测试需先 `docker exec doc-nexus-backend pip install pytest httpx`。

## 已知限制

1. **前端只有编译产物**：tar 里没有 `src/`、`vite.config`、`package.json`，改 UI 只能改 dist 里的 chunk 或从原仓库取源码。
2. **线上比本地新**：线上 `/health` 为 `manual-44eff59-20261015_124008-8ea5b2af`，本地这份是 2026-10-12 的构建。
3. 六个 prompt-only 内置技能（writing-bid 等）随仓库根 `scripts/` 缺失，已从构建中移除。
4. MinIO、LinkMind、外部 Agent 平台（翠花 192.168.88.244:8991）均关闭；文档上传、SSO、按 LinkMind 计费相关功能不可用。
5. `DOC_NEXUS_PI_EXECUTION_MODE=host`：agent 直接跑在容器内，没有 Docker-in-Docker 沙箱隔离。
6. SQLite 单文件（`data/db/doc_nexus.sqlite3`），不是线上那套 Postgres/MinIO 形态。
