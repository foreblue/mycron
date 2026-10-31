#!/usr/bin/env bash
# 요청 허브(deepheart-dev) 이슈를 처리한다.
#   1) /inbox-flow 로 새 이슈를 분류한다 — 코멘트 답변 / 대상 리포로 디스패치 / 사람 확인 요청
#   2) dispatched 이슈마다 대상 리포에서 /dev-flow <복사본 번호> 를 돌리고 결과를 허브 이슈에 반영한다
#   3) 끝난 건과 리뷰 미통과·확인 필요만 Telegram 으로 알린다 (dev-flow 자체 알림은 DEV_FLOW_NOTIFY=none 으로 끈다)
# 판단은 Claude 가, 흐름(cd·루프·결과 판정·알림)은 이 스크립트가 맡는다.
# 요청 처리는 Claude 로 고정한다 — ~/.mycron/flow-engine 은 보지 않는다.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"

WORKSPACE="/Users/dysim/workspace"
HUB_REPO="${INBOX_HUB_REPO:-foreblue/deepheart-dev}"
HUB_OWNER="${HUB_REPO%%/*}"
HUB_NAME="${HUB_REPO#*/}"
HUB_DIR="${INBOX_HUB_DIR:-${WORKSPACE}/${HUB_NAME}}"
# 기본은 리뷰 APPROVE 시 머지. 1 이면 머지하지 않고 PR 에 ready-to-merge 라벨만 단다. 배포는 어느 쪽이든 하지 않는다.
INBOX_NO_MERGE="${INBOX_NO_MERGE:-0}"
LOCK_FILE="${HOME}/.mycron/inbox-flow.lock"

LABEL_IN_PROGRESS="in-progress"
LABEL_NEEDS_HUMAN="needs-human"
LABEL_DISPATCHED="dispatched"
LABEL_DONE="done"
LABEL_FAILED="failed"
LABEL_READY="ready-to-merge"
STATE_LABELS_JQ="\"${LABEL_IN_PROGRESS}\",\"${LABEL_NEEDS_HUMAN}\",\"${LABEL_DISPATCHED}\",\"${LABEL_DONE}\",\"${LABEL_FAILED}\""
# gh --jq 결과를 여러 변수로 나눠 읽을 때 쓰는 구분자. 탭은 IFS 공백 문자라 빈 필드(라벨 없음)가 사라진다.
SEP=$'\x1f'

if [[ ! -d "$HUB_DIR" ]]; then
    echo "[ERROR] hub repo not found: ${HUB_DIR}" >&2
    exit 1
fi

# mycron 의 skip-if-running 은 데몬 스케줄에만 적용되고 `mycron run` 수동 실행은 그냥 돈다.
# 둘이 겹치면 같은 이슈에 복사본이 두 개 생기므로 lockf 로 자신을 감싸 다시 실행한다.
# flock 기반이라 프로세스가 죽으면 커널이 락을 푼다. 이미 잡혀 있으면 lockf 는 75(EX_TEMPFAIL) 로 끝난다.
if [[ -z "${INBOX_FLOW_LOCKED:-}" ]]; then
    INBOX_FLOW_LOCKED=1 lockf -s -t 0 "$LOCK_FILE" "$BASH" "$0" "$@"
    ec=$?
    if [[ $ec -eq 75 ]]; then
        echo "[SKIP] another inbox-flow is running (lock: ${LOCK_FILE})"
        exit 0
    fi
    exit "$ec"
fi

kill_stray_orphans 0 "CLEANUP PRE"
install_stray_cleanup_trap

notify() {
    send_telegram "[${HUB_REPO}] $1"
}

# $1 의 쉼표 구분 라벨 목록에 $2 가 있는지.
has_label() {
    [[ ",$1," == *",$2,"* ]]
}

hub_comment() {
    gh issue comment "$1" -R "$HUB_REPO" --body "$2" >/dev/null
}

# 허브 이슈의 "제목␟라벨,목록␟URL".
hub_issue_info() {
    gh issue view "$1" -R "$HUB_REPO" --json title,labels,url \
        --jq '[.title, ([.labels[].name] | join(",")), .url] | join("\u001f")'
}

stop_if_limit() {
    local reset_info

    claude_hit_limit <<<"$1" || return 0
    reset_info=$(extract_limit_reset <<<"$1")
    send_telegram "⚠️ inbox-flow 중단 ($2 중): Claude 사용량 한도 도달 (${reset_info:-reset 시각 미상})"
    exit 0
}

# 이슈별 실패는 여기서 바로 알린다. 스크립트는 0 으로 끝내서 mycron 실패 알림이 겹치지 않게 한다.
mark_failed() {
    local n="$1" reason="$2" title="$3" url="$4"

    echo "[FAILED] #${n}: ${reason}"
    hub_comment "$n" "## ❌ inbox-flow 처리 실패
${reason}

\`mycron logs inbox-flow\` 로 로그를 확인하고, \`${LABEL_FAILED}\` 라벨을 떼면 다음 실행에서 다시 시도합니다."
    gh issue edit "$n" -R "$HUB_REPO" --remove-label "$LABEL_IN_PROGRESS" --add-label "$LABEL_FAILED" >/dev/null
    notify "❌ 처리 실패: #${n} ${title}
${reason}
${url}"
}

# 락을 잡은 시점에 남아 있는 in-progress 는 이전 실행이 죽으며 남긴 것이다.
clear_stale_in_progress() {
    local n

    for n in $(gh issue list -R "$HUB_REPO" --state open --label "$LABEL_IN_PROGRESS" --json number --jq '.[].number'); do
        echo "[CLEANUP] #${n}: stale ${LABEL_IN_PROGRESS} 제거"
        gh issue edit "$n" -R "$HUB_REPO" --remove-label "$LABEL_IN_PROGRESS" >/dev/null
    done
}

# ──────────────────────────── 1) 새 이슈 분류 ────────────────────────────

new_issue_numbers() {
    gh issue list -R "$HUB_REPO" --state open --author "$HUB_OWNER" --limit 100 --json number,labels \
        --jq "map(select(any(.labels[]; .name | IN(${STATE_LABELS_JQ})) | not)) | sort_by(.number) | .[].number"
}

report_classification() {
    local n="$1" title labels url

    IFS="$SEP" read -r title labels url < <(hub_issue_info "$n")

    if has_label "$labels" "$LABEL_DONE"; then
        echo "[COMMENTED] #${n}"
        notify "💬 답변 완료: #${n} ${title}
${url}"
    elif has_label "$labels" "$LABEL_NEEDS_HUMAN"; then
        echo "[NEEDS-HUMAN] #${n}"
        notify "🙋 확인 필요: #${n} ${title}
${url}"
    elif has_label "$labels" "$LABEL_DISPATCHED"; then
        echo "[DISPATCHED] #${n}"
    else
        mark_failed "$n" "분류 단계에서 결과(done / needs-human / dispatched)를 남기지 못했습니다." "$title" "$url"
    fi
}

classify_new_issues() {
    local numbers=() n output

    while IFS= read -r n; do
        [[ -n "$n" ]] && numbers+=("$n")
    done < <(new_issue_numbers)

    if [[ ${#numbers[@]} -eq 0 ]]; then
        echo "[SKIP] 새 이슈 없음"
        return 0
    fi

    echo "[CLASSIFY] ${#numbers[@]}건: ${numbers[*]}"
    output=$(cd "$HUB_DIR" && "$CLAUDE_BIN" --dangerously-skip-permissions -p "/inbox-flow

Batch mode (inbox-flow.sh): process only these hub issues, in this order: ${numbers[*]}. Do not send any Telegram messages. Do not run /dev-flow and do not modify code in any repo — only classify, comment, and dispatch as the skill describes." </dev/null 2>&1)
    printf '%s\n' "$output"
    stop_if_limit "$output" "이슈 분류"

    for n in "${numbers[@]}"; do
        report_classification "$n"
    done
}

# ──────────────────────────── 2) 디스패치 이슈 개발 ────────────────────────────

dispatched_issue_numbers() {
    gh issue list -R "$HUB_REPO" --state open --label "$LABEL_DISPATCHED" --limit 100 --json number,labels \
        --jq "map(select(any(.labels[]; .name | IN(\"${LABEL_NEEDS_HUMAN}\",\"${LABEL_FAILED}\")) | not)) | sort_by(.number) | .[].number"
}

# 허브 소유자가 단 댓글의 <!-- inbox-dispatch: owner/repo#n --> 마커 중 마지막 것.
dispatch_ref() {
    gh issue view "$1" -R "$HUB_REPO" --json comments \
        --jq ".comments[] | select(.author.login == \"${HUB_OWNER}\") | .body" \
        | sed -nE 's/.*<!-- inbox-dispatch: ([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+#[0-9]+) -->.*/\1/p' \
        | tail -1
}

# 대상 리포 이슈의 "STATE␟라벨,목록".
copy_issue_info() {
    gh issue view "$2" -R "$1" --json state,labels --jq '[.state, ([.labels[].name] | join(","))] | join("\u001f")'
}

# dev-flow 가 복사본 이슈로 연 PR 번호 — dev-flow 규칙의 issue-<n> 브랜치로 찾는다.
# 공개 리포에서는 남도 포크에서 같은 이름의 브랜치나 "Closes #n" 으로 PR 을 열 수 있으므로,
# 같은 리포 브랜치에서 허브 소유자가 연 PR 만 센다.
linked_pr() {
    local repo="$1" n="$2"

    gh pr list -R "$repo" --head "issue-${n}" --state all --limit 20 \
        --json number,author,isCrossRepository \
        --jq "[.[] | select((.isCrossRepository | not) and .author.login == \"${HUB_OWNER}\")] | .[0].number // empty"
}

# PR 의 "STATE␟라벨,목록␟URL".
pr_info() {
    gh pr view "$2" -R "$1" --json state,labels,url --jq '[.state, ([.labels[].name] | join(",")), .url] | join("\u001f")'
}

# 허브 소유자가 단 마지막 댓글의 앞부분 — dev-flow 가 needs-human 을 붙이며 남긴 사유를 알림에 싣는다.
# 작성자를 거르지 않으면 공개 리포에서 남이 그 사이에 단 글이 Telegram 알림으로 온다.
last_comment_excerpt() {
    gh issue view "$2" -R "$1" --json comments \
        --jq "[.comments[] | select(.author.login == \"${HUB_OWNER}\")] | last | .body // \"\" | .[:500]"
}

run_inbox_dev_flow() {
    local issue="$1" merge_rule

    if [[ "$INBOX_NO_MERGE" == "1" ]]; then
        merge_rule=" DEV_FLOW_NO_MERGE=1 is set: never run gh pr merge. When review approves, add the ${LABEL_READY} label to the PR and leave it open for a human to merge."
    else
        merge_rule=" Merge the PR when review approves."
    fi

    DEV_FLOW_DEPLOY=0 DEV_FLOW_NOTIFY=none DEV_FLOW_NO_MERGE="$INBOX_NO_MERGE" DEV_FLOW_REQUIRE_APPROVE=1 \
        "$CLAUDE_BIN" --dangerously-skip-permissions -p "/dev-flow ${issue}

Batch mode override (inbox-flow): DEV_FLOW_DEPLOY=0, DEV_FLOW_NOTIFY=none and DEV_FLOW_REQUIRE_APPROVE=1 are set. Do not run deploy.sh. Do not send any Telegram messages — inbox-flow sends the completion notice.${merge_rule} Never merge without a review that returned VERDICT: APPROVE: if the 3rd review still requests changes, skip consensus and handle it as ABORT (PR stays open, needs-human on the issue, unresolved findings commented on both). The PR body must contain \"Closes #${issue}\". If analysis ends in ACTION: COMMENT, always close the issue after commenting — inbox-flow reads an open issue with no PR and no needs-human label as a failure." </dev/null
}

# 머지는 원격에서만 일어나므로 원래 체크아웃의 기본 브랜치를 원격에 맞춘다.
# 기본 브랜치에 있고 추적 파일 변경이 없을 때만 fast-forward 한다. 추적 안 되는 파일은 git 이 덮어쓰기 전에 멈추므로 보지 않는다.
sync_local_default() {
    local repo="$1" dir="$2" base current

    base=$(gh repo view "$repo" --json defaultBranchRef --jq .defaultBranchRef.name) || return 0
    current=$(git -C "$dir" branch --show-current)
    if [[ "$current" != "$base" ]]; then
        echo "[SYNC SKIP] ${repo}: 현재 브랜치 ${current:-detached} (${base} 아님)"
    elif [[ -n "$(git -C "$dir" status --porcelain --untracked-files=no)" ]]; then
        echo "[SYNC SKIP] ${repo}: 커밋 안 된 변경 있음"
    elif git -C "$dir" pull -q --ff-only origin "$base"; then
        echo "[SYNC] ${repo}: ${base} → $(git -C "$dir" rev-parse --short HEAD)"
    else
        echo "[SYNC SKIP] ${repo}: ${base} fast-forward 불가"
    fi
}

finish_dispatched() {
    local n="$1" headline="$2" detail="$3" title="$4"

    echo "[DONE] #${n}: ${headline}"
    hub_comment "$n" "## ${headline}
${detail}

---
_Inbox Flow_"
    gh issue edit "$n" -R "$HUB_REPO" --remove-label "$LABEL_DISPATCHED" --add-label "$LABEL_DONE" >/dev/null
    gh issue close "$n" -R "$HUB_REPO" >/dev/null
    notify "${headline}: #${n} ${title}
${detail}"
}

process_dispatched() {
    local n="$1" title labels url ref target_repo target_n repo_name repo_dir
    local copy_state copy_labels copy_url pr pr_state="" pr_labels="" pr_url="" output reason

    IFS="$SEP" read -r title labels url < <(hub_issue_info "$n")

    ref="$(dispatch_ref "$n")"
    if [[ -z "$ref" ]]; then
        mark_failed "$n" "디스패치 마커(<!-- inbox-dispatch: owner/repo#n -->)가 없습니다." "$title" "$url"
        return
    fi

    target_repo="${ref%#*}"
    target_n="${ref##*#}"
    repo_name="${target_repo#*/}"
    repo_dir="${WORKSPACE}/${repo_name}"
    copy_url="https://github.com/${target_repo}/issues/${target_n}"

    if [[ "${target_repo%%/*}" != "$HUB_OWNER" || "$repo_name" == "$HUB_NAME" ]] \
        || ! git -C "$repo_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        mark_failed "$n" "대상 ${target_repo} 는 ${WORKSPACE} 아래 ${HUB_OWNER} 리포가 아닙니다." "$title" "$url"
        return
    fi

    IFS="$SEP" read -r copy_state copy_labels < <(copy_issue_info "$target_repo" "$target_n")
    if has_label "$copy_labels" "$LABEL_NEEDS_HUMAN"; then
        echo "[WAIT] #${n} → ${ref}: ${LABEL_NEEDS_HUMAN}"
        return
    fi

    pr="$(linked_pr "$target_repo" "$target_n")"
    [[ -n "$pr" ]] && IFS="$SEP" read -r pr_state pr_labels pr_url < <(pr_info "$target_repo" "$pr")

    # 이전 실행이 dev-flow 를 끝내고 허브 반영 전에 죽었으면 다시 돌리지 않는다.
    if [[ "$copy_state" == "OPEN" ]] && ! { [[ "$pr_state" == "OPEN" ]] && has_label "$pr_labels" "$LABEL_READY"; }; then
        echo "[DEV-FLOW] #${n} → ${ref}"
        output=$(cd "$repo_dir" && run_inbox_dev_flow "$target_n" 2>&1)
        printf '%s\n' "$output"
        stop_if_limit "$output" "${ref} 개발"

        IFS="$SEP" read -r copy_state copy_labels < <(copy_issue_info "$target_repo" "$target_n")
        pr="$(linked_pr "$target_repo" "$target_n")"
        pr_state="" pr_labels="" pr_url=""
        [[ -n "$pr" ]] && IFS="$SEP" read -r pr_state pr_labels pr_url < <(pr_info "$target_repo" "$pr")
    fi

    if has_label "$copy_labels" "$LABEL_NEEDS_HUMAN"; then
        reason="$(last_comment_excerpt "$target_repo" "$target_n")"
        # PR 이 열린 채 needs-human 이면 3차 리뷰까지 APPROVE 를 못 받아 멈춘 것이다 (DEV_FLOW_REQUIRE_APPROVE=1).
        if [[ "$pr_state" == "OPEN" ]]; then
            echo "[REVIEW-FAILED] #${n} → ${ref}"
            hub_comment "$n" "## ⛔ 리뷰 미통과 — 머지하지 않음
PR: ${pr_url}
리뷰 3차까지 통과하지 못해 PR 을 열어 두었습니다. 미해결 사항은 PR 과 ${copy_url} 에 있습니다.
그 이슈에서 조치하거나 답한 뒤 \`${LABEL_NEEDS_HUMAN}\` 라벨을 떼면 다음 배치 실행에서 이어서 진행합니다."
            notify "⛔ 리뷰 미통과: #${n} ${title}
PR: ${pr_url}

${reason}

${copy_url} 에서 확인 후 ${LABEL_NEEDS_HUMAN} 라벨을 떼면 재개"
        else
            echo "[NEEDS-HUMAN] #${n} → ${ref}"
            hub_comment "$n" "## 🙋 개발 중 확인 필요
${copy_url} 에서 사람 확인을 요청했습니다.
그 이슈에서 답하고 \`${LABEL_NEEDS_HUMAN}\` 라벨을 떼면 다음 배치 실행에서 이어서 진행합니다."
            notify "🙋 개발 중 확인 필요: #${n} ${title}

${reason}

${copy_url}"
        fi
    elif [[ "$pr_state" == "MERGED" ]]; then
        sync_local_default "$target_repo" "$repo_dir"
        finish_dispatched "$n" "✅ 머지 완료" "PR: ${pr_url}" "$title"
    elif [[ "$pr_state" == "OPEN" ]] && has_label "$pr_labels" "$LABEL_READY"; then
        finish_dispatched "$n" "✅ 리뷰 통과, 머지 대기" "PR: ${pr_url}" "$title"
    elif [[ "$copy_state" == "CLOSED" ]]; then
        finish_dispatched "$n" "💬 개발 없이 종료" "대상 이슈: ${copy_url}" "$title"
    else
        mark_failed "$n" "dev-flow 가 끝났지만 머지된 PR 도, 사람 확인 요청도 없습니다. 대상 이슈: ${copy_url}${pr_url:+, PR: ${pr_url}}" "$title" "$url"
    fi
}

# ──────────────────────────── main ────────────────────────────

clear_stale_in_progress
classify_new_issues

# claude 가 stdin 을 읽지 않도록 목록을 먼저 다 받아 둔다.
DISPATCHED=()
while IFS= read -r n; do
    [[ -n "$n" ]] && DISPATCHED+=("$n")
done < <(dispatched_issue_numbers)

# bash 3.2 는 set -u 에서 빈 배열 전개를 unbound 로 본다.
if [[ ${#DISPATCHED[@]} -eq 0 ]]; then
    echo "[SKIP] 개발 대기 이슈 없음"
else
    for n in "${DISPATCHED[@]}"; do
        process_dispatched "$n"
    done
fi

echo "inbox-flow done."
