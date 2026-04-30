#!/usr/bin/env bash
# backlog/repos.txt 에 등재된 리포를 순회하면서 /dev-flow 실행
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"

WORKSPACE="/Users/dysim/workspace"
REPOS_FILE="${WORKSPACE}/backlog/repos.txt"

if [[ ! -f "$REPOS_FILE" ]]; then
    echo "[ERROR] repos file not found: ${REPOS_FILE}" >&2
    exit 1
fi

REPOS=()
while IFS= read -r repo; do
    [[ -n "$repo" ]] && REPOS+=("$repo")
done < <(awk -F'|' '
    /^[[:space:]]*(#|$)/ { next }
    {
        name = $1
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
        if (name) print name
    }
' "$REPOS_FILE")

if [[ ${#REPOS[@]} -eq 0 ]]; then
    echo "[ERROR] no repos listed in ${REPOS_FILE}" >&2
    exit 1
fi

failed=0

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
    echo "[ENGINE] $(flow_engine)"
    output=$(run_dev_flow 2>&1)
    ec=$?
    printf '%s\n' "$output"

    if claude_hit_limit <<<"$output"; then
        reset_info=$(extract_limit_reset <<<"$output")
        send_telegram "⚠️ dev-flow 중단 (${repo} 처리 중): Claude 사용량 한도 도달 (${reset_info:-reset 시각 미상}). 이후 리포 스킵."
        echo "[ABORT] ${repo}: Claude 한도 도달 — 남은 리포 스킵"
        exit 0
    fi

    if [[ $ec -ne 0 ]]; then
        echo "[ERROR] ${repo}: dev-flow failed (exit ${ec})"
        failed=1
    fi

    echo "[DONE] ${repo}"
    echo ""
done

if [[ $failed -ne 0 ]]; then
    echo "All repos processed with failures."
    exit 1
fi

echo "All repos processed successfully."
