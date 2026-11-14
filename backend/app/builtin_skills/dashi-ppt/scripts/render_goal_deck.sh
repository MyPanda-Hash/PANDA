#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${DASHI_PPT_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../project" && pwd)}"
CALLER_CWD="$(pwd)"

if [[ $# -ne 2 ]]; then
  echo "Usage: render_goal_deck.sh <goal-spec.json> <output/ppt/index.html>" >&2
  exit 2
fi

SPEC_PATH="$1"
OUT_PATH="$2"
[[ "$SPEC_PATH" == /* ]] || SPEC_PATH="$CALLER_CWD/$SPEC_PATH"
[[ "$OUT_PATH" == /* ]] || OUT_PATH="$CALLER_CWD/$OUT_PATH"

# 仅使用工作区副本与镜像依赖，不写批准目录、不联网安装。
if [[ ! -f "$PROJECT_ROOT/node_modules/tsx/package.json" ]]; then
  echo "缺少预装依赖，请先运行 /skills/dashi-ppt/scripts/prepare_runtime.py。" >&2
  exit 1
fi
if [[ ! -x "${CHROME_PATH:-/usr/bin/chromium}" ]]; then
  echo "sandbox 镜像缺少 Chromium，请管理员更新镜像。" >&2
  exit 1
fi
cd "$PROJECT_ROOT"
mkdir -p "$(dirname "$OUT_PATH")"
node scripts/write-safe-props.mjs --goal "$SPEC_PATH" --write
node scripts/validate-goal-spec.mjs "$SPEC_PATH"
./node_modules/.bin/tsx scripts/render-goal-deck.jsx "$SPEC_PATH" "$OUT_PATH"
node scripts/validate-swiss-deck.mjs "$OUT_PATH"
node scripts/validate-goal-copy.mjs "$SPEC_PATH" "$OUT_PATH"
