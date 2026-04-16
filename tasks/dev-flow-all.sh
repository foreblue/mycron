#!/usr/bin/env bash
# 7개 리포를 순회하면서 이슈를 확인하고 /dev-flow 실행
set -euo pipefail

CLAUDE="/Users/dysim/.local/bin/claude"
WORKSPACE="/Users/dysim/workspace"
REPOS=(
    golf-memo
    deepheart-gw
    telegram-receiver
    cube-solver
    unified-proxy
    stueng
    mycron
)

for repo in "${REPOS[@]}"; do
    repo_dir="${WORKSPACE}/${repo}"
    if [[ ! -d "$repo_dir" ]]; then
        echo "[SKIP] ${repo}: directory not found"
        continue
    fi

    echo "=========================================="
    echo "[START] ${repo}"
    echo "=========================================="

    cd "$repo_dir"
    "$CLAUDE" --dangerously-skip-permissions -p "/dev-flow" || {
        echo "[ERROR] ${repo}: dev-flow failed (exit $?)"
    }

    echo "[DONE] ${repo}"
    echo ""
done

echo "All repos processed."
