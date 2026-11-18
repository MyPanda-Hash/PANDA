#!/usr/bin/env bash
# 本地一键启动：PostgreSQL(容器,主库) + MySQL(容器,回滚保留) + light-mes 后端(8080) + 前端(4173)
#                + AI 后端(8000) + AI 旧门户(18091) + AI 新工作台(18092)
# 与官方 start-all.sh 的区别：AI 部分走本地 Python venv + 自建静态代理，不依赖 Docker 构建。
#
# 常驻约定（2026-11-16 起）：每个服务都用 setsid 起在**独立会话**里，父进程（终端 / DSH）退出时
# 按进程组发信号打不到它们，所以重开 DSH 不用重拉。要停只有 `bash start-local.sh stop`。
set -uo pipefail
L="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export JAVA_HOME="$HOME/.local/jdk/home"
export PATH="$JAVA_HOME/bin:$HOME/.local/node/bin:$HOME/.local/bin:$HOME/.local/lima/bin:$HOME/.local/pi-runtime/bin:$PATH"
export PATH="$HOME/.local/bin:$PATH"
[ -f "$L/.env.local" ] && . "$L/.env.local"

stop_all() {
  # 置 hold：launchd 守护（登录自启 + 分钟级自愈）看到它就让路，不然刚停就被拉起来
  touch /tmp/lightmes.hold
  pkill -f 'light-mes-backend-0.1.0.jar' 2>/dev/null
  pkill -f 'vite preview' 2>/dev/null
  pkill -f 'serve-ai-web.mjs' 2>/dev/null
  pkill -f 'serve-ai-desk.mjs' 2>/dev/null
  pkill -f 'app.server' 2>/dev/null
  pkill -f 'init_org_chart_local.py' 2>/dev/null
  echo "已停止 MES 后端/前端、AI 后端/旧门户/新工作台、本地组织架构解析（MySQL 容器仍在运行）"
  exit 0
}
[ "${1:-}" = "stop" ] && stop_all

port_up() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# 手动启动即解除 hold（守护进程下一轮就会正常自愈）
rm -f /tmp/lightmes.hold

# 以独立会话启动：macOS 没有 setsid 命令，借 python 调 setsid(2)。
# 为什么必须这么做：`nohup ... &` 只脱离终端，进程组仍属于启动它的 shell；
# DSH/终端退出时若按进程组发 TERM，服务会跟着掉。setsid 之后它们自成一组，只有显式 stop 才停。
# 用法：run_detached <工作目录> <日志> <命令> [参数...]
run_detached() {
  local dir="$1" log="$2"; shift 2
  /usr/bin/python3 -c '
import os, sys
os.chdir(sys.argv[1])
os.setsid()                       # 新会话 + 新进程组：父进程组的信号打不到
fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
null = os.open(os.devnull, os.O_RDONLY)
os.dup2(null, 0)
os.dup2(fd, 1)
os.dup2(fd, 2)
os.execvp(sys.argv[3], sys.argv[3:])
' "$dir" "$log" "$@" >>/dev/null 2>&1 &
}

echo "[1/6] 数据库（PostgreSQL 主库）"
# MySQL 已停用（2026-11-16）：容器 light-mes-db 保留不删，只不再启动，供需要回滚时手工 docker start
docker start light-mes-pg >/dev/null 2>&1
sleep 3
for i in $(seq 1 15); do docker exec light-mes-pg pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
docker exec light-mes-pg pg_isready -U postgres >/dev/null 2>&1 && echo "  5432 OK" || echo "  5432 未就绪"

echo "[2/6] light-mes 后端 :8080"
if ! port_up 8080; then
  run_detached "$L/light-mes/backend" /tmp/lightmes-backend.log \
    "$JAVA_HOME/bin/java" -jar target/light-mes-backend-0.1.0.jar
  for i in $(seq 1 30); do sleep 2; port_up 8080 && break; done
fi
port_up 8080 && echo "  8080 OK" || echo "  8080 未就绪"

echo "[3/6] light-mes 前端 :4173"
if ! port_up 4173; then
  run_detached "$L/light-mes/frontend" /tmp/lightmes-frontend.log \
    npx vite preview --port 4173 --host
  sleep 6
fi
port_up 4173 && echo "  4173 OK" || echo "  4173 未就绪"

echo "[4/6] AI 数字员工 :8000 / :18092"
if ! port_up 8000; then
  run_detached "$L/doc-nexus-ai/backend" /tmp/doc-nexus.log ./.venv/bin/python -m app.server
  for i in $(seq 1 30); do sleep 2; port_up 8000 && break; done
fi
port_up 8000 && echo "  AI 后端 8000 OK" || echo "  AI 后端未就绪"
# 旧门户 :18091 已停用（2026-11-16）：入口、群聊、语音窗都迁到新工作台 :18092
if ! port_up 18092; then
  run_detached "$L" /tmp/desk-web.log node serve-ai-desk.mjs
  sleep 2
fi
port_up 18092 && echo "  AI 新工作台 18092 OK" || echo "  AI 新工作台未就绪"

echo "[5/6] 组织架构图解析 :18093"
# 视觉模型走云端 qwen3-vl-plus（配置在 .env.local 的 LOCAL_VLM_*），不再需要本机 Ollama
if [ -n "${LOCAL_VLM_URL:-}" ]; then
  echo "  视觉模型 ${LOCAL_VLM_MODEL:-?} @ ${LOCAL_VLM_URL}"
else
  echo "  提示：未配置 LOCAL_VLM_URL，18093 会退回本机端点 http://127.0.0.1:11434/v1"
fi
# 只在 MES 配了内部令牌时启动；没配就跳过（Excel 模板导入不受影响）
if [ -n "${MES_INIT_AI_TOKEN:-}" ]; then
  if ! port_up 18093; then
    run_detached "$L/doc-nexus-ai/backend" /tmp/orgchart-local.log \
      /usr/bin/env "ORGCHART_TOKEN=$MES_INIT_AI_TOKEN" ./.venv/bin/python scripts/init_org_chart_local.py
    sleep 3
  fi
  port_up 18093 && echo "  18093 OK（模型 ${LOCAL_VLM_MODEL:-qwen2.5vl:7b} @ ${LOCAL_VLM_URL:-http://127.0.0.1:11434/v1}）" \
    || echo "  18093 未就绪，见 /tmp/orgchart-local.log"
else
  echo "  跳过：未设置 MES_INIT_AI_TOKEN"
fi

echo "[6/6] 常驻检查"
echo "  每个服务都在自己的会话里（setsid），关终端 / 重开 DSH 不会带走它们"
echo "  看会话：ps -o pid,pgid,sess,command -ax | grep -E 'light-mes-backend|vite preview|serve-ai|app.server' | grep -v grep"

echo
echo "MES:        http://localhost:4173   admin / 123456"
echo "AI 数字员工: http://localhost:18092  （新工作台；MES 顶栏入口已指向这里，可单点登录）"
echo "群聊/语音:   http://localhost:18092/group    http://localhost:18092/pod"
echo "AI 接口文档: http://localhost:8000/docs"
