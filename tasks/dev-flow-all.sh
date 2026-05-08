#!/usr/bin/env bash
# backlog/repos.txt 에 등재된 리포를 순회하면서 /dev-flow 실행
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"

WORKSPACE="/Users/dysim/workspace"
REPOS_FILE="${WORKSPACE}/backlog/repos.txt"
NEEDS_HUMAN_LABEL="needs-human"
QA_RECORD_LABEL="qa-record"
QA_FLOW_GATE="${QA_FLOW_GATE:-/Users/dysim/mylogs/codex/skills/qa-flow/scripts/qa-regression-gate.sh}"
QA_ARTIFACT_ROOT_DEFAULT="${WORKSPACE}/qa-artifacts"
QA_ARTIFACT_BASE_URL_DEFAULT="https://artifacts.deepheart.duckdns.org"
QA_SCRIPT_NAMES=(
    "qa:regression"
    "test:e2e:regression"
    "e2e:regression"
    "test:regression"
    "test:e2e"
    "e2e"
)
QA_ROOT_CANDIDATES=(
    "."
    "web"
    "frontend"
    "client"
    "app"
    "apps/web"
)

has_eligible_issue() {
    local issue_count

    if ! command -v gh >/dev/null 2>&1; then
        echo "[WARN] gh not found; cannot pre-filter issues by ${NEEDS_HUMAN_LABEL}/${QA_RECORD_LABEL}" >&2
        return 0
    fi

    if ! issue_count=$(gh issue list \
        --state open \
        --limit 1000 \
        --json labels \
        --jq "map(select(([.labels[].name] | index(\"${NEEDS_HUMAN_LABEL}\") | not) and ([.labels[].name] | index(\"${QA_RECORD_LABEL}\") | not))) | length" 2>/dev/null); then
        echo "[WARN] failed to fetch issues; running dev-flow without pre-filter" >&2
        return 0
    fi

    [[ "$issue_count" -gt 0 ]]
}

package_has_qa_script() {
    local dir="$1"
    local package_json="${dir}/package.json"

    [[ -f "$package_json" ]] || return 1

    "$PYTHON_BIN" - "$package_json" "${QA_SCRIPT_NAMES[@]}" <<'PY'
import json
import sys

package_json = sys.argv[1]
script_names = sys.argv[2:]

try:
    with open(package_json, "r", encoding="utf-8") as file:
        package = json.load(file)
except Exception:
    sys.exit(1)

scripts = package.get("scripts") or {}
sys.exit(0 if any(name in scripts for name in script_names) else 1)
PY
}

has_qa_signal() {
    local dir="$1"

    [[ -x "${dir}/qa-regression.sh" ]] && return 0
    package_has_qa_script "$dir" && return 0

    find "$dir" -maxdepth 1 \( -name 'playwright.config.*' -o -name 'cypress.config.*' \) -print -quit | grep -q .
}

short_sha() {
    local repo_dir="$1"

    git -C "$repo_dir" rev-parse --short HEAD 2>/dev/null || printf 'no-git'
}

run_qa_flow_gates() {
    local repo="$1"
    local repo_dir="$2"

    if [[ "${QA_FLOW_AFTER_DEV_FLOW:-1}" == "0" ]]; then
        echo "[QA SKIP] ${repo}: QA_FLOW_AFTER_DEV_FLOW=0"
        return 0
    fi

    if [[ ! -x "$QA_FLOW_GATE" ]]; then
        echo "[QA WARN] ${repo}: qa-flow gate not executable: ${QA_FLOW_GATE}" >&2
        return 0
    fi

    local qa_roots=()
    local candidate candidate_dir
    for candidate in "${QA_ROOT_CANDIDATES[@]}"; do
        candidate_dir="${repo_dir}/${candidate}"
        [[ -d "$candidate_dir" ]] || continue
        if has_qa_signal "$candidate_dir"; then
            qa_roots+=("$candidate_dir")
        fi
    done

    if [[ ${#qa_roots[@]} -eq 0 ]]; then
        echo "[QA SKIP] ${repo}: no QA regression command detected"
        return 0
    fi

    local qa_failed=0
    local qa_root root_label run_id output qa_ec report_url
    for qa_root in "${qa_roots[@]}"; do
        if [[ "$qa_root" == "$repo_dir/." || "$qa_root" == "$repo_dir" ]]; then
            root_label="root"
        else
            root_label="${qa_root#${repo_dir}/}"
            root_label="${root_label//\//-}"
        fi

        run_id="$(date +%Y%m%d-%H%M%S)-$(short_sha "$repo_dir")-${root_label}"

        echo "[QA START] ${repo}:${root_label}"
        output=$(
            QA_ARTIFACT_ROOT="${QA_ARTIFACT_ROOT:-$QA_ARTIFACT_ROOT_DEFAULT}" \
            QA_ARTIFACT_BASE_URL="${QA_ARTIFACT_BASE_URL:-$QA_ARTIFACT_BASE_URL_DEFAULT}" \
            QA_ARTIFACT_RUN_ID="$run_id" \
                "$QA_FLOW_GATE" "$qa_root" 2>&1
        )
        qa_ec=$?
        printf '%s\n' "$output"

        report_url="$(grep -Eo 'https?://[^[:space:]]+/report\.html' <<<"$output" | tail -1 || true)"

        if [[ $qa_ec -ne 0 ]]; then
            echo "[QA ERROR] ${repo}:${root_label}: qa-flow failed (exit ${qa_ec})"
            if [[ -n "$report_url" ]]; then
                send_telegram "⚠️ QA regression 실패 (${repo}:${root_label}) — ${report_url}"
            else
                send_telegram "⚠️ QA regression 실패 (${repo}:${root_label})"
            fi
            qa_failed=1
        else
            echo "[QA DONE] ${repo}:${root_label}"
        fi
    done

    return "$qa_failed"
}

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
    if ! has_eligible_issue; then
        echo "[SKIP] ${repo}: no open issues without ${NEEDS_HUMAN_LABEL}/${QA_RECORD_LABEL}"
        echo ""
        continue
    fi

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
    elif ! run_qa_flow_gates "$repo" "$repo_dir"; then
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
