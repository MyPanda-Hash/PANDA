#!/usr/bin/env bash
# 本地一键启动：PostgreSQL(容器,主库) + MySQL(容器,回滚保留) + light-mes 后端(8080) + 前端(4173) + AI 后端(8000) + AI 前端(18091)
# 与官方 start-all.sh 的区别：AI 部分走本地 Python venv + 自建静态代理，不依赖 Docker 构建。
set -uo pipefail
L="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export JAVA_HOME="$HOME/.local/jdk/home"
export PATH="$JAVA_HOME/bin:$HOME/.local/node/bin:$HOME/.local/bin:$HOME/.local/lima/bin:$HOME/.local/pi-runtime/bin:$PATH"
export PATH="$HOME/.local/bin:$PATH"
[ -f "$L/.env.local" ] && . "$L/.env.local"

stop_all() {
  pkill -f 'light-mes-backend-0.1.0.jar' 2>/dev/null
  pkill -f 'vite preview' 2>/dev/null
  pkill -f 'serve-ai-web.mjs' 2>/dev/null
  pkill -f 'app.server' 2>/dev/null
  pkill -f 'workbuddy-relay.mjs' 2>/dev/null
  pkill -f 'init_org_chart_local.py' 2>/dev/null
  echo "已停止 MES 后端/前端、AI 后端/前端、WorkBuddy 中转、本地组织架构解析（MySQL 容器仍在运行）"
  exit 0
}
[ "${1:-}" = "stop" ] && stop_all

port_up() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

echo "[1/6] 数据库（PostgreSQL 主库 + MySQL 回滚保留）"
docker start light-mes-pg >/dev/null 2>&1
docker start light-mes-db >/dev/null 2>&1
sleep 3
for i in $(seq 1 15); do docker exec light-mes-pg pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
docker exec light-mes-pg pg_isready -U postgres >/dev/null 2>&1 && echo "  5432 OK" || echo "  5432 未就绪"
port_up 3306 && echo "  3306 OK（MySQL，仅回滚用）" || echo "  3306 未就绪（不影响 PG 主链路）"

echo "[2/6] light-mes 后端 :8080"
if ! port_up 8080; then
  ( cd "$L/light-mes/backend" && nohup "$JAVA_HOME/bin/java" -jar target/light-mes-backend-0.1.0.jar > /tmp/lightmes-backend.log 2>&1 & )
  for i in $(seq 1 30); do sleep 2; port_up 8080 && break; done
fi
port_up 8080 && echo "  8080 OK" || echo "  8080 未就绪"

echo "[3/6] light-mes 前端 :4173"
if ! port_up 4173; then
  ( cd "$L/light-mes/frontend" && nohup npx vite preview --port 4173 --host > /tmp/lightmes-frontend.log 2>&1 & )
  sleep 6
fi
port_up 4173 && echo "  4173 OK" || echo "  4173 未就绪"

echo "[4/6] AI 数字员工 :8000 / :18091"
if ! port_up 8000; then
  ( cd "$L/doc-nexus-ai/backend" && nohup ./.venv/bin/python -m app.server > /tmp/doc-nexus.log 2>&1 & )
  for i in $(seq 1 30); do sleep 2; port_up 8000 && break; done
fi
port_up 8000 && echo "  AI 后端 8000 OK" || echo "  AI 后端未就绪"
if ! port_up 18091; then
  ( cd "$L" && nohup node serve-ai-web.mjs > /tmp/ai-web.log 2>&1 & )
  sleep 3
fi
port_up 18091 && echo "  AI 前端 18091 OK" || echo "  AI 前端未就绪"

echo "[5/6] 本地组织架构图解析 :18093（本机视觉模型，图片不出网）"
# 只在 MES 配了内部令牌时启动；没配就跳过（Excel 模板导入不受影响）
if [ -n "${MES_INIT_AI_TOKEN:-}" ]; then
  if ! port_up 18093; then
    ( cd "$L/doc-nexus-ai/backend" && ORGCHART_TOKEN="$MES_INIT_AI_TOKEN" \
        nohup ./.venv/bin/python scripts/init_org_chart_local.py > /tmp/orgchart-local.log 2>&1 & )
    sleep 3
  fi
  port_up 18093 && echo "  18093 OK（模型 ${LOCAL_VLM_MODEL:-qwen2.5vl:7b} @ ${LOCAL_VLM_URL:-http://127.0.0.1:11434/v1}）" \
    || echo "  18093 未就绪，见 /tmp/orgchart-local.log"
else
  echo "  跳过：未设置 MES_INIT_AI_TOKEN"
fi

echo "[6/6] WorkBuddy 中转 :18092（依赖 DSH 在运行）"
if ! port_up 18092; then
  ( cd "$L" && nohup node workbuddy-relay.mjs > /tmp/wb-relay.log 2>&1 & )
  sleep 3
fi
if port_up 18092; then
  echo "  18092 OK：$(curl -s --max-time 5 http://127.0.0.1:18092/healthz | head -c 120)"
else
  echo "  18092 未就绪（DSH 没开或插件补丁未生效）"
fi

echo
echo "MES:        http://localhost:4173   admin / 123456"
echo "AI 数字员工: http://localhost:18091  （从 MES 顶栏点入口可单点登录）"
echo "AI 接口文档: http://localhost:8000/docs"
echo "WorkBuddy:  http://127.0.0.1:18092/v1  （中转，供 AI 数字员工调用模型）"
