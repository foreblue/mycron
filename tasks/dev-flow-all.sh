#!/usr/bin/env bash
# backlog/repos.txt 에 등재된 리포를 순회하면서 /dev-flow 실행
set -euo pipefail

CLAUDE="/Users/dysim/.local/bin/claude"
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
