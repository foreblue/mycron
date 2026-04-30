#!/usr/bin/env bash
# backlog 허브에서 /plan-flow 실행
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"

BACKLOG_DIR="/Users/dysim/workspace/backlog"

cd "$BACKLOG_DIR"

echo "[ENGINE] $(flow_engine)"
output=$(run_plan_flow 2>&1)
ec=$?
printf '%s\n' "$output"

if claude_hit_limit <<<"$output"; then
    reset_info=$(extract_limit_reset <<<"$output")
    send_telegram "⚠️ plan-flow 중단: Claude 사용량 한도 도달 (${reset_info:-reset 시각 미상})"
    exit 0
fi

exit "$ec"
