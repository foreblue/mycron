#!/usr/bin/env bash
# backlog 허브에서 /plan-flow 실행
set -euo pipefail

CLAUDE="/Users/dysim/.local/bin/claude"
BACKLOG_DIR="/Users/dysim/workspace/backlog"

cd "$BACKLOG_DIR"
"$CLAUDE" --dangerously-skip-permissions -p "/plan-flow"
