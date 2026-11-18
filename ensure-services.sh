#!/usr/bin/env bash
# 幂等自愈：进 DSH 会话时跑一次，缺啥补啥；全都活着就 50ms 内静默退出。
#
# 由 AGENTS.md 的「开工第一条」规则调用，也可以手动跑。
#   bash ensure-services.sh          # 静默补起（日志 /tmp/dsh-ensure.log）
#   bash ensure-services.sh --check   # 只看状态，不动手
#
# 为什么单独成脚本而不是每次 start-local.sh：
#   start-local.sh 会走完整启动流程（含 30 次端口轮询），服务都在时也要几秒；
#   这里先探端口，全通就直接退出，保证每次开会话的额外成本近乎为零。
set -uo pipefail

# 用户主动 stop 后置了 hold 文件：自愈必须让路，否则会被反复拉起
if [ -f /tmp/lightmes.hold ]; then
  echo "services: hold 存在（用户主动停止过），跳过自愈；要恢复跑 bash start-local.sh"
  exit 0
fi

L="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 需要 .env.local 才能判断 18093 这类"配了才启"的可选件；launchd 起的进程不会自动带上这些变量
[ -f "$L/.env.local" ] && . "$L/.env.local"
export PATH="$HOME/.local/lima/bin:$HOME/.local/bin:$PATH"
LOG=/tmp/dsh-ensure.log
LOCK=/tmp/dsh-ensure.lock
PORTS="5432 8080 4173 8000 18092"
# 组织架构图解析 :18093 是可选件（MES 配了内部令牌才有），配了才纳入体检；
# 视觉模型走云端，不再依赖本机 11434。
if [ -n "${MES_INIT_AI_TOKEN:-}" ]; then
  PORTS="$PORTS 18093"
fi
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

port_up() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

down=""
for p in $PORTS; do
  port_up "$p" || down="$down $p"
done

if [ -z "$down" ]; then
  echo "services: all up ($(echo $PORTS | tr ' ' '/'))"
  exit 0
fi

echo "services: down =$down"
[ "$CHECK_ONLY" = "1" ] && exit 1

# 同一时刻只允许一个自愈在跑（多个会话同时开会话时不打架）
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "services: 另一个自愈进程正在跑（${LOCK}），本次跳过"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

{
  echo
  echo "=== $(date '+%F %T') 自愈开始，缺：$down"
} >>"$LOG"

# PostgreSQL 没起通常是 lima VM 没起，先起虚拟机（start-local.sh 里只做 docker start）
if ! port_up 5432 && command -v limactl >/dev/null 2>&1; then
  echo "  [ensure] lima VM 状态：$(limactl list 2>/dev/null | awk 'NR==2{print $2}')" | tee -a "$LOG" >/dev/null
  limactl start colima >>"$LOG" 2>&1 || true
fi

bash "$L/start-local.sh" >>"$LOG" 2>&1
rc=$?

# start-local.sh 起服务后自己会轮询端口；这里再复核一遍，结果写日志
down2=""
for p in $PORTS; do port_up "$p" || down2="$down2 $p"; done
if [ -z "$down2" ]; then
  echo "services: 已就绪（退出码 ${rc}）" | tee -a "$LOG" >/dev/null
else
  echo "services: 仍有端口未就绪：${down2}（详见 ${LOG}）" | tee -a "$LOG" >/dev/null
fi
exit 0
