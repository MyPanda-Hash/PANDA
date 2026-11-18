#!/usr/bin/env bash
# ============================================================================
# 一键启动 light-mes + AI 数字员工（macOS）
#
# 用法：
#   bash start-all.sh            启动全部（MySQL 需先运行）
#   bash start-all.sh check      只做环境检查，不启动
#   bash start-all.sh stop       停止本项目启动的进程
#
# 前置：见 README-MAC.md。脚本只负责启动，不安装依赖。
# ============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MES_DIR="$ROOT/light-mes"
AI_DIR="$ROOT/doc-nexus-ai"
RUN_DIR="$ROOT/.run"
mkdir -p "$RUN_DIR"

# ---------- 默认端口（可用环境变量覆盖）----------
: "${MES_DB_PORT:=3306}"
: "${MES_HTTP_PORT:=8080}"
: "${MES_PREVIEW_PORT:=4173}"
: "${AI_PORT:=18091}"

red()  { printf '\033[31m%s\033[0m\n' "$*"; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
ylw()  { printf '\033[33m%s\033[0m\n' "$*"; }
say()  { printf '  %s\n' "$*"; }

port_open() {
  nc -z 127.0.0.1 "$1" >/dev/null 2>&1
}

# ---------- check ----------
do_check() {
  local fail=0
  echo "== 环境检查 =="

  # Java 17+
  if command -v java >/dev/null 2>&1; then
    local jv; jv="$(java -version 2>&1 | head -1)"
    say "Java: $jv"
  else
    red "  缺少 Java。brew install --cask temurin@17"; fail=1
  fi

  # Node 20+
  if command -v node >/dev/null 2>&1; then
    say "Node: $(node -v)"
  else
    red "  缺少 Node。brew install node@20"; fail=1
  fi

  # Maven（项目内 Windows 版不可用，macOS 需自备）
  if command -v mvn >/dev/null 2>&1; then
    say "Maven: $(mvn -v 2>/dev/null | head -1)"
  else
    ylw "  未找到 mvn。只有需要重新打包后端时必需：brew install maven"
  fi

  # MySQL
  if port_open "$MES_DB_PORT"; then
    say "MySQL: 127.0.0.1:$MES_DB_PORT 可连接"
  else
    red "  MySQL 未在 $MES_DB_PORT 运行。brew services start mysql"; fail=1
  fi

  # 环境变量
  [ -n "${SPRING_DATASOURCE_PASSWORD:-}" ] || { red "  未设置 SPRING_DATASOURCE_PASSWORD"; fail=1; }
  [ -n "${MES_JWT_SECRET:-}" ] || { red "  未设置 MES_JWT_SECRET（≥64 位随机串）"; fail=1; }
  [ -n "${MES_AI_SSO_SECRET:-}" ] || { ylw "  未设置 MES_AI_SSO_SECRET（AI 单点登录会降级）"; }

  # AI 目录
  if [ -d "$AI_DIR" ]; then
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
      say "Docker: 引擎可用"
    else
      ylw "  Docker 未运行（AI 服务走 docker compose，需先启动 Docker Desktop）"
    fi
    [ -f "$AI_DIR/.env" ] && say "AI .env: 已存在" || { ylw "  AI 缺少 .env，请 cp .env.example .env 后填值"; }
  fi

  return $fail
}

# ---------- start ----------
do_start() {
  # 后端 jar 是否已构建
  local jar="$MES_DIR/backend/target/light-mes-backend-0.1.0.jar"
  if [ ! -f "$jar" ]; then
    ylw "未找到后端 jar，先打包..."
    ( cd "$MES_DIR/backend" && mvn -q clean package -DskipTests ) || { red "后端打包失败"; return 1; }
  fi

  # 前端 dist 是否已构建
  if [ ! -f "$MES_DIR/frontend/dist/index.html" ]; then
    ylw "未找到前端构建产物，先构建..."
    ( cd "$MES_DIR/frontend" && npm ci && npm run build ) || { red "前端构建失败"; return 1; }
  fi

  # 后端
  if port_open "$MES_HTTP_PORT"; then
    grn "后端已在 $MES_HTTP_PORT 运行"
  else
    echo "启动后端 ($MES_HTTP_PORT)..."
    ( cd "$MES_DIR/backend" && nohup java -jar "$jar" > "$RUN_DIR/backend.log" 2>&1 & echo $! > "$RUN_DIR/backend.pid" )
    for _ in $(seq 1 60); do port_open "$MES_HTTP_PORT" && break; sleep 2; done
    port_open "$MES_HTTP_PORT" && grn "后端就绪" || { red "后端启动失败，看 $RUN_DIR/backend.log"; tail -20 "$RUN_DIR/backend.log"; }
  fi

  # 前端预览
  if port_open "$MES_PREVIEW_PORT"; then
    grn "前端已在 $MES_PREVIEW_PORT 运行"
  else
    echo "启动前端预览 ($MES_PREVIEW_PORT)..."
    ( cd "$MES_DIR/frontend" && nohup npx vite preview --port "$MES_PREVIEW_PORT" > "$RUN_DIR/frontend.log" 2>&1 & echo $! > "$RUN_DIR/frontend.pid" )
    sleep 5
    port_open "$MES_PREVIEW_PORT" && grn "前端就绪" || red "前端启动失败，看 $RUN_DIR/frontend.log"
  fi

  # AI（docker compose）
  if [ -d "$AI_DIR" ] && [ -f "$AI_DIR/docker-compose.yml" ]; then
    if docker info >/dev/null 2>&1; then
      echo "启动 AI 数字员工..."
      ( cd "$AI_DIR" && docker compose up -d ) || ylw "AI 启动失败（不影响 MES）"
      for _ in $(seq 1 30); do
        curl -fsS "http://localhost:$AI_PORT/api/health" >/dev/null 2>&1 && break; sleep 3
      done
      curl -fsS "http://localhost:$AI_PORT/api/health" >/dev/null 2>&1 \
        && grn "AI 就绪：http://localhost:$AI_PORT" \
        || ylw "AI 尚未就绪（首次启动拉镜像较慢，稍后重试）"
    else
      ylw "Docker 未运行，跳过 AI"
    fi
  fi

  echo
  grn "MES:    http://localhost:$MES_PREVIEW_PORT"
  [ -f "$AI_DIR/docker-compose.yml" ] && echo "AI:     http://localhost:$AI_PORT"
  echo "日志:   $RUN_DIR/"
}

do_stop() {
  for name in backend frontend; do
    pidfile="$RUN_DIR/$name.pid"
    if [ -f "$pidfile" ]; then
      pid="$(cat "$pidfile")"
      kill "$pid" 2>/dev/null && grn "已停止 $name (pid $pid)"
      rm -f "$pidfile"
    fi
  done
  if [ -d "$AI_DIR" ] && docker info >/dev/null 2>&1; then
    ( cd "$AI_DIR" && docker compose stop ) && grn "已停止 AI 容器"
  fi
}

case "${1:-run}" in
  check) do_check ;;
  stop)  do_stop ;;
  run|"") do_check || ylw "检查有未通过项，仍尝试启动"; echo; do_start ;;
  *) echo "用法: bash start-all.sh [check|stop]"; exit 1 ;;
esac
