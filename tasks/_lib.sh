#!/usr/bin/env bash
# flow 스크립트 공용 헬퍼.
# source 해서 사용한다: source "$(dirname "$0")/_lib.sh"

CLAUDE_BIN="/Users/dysim/.local/bin/claude"
CODEX_BIN="/opt/homebrew/bin/codex"
PYTHON_BIN="/Users/dysim/workspace/mycron/.venv/bin/python3"
FLOW_ENGINE_FILE="/Users/dysim/.mycron/flow-engine"
# Claude: "You've hit your limit" / "You've hit your session limit"
#         / "You've hit your weekly limit" / "You've hit your 5-hour limit"
# Codex: "You've hit your usage limit"
# 한도 종류가 계속 늘어나므로 수식어를 한 단어 와일드카드로 받는다.
LIMIT_MARKER_REGEX="You've hit your ([a-z0-9-]+ )?limit"

# LaunchAgent/mycron daemon environments are intentionally sparse on macOS.
# Codex is installed under Homebrew and uses `#!/usr/bin/env node`, so node
# must be discoverable through PATH even when the daemon starts with
# `/usr/bin:/bin:/usr/sbin:/sbin`.
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"

# claude/codex 의 Bash 툴은 자식을 별도 프로세스 그룹으로 띄운다. 그래서
# mycron 이 타임아웃으로 killpg 를 보내도 그 자손들은 살아남아 PPID=1 고아가
# 된다. 실제로 `xcodebuild -runFirstLaunch` 가 sudo/라이선스 입력을 기다리며
# 며칠씩 쌓인 적이 있어, flow 스크립트가 직접 정리한다.
# flutter_tester 는 `flutter test` 가 끊기면 남는다(spider 에서 27일짜리가 발견됨).
STRAY_PROCESS_REGEX="${STRAY_PROCESS_REGEX:-xcodebuild -runFirstLaunch|simctl list devices|flutter_tester}"
STRAY_KILL_GRACE_SECONDS="${STRAY_KILL_GRACE_SECONDS:-2}"
_STRAY_CLEANUP_DONE=0
_STRAY_CLEANUP_SINCE=0

# ps 의 etime("[[dd-]hh:]mm:ss") 을 초로 변환한다. macOS ps 에는 etimes 가 없다.
etime_to_seconds() {
    local etime="$1"
    local days=0 rest parts

    if [[ "$etime" == *-* ]]; then
        days="${etime%%-*}"
        rest="${etime#*-}"
    else
        rest="$etime"
    fi

    IFS=: read -r -a parts <<< "$rest"
    case ${#parts[@]} in
        3) printf '%s\n' $(( 10#$days * 86400 + 10#${parts[0]} * 3600 + 10#${parts[1]} * 60 + 10#${parts[2]} )) ;;
        2) printf '%s\n' $(( 10#$days * 86400 + 10#${parts[0]} * 60 + 10#${parts[1]} )) ;;
        *) return 1 ;;
    esac
}

# STRAY_PROCESS_REGEX 에 걸리는 고아(PPID=1) 프로세스를 종료한다.
# $1 이 주어지면 그 epoch 이후에 시작된 것만 대상으로 한다(0 이면 전부).
# $3 이 주어지면 그 초 이상 살아있는 것만 대상으로 한다(기본 0).
# 부모가 살아있는 프로세스는 사용자가 직접 띄운 것일 수 있으므로 건드리지 않는다.
kill_stray_orphans() {
    local since_epoch="${1:-0}"
    local label="${2:-CLEANUP}"
    local min_age="${3:-0}"
    local now pid ppid etime cmd age started
    local targets=()

    now="$(date +%s)"

    while read -r pid ppid etime cmd; do
        [[ "$ppid" == "1" ]] || continue
        age="$(etime_to_seconds "$etime")" || continue
        (( age >= min_age )) || continue
        started=$(( now - age ))
        (( started >= since_epoch )) || continue

        targets+=("$pid")
        echo "[${label}] stray orphan pid=${pid} age=${age}s: ${cmd}" >&2
    done < <(ps -eo pid=,ppid=,etime=,command= | grep -E "$STRAY_PROCESS_REGEX" | grep -v grep)

    [[ ${#targets[@]} -eq 0 ]] && return 0

    kill -TERM "${targets[@]}" 2>/dev/null || true
    sleep "$STRAY_KILL_GRACE_SECONDS"
    kill -KILL "${targets[@]}" 2>/dev/null || true

    echo "[${label}] terminated ${#targets[@]} stray orphan(s)" >&2
}

# 종료 시 이번 실행 중에 생긴 고아를 정리하도록 trap 을 건다.
# mycron 은 SIGKILL 전에 SIGTERM + 5초 유예를 주므로 그 안에 정리된다.
_stray_cleanup_once() {
    [[ "$_STRAY_CLEANUP_DONE" == "1" ]] && return 0
    _STRAY_CLEANUP_DONE=1
    kill_stray_orphans "$_STRAY_CLEANUP_SINCE"
}

install_stray_cleanup_trap() {
    # trap 은 나중에 실행되므로 시작 시각을 전역에 둔다(local 은 그때 사라진다).
    _STRAY_CLEANUP_SINCE="$(date +%s)"

    trap '_stray_cleanup_once' EXIT
    trap '_stray_cleanup_once; exit 143' TERM
    trap '_stray_cleanup_once; exit 130' INT
}

flow_engine() {
    local engine="${FLOW_ENGINE:-}"
    if [[ -z "$engine" && -f "$FLOW_ENGINE_FILE" ]]; then
        engine="$(tr -d '[:space:]' < "$FLOW_ENGINE_FILE")"
    fi
    printf '%s\n' "${engine:-codex}"
}

run_plan_flow() {
    case "$(flow_engine)" in
        claude)
            "$CLAUDE_BIN" --dangerously-skip-permissions -p "/plan-flow"
            ;;
        codex)
            "$CODEX_BIN" -a never exec \
                --dangerously-bypass-approvals-and-sandbox \
                "Use \$plan-flow. Process all eligible open backlog issues."
            ;;
        *)
            echo "[ERROR] unknown FLOW_ENGINE: $(flow_engine)" >&2
            return 2
            ;;
    esac
}

# DEV_FLOW_NO_NEW_ISSUES=1 이면 소진 모드 지시문을 프롬프트에 덧붙인다.
# 환경변수만으로는 하위 리뷰 에이전트까지 전달이 보장되지 않아 프롬프트에도 박는다.
drain_mode_prompt() {
    [[ "${DEV_FLOW_NO_NEW_ISSUES:-0}" == "1" ]] || return 0

    printf '%s' "

DRAIN MODE: DEV_FLOW_NO_NEW_ISSUES=1 is set. Never run \`gh issue create\` at any stage of this run — the open issue set may only shrink. Record non-blocking review findings and consensus FOLLOWUP items as PR comments, not issues. If a finding matters enough to file as an issue, it is not non-blocking: return REQUEST_CHANGES and fix it in the current PR. Pass this prohibition explicitly into every sub-agent prompt (review and consensus agents), otherwise they will file issues on their own judgment. Adding the needs-human label is not issue creation and remains allowed."
}

run_dev_flow() {
    case "$(flow_engine)" in
        claude)
            DEV_FLOW_DEPLOY=0 DEV_FLOW_NO_NEW_ISSUES="${DEV_FLOW_NO_NEW_ISSUES:-0}" \
                "$CLAUDE_BIN" --dangerously-skip-permissions -p "/dev-flow

Batch mode override: DEV_FLOW_DEPLOY=0 is set. Process eligible issues through merge, but skip Stage 8 deployment. dev-flow-all will run the QA gate and execute deploy.sh only after QA passes.$(drain_mode_prompt)"
            ;;
        codex)
            DEV_FLOW_DEPLOY=0 DEV_FLOW_NO_NEW_ISSUES="${DEV_FLOW_NO_NEW_ISSUES:-0}" \
                "$CODEX_BIN" -a never exec \
                --dangerously-bypass-approvals-and-sandbox \
                "Use \$dev-flow. Process all eligible open issues. Exclude issues labeled needs-human or qa-record. Treat qa-record issues as QA evidence/result records, not development work. If an issue requires human input or manual intervention, add the needs-human label, comment with the blocker, and skip it until a human removes that label. DEV_FLOW_DEPLOY=0 is set for this batch run: do not run deploy.sh inside dev-flow. dev-flow-all will run QA and deploy only after QA passes.$(drain_mode_prompt)"
            ;;
        *)
            echo "[ERROR] unknown FLOW_ENGINE: $(flow_engine)" >&2
            return 2
            ;;
    esac
}

# stdin 이 Claude/Codex 사용량 한도 메시지를 포함하는지 검사한다.
claude_hit_limit() {
    grep -qE "$LIMIT_MARKER_REGEX"
}

# stdin 에서 리셋 시각 힌트를 뽑는다.
# Claude: "resets …" / Codex: "try again at Jul 30th, 2026 11:30 PM."
extract_limit_reset() {
    grep -oE "(resets [^·]+|try again at [^.]+)" | head -1 | sed 's/[[:space:]]*$//'
}

# Telegram 으로 임의 텍스트를 발송한다. 설정이 없으면 조용히 실패.
send_telegram() {
    "$PYTHON_BIN" - "$1" <<'PY'
import sys
from mycron.config import load_config
from mycron.notifier import send_text
send_text(load_config().telegram, sys.argv[1])
PY
}
