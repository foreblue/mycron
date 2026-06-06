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
QA_ARTIFACT_ROOT_DEFAULT="${WORKSPACE}/artifacts"
QA_ARTIFACT_BASE_URL_DEFAULT="https://artifacts.deepheart.duckdns.org"
DEV_FLOW_ALL_EXCLUDED_REPOS="${DEV_FLOW_ALL_EXCLUDED_REPOS-restaurant-service}"
#DEV_FLOW_ALL_EXCLUDED_REPOS=""
QA_FAILURE_LABELS=(
    "qa-record"
    "qa-regression"
    "qa-failure"
    "release-blocker"
)
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

is_excluded_repo() {
    local repo="$1"
    local excluded excluded_repos
    local excluded_repo_list=()
    excluded_repos="${DEV_FLOW_ALL_EXCLUDED_REPOS//,/ }"

    read -r -a excluded_repo_list <<< "$excluded_repos"
    for excluded in "${excluded_repo_list[@]}"; do
        [[ "$repo" == "$excluded" ]] && return 0
    done

    return 1
}

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

default_branch() {
    local repo_dir="$1"
    local branch

    if command -v gh >/dev/null 2>&1; then
        branch=$(cd "$repo_dir" && gh repo view --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null || true)
        if [[ -n "$branch" ]]; then
            printf '%s\n' "$branch"
            return 0
        fi
    fi

    branch=$(git -C "$repo_dir" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || true)
    branch="${branch#origin/}"
    printf '%s\n' "${branch:-main}"
}

remote_branch_sha() {
    local repo_dir="$1"
    local branch="$2"

    git -C "$repo_dir" ls-remote --heads origin "$branch" 2>/dev/null | awk '{print $1}'
}

has_merge_signal() {
    grep -Eiq '(gh pr merge|머지 완료|merge completed|merged pull request|PR #[0-9]+[[:space:]]*(->|→)[[:space:]]*)'
}

dev_flow_merged() {
    local before_sha="$1"
    local after_sha="$2"

    if [[ -n "$before_sha" && -n "$after_sha" && "$before_sha" != "$after_sha" ]]; then
        return 0
    fi

    has_merge_signal
}

sync_default_branch() {
    local repo="$1"
    local repo_dir="$2"
    local branch="$3"
    local current_branch

    if ! git -C "$repo_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "[QA WARN] ${repo}: not a git repository; cannot sync default branch" >&2
        return 0
    fi

    if [[ -n "$(git -C "$repo_dir" status --porcelain)" ]]; then
        echo "[QA ERROR] ${repo}: working tree is dirty; cannot sync ${branch} before QA" >&2
        return 1
    fi

    git -C "$repo_dir" fetch origin "$branch" || return 1

    current_branch="$(git -C "$repo_dir" branch --show-current 2>/dev/null || true)"
    if [[ "$current_branch" != "$branch" ]]; then
        if git -C "$repo_dir" show-ref --verify --quiet "refs/heads/${branch}"; then
            git -C "$repo_dir" checkout "$branch" || return 1
        else
            git -C "$repo_dir" checkout -b "$branch" --track "origin/${branch}" || return 1
        fi
    fi

    git -C "$repo_dir" pull --ff-only origin "$branch"
}

ensure_qa_labels() {
    local repo_dir="$1"

    if ! command -v gh >/dev/null 2>&1; then
        return 1
    fi

    (
        cd "$repo_dir" || exit 1
        gh label create qa-record --color 6f42c1 --description "QA evidence/result record" 2>/dev/null || true
        gh label create qa-regression --color 1d76db --description "QA regression gate result" 2>/dev/null || true
        gh label create qa-failure --color d73a4a --description "QA regression failure" 2>/dev/null || true
        gh label create release-blocker --color b60205 --description "Blocks deployment or release" 2>/dev/null || true
    )
}

create_qa_failure_issue() {
    local repo="$1"
    local repo_dir="$2"
    local root_label="$3"
    local run_id="$4"
    local qa_ec="$5"
    local output="$6"
    local report_url="$7"
    local artifact_path="$8"
    local body_file issue_body_file title create_output create_ec issue_url output_tail
    local label_args=()
    local label

    if ! command -v gh >/dev/null 2>&1; then
        echo "[QA WARN] ${repo}:${root_label}: gh not found; cannot create QA failure issue" >&2
        return 1
    fi

    ensure_qa_labels "$repo_dir" || true

    body_file="$(mktemp)"
    issue_body_file="${artifact_path}/issue-body.md"

    if [[ -n "$artifact_path" && -f "$issue_body_file" ]]; then
        cp "$issue_body_file" "$body_file"
        {
            printf '\n---\n\n'
            printf -- '- QA root: `%s`\n' "$root_label"
            printf -- '- Run ID: `%s`\n' "$run_id"
            printf -- '- Exit status: `%s`\n' "$qa_ec"
        } >> "$body_file"
    else
        output_tail="$(tail -40 <<<"$output")"
        {
            printf '## QA Regression Result\n\n'
            printf -- '- Repository: `%s`\n' "$repo"
            printf -- '- QA root: `%s`\n' "$root_label"
            printf -- '- Run ID: `%s`\n' "$run_id"
            printf -- '- Commit: `%s`\n' "$(short_sha "$repo_dir")"
            printf -- '- Status: `failed`\n'
            printf -- '- Deployment: `blocked`\n'
            printf -- '- Exit status: `%s`\n' "$qa_ec"
            if [[ -n "$report_url" ]]; then
                printf '\nQA report: %s\n' "$report_url"
            else
                printf '\nQA report: not available\n'
                printf '\n### Last QA output\n\n```text\n%s\n```\n' "$output_tail"
            fi
        } > "$body_file"
    fi

    for label in "${QA_FAILURE_LABELS[@]}"; do
        label_args+=(--label "$label")
    done

    title="QA regression 실패: ${root_label} (${run_id})"
    create_output=$(cd "$repo_dir" && gh issue create --title "$title" --body-file "$body_file" "${label_args[@]}" 2>&1)
    create_ec=$?

    if [[ $create_ec -ne 0 ]]; then
        create_output=$(cd "$repo_dir" && gh issue create --title "$title" --body-file "$body_file" 2>&1)
        create_ec=$?
    fi

    rm -f "$body_file"

    if [[ $create_ec -ne 0 ]]; then
        echo "[QA WARN] ${repo}:${root_label}: failed to create QA issue"
        printf '%s\n' "$create_output"
        return "$create_ec"
    fi

    issue_url="$(tail -1 <<<"$create_output")"
    echo "[QA ISSUE] ${repo}:${root_label}: ${issue_url}"
    send_telegram "QA regression 실패 이슈 등록 (${repo}:${root_label}) — ${issue_url}"
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
    local qa_root root_label run_id output qa_ec report_url artifact_path
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
        artifact_path="$(sed -n 's/^\[qa-flow\] artifact path: //p' <<<"$output" | tail -1 || true)"

        if [[ $qa_ec -ne 0 ]]; then
            echo "[QA ERROR] ${repo}:${root_label}: qa-flow failed (exit ${qa_ec})"
            create_qa_failure_issue "$repo" "$repo_dir" "$root_label" "$run_id" "$qa_ec" "$output" "$report_url" "$artifact_path" || true
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

run_deploy() {
    local repo="$1"
    local repo_dir="$2"
    local output deploy_ec output_tail

    if [[ "${DEPLOY_AFTER_QA:-1}" == "0" ]]; then
        echo "[DEPLOY SKIP] ${repo}: DEPLOY_AFTER_QA=0"
        return 0
    fi

    if [[ ! -x "${repo_dir}/deploy.sh" ]]; then
        echo "[DEPLOY SKIP] ${repo}: executable deploy.sh not found"
        return 0
    fi

    echo "[DEPLOY START] ${repo}"
    output=$(cd "$repo_dir" && bash ./deploy.sh 2>&1)
    deploy_ec=$?
    printf '%s\n' "$output"

    if [[ $deploy_ec -ne 0 ]]; then
        echo "[DEPLOY ERROR] ${repo}: deploy.sh failed (exit ${deploy_ec})"
        output_tail="$(tail -20 <<<"$output")"
        send_telegram "배포 실패 (${repo}, exit ${deploy_ec})

마지막 로그:
${output_tail}"
        return "$deploy_ec"
    fi

    echo "[DEPLOY DONE] ${repo}"
    send_telegram "배포 완료 (${repo})"
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
    if is_excluded_repo "$repo"; then
        echo "[SKIP] ${repo}: excluded from dev-flow-all"
        continue
    fi

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
    default_branch_name="$(default_branch "$repo_dir")"
    before_sha="$(remote_branch_sha "$repo_dir" "$default_branch_name")"
    output=$(run_dev_flow 2>&1)
    ec=$?
    after_sha="$(remote_branch_sha "$repo_dir" "$default_branch_name")"
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
    elif ! dev_flow_merged "$before_sha" "$after_sha" <<<"$output"; then
        echo "[DEPLOY SKIP] ${repo}: no merged PR detected"
    elif ! sync_default_branch "$repo" "$repo_dir" "$default_branch_name"; then
        echo "[ERROR] ${repo}: failed to sync ${default_branch_name} before QA"
        failed=1
    elif ! run_qa_flow_gates "$repo" "$repo_dir"; then
        echo "[DEPLOY BLOCKED] ${repo}: QA regression failed"
        failed=1
    elif ! run_deploy "$repo" "$repo_dir"; then
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
