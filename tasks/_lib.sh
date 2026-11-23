#!/usr/bin/env bash
# flow 스크립트 공용 헬퍼.
# source 해서 사용한다: source "$(dirname "$0")/_lib.sh"

# 경로 · 모델은 환경 변수로 바꿀 수 있다(시험에서 가짜 실행 파일을 끼울 때 쓴다).
CLAUDE_BIN="${CLAUDE_BIN:-/Users/dysim/.local/bin/claude}"
CLAUDE_MODEL="${CLAUDE_MODEL:-claude-opus-5-5}"   # 배치 claude 호출 모델(서브에이전트도 따라감)
# claude 를 못 쓸 때 대신 도는 codex. ~/.codex/config.toml 의 기본 모델은 2026-11-23 gpt-6.2-sol 로 바꿨지만,
# 배치는 설정 파일과 상관없이 같은 모델로 돌게 명시한다.
CODEX_BIN="${CODEX_BIN:-/opt/homebrew/bin/codex}"
CODEX_MODEL="${CODEX_MODEL:-gpt-6.2-sol}"
PYTHON_BIN="${PYTHON_BIN:-/Users/dysim/workspace/mycron/.venv/bin/python3}"
# Claude 슬래시 커맨드 파일(/inbox-flow, /dev-flow)이 있는 곳 — codex 로 돌 때 본문을 프롬프트에 펼친다.
CLAUDE_COMMANDS_DIR="${CLAUDE_COMMANDS_DIR:-${HOME}/.claude/commands}"
# codex 의 진행 기록(stderr)을 두는 곳. mycron 은 작업 출력을 앞 64KB 만 남기므로 화면에는 마지막 답만 찍는다.
AGENT_LOG_DIR="${AGENT_LOG_DIR:-${HOME}/.mycron/agent-logs}"
AGENT_LOG_KEEP_DAYS="${AGENT_LOG_KEEP_DAYS:-14}"
# 이 초 안에 비정상 종료하면 일을 시작도 못 한 것으로 본다(모델 미지원 · 설정 오류 · 네트워크 끊김).
AGENT_FAST_FAIL_SECONDS="${AGENT_FAST_FAIL_SECONDS:-30}"
# Claude: "You've hit your limit" / "You've hit your session limit"
#         / "You've hit your weekly limit" / "You've hit your 5-hour limit"
# Codex: "You’ve hit your usage limit" — 굽은 따옴표(U+2019)로 온다(codex 0.169 에서 확인).
# 한도 종류가 계속 늘어나므로 수식어를 한 단어 와일드카드로 받는다.
# 따옴표는 곧은 것 · 굽은 것을 다 받는다(굽은 따옴표는 C 로캘에서 3바이트라 .{1,3}).
LIMIT_MARKER_REGEX="You.{1,3}ve hit your ([a-z0-9-]+ )?limit"
# 로그인이 풀렸을 때 — Claude: "Not logged in · Please run /login" (종료 1), "Invalid API key · Please run /login"
#                      Codex: "Not logged in" / "401 Unauthorized"
AUTH_MARKER_REGEX="Not logged in|Please run /login|Invalid API key|OAuth token (has expired|revoked)|authentication_error|401 Unauthorized"

# LaunchAgent/mycron daemon environments are intentionally sparse on macOS.
# gh and other Homebrew tools must be discoverable through PATH even when the
# daemon starts with `/usr/bin:/bin:/usr/sbin:/sbin`.
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

# stdin 이 Claude/Codex 사용량 한도 메시지를 포함하는지 검사한다.
claude_hit_limit() {
    grep -qE "$LIMIT_MARKER_REGEX"
}

# stdin 에서 리셋 시각 힌트를 뽑는다.
# Claude: "resets …" / Codex: "try again at Jul 30th, 2026 11:30 PM."
extract_limit_reset() {
    grep -oE "(resets [^·]+|try again at [^.]+)" | head -1 | sed 's/[[:space:]]*$//'
}

# ──────────────────────────── 에이전트 실행 (claude → codex) ────────────────────────────

AGENT_ENGINE="none"     # 마지막으로 실제로 돈 엔진: claude | codex | none
AGENT_STATUS="missing"  # 그 결과: ok | limit | auth | nostart | failed | missing
AGENT_NOTE=""           # 사람이 읽을 한 줄 — "claude: 사용량 한도 (resets 3am) → codex: ok"
_AGENT_TRY_STATUS=""
_AGENT_TRY_DETAIL=""

# 엔진 하나를 한 번 돌리고 결과를 _AGENT_TRY_STATUS / _AGENT_TRY_DETAIL 에 남긴다.
#   _agent_try <claude|codex> <dir> <prompt>
# 에이전트의 답은 stdout 으로 흘린다. 실행 파일이 없으면 돌리지 않고 missing.
_agent_try() {
    local engine="$1" dir="$2" prompt="$3"
    local bin out err="" rc scope reset started elapsed

    case "$engine" in
        claude) bin="$CLAUDE_BIN" ;;
        codex) bin="$CODEX_BIN" ;;
        *) _AGENT_TRY_STATUS="missing"; _AGENT_TRY_DETAIL="모르는 엔진 ${engine}"; return 1 ;;
    esac
    if ! command -v "$bin" >/dev/null 2>&1; then
        _AGENT_TRY_STATUS="missing"
        _AGENT_TRY_DETAIL="없음 (${bin})"
        return 1
    fi

    out="$(mktemp "${TMPDIR:-/tmp}/mycron-agent.XXXXXX")"
    started=$SECONDS
    if [[ "$engine" == "claude" ]]; then
        (cd "$dir" && "$bin" --model "$CLAUDE_MODEL" --dangerously-skip-permissions -p "$prompt" </dev/null 2>&1) | tee "$out"
        rc=${PIPESTATUS[0]}
    else
        # codex exec 는 마지막 답만 stdout 으로, 진행 기록(프롬프트 되풀이 · 툴 출력 · 오류)은 stderr 로 낸다.
        # 옵션은 codex-cli 0.169 `codex exec --help` 기준: 모델 명시, 승인 · 샌드박스 우회, git 리포 밖 허용, 색 없음.
        mkdir -p "$AGENT_LOG_DIR"
        find "$AGENT_LOG_DIR" -name 'codex-*.log' -mtime "+${AGENT_LOG_KEEP_DAYS}" -delete 2>/dev/null
        err="${AGENT_LOG_DIR}/codex-$(date +%Y%m%d-%H%M%S)-$$.log"
        (cd "$dir" && "$bin" exec -m "$CODEX_MODEL" \
            --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --color never \
            "$prompt" </dev/null 2>"$err") | tee "$out"
        rc=${PIPESTATUS[0]}
    fi

    elapsed=$(( SECONDS - started ))

    # 한도 · 로그인 오류는 출력 끝에 온다. 끝만 보는 까닭: codex 는 프롬프트와 읽은 파일을 기록에 되풀이하므로
    # 전체를 훑으면 본문에 적힌 문구(이 파일의 주석 등)에 걸린다.
    if (( rc != 0 )); then
        scope="$(tail -n 20 "$out"; [[ -n "$err" ]] && tail -n 20 "$err")"
    else
        scope="$(tail -n 5 "$out")"
    fi
    rm -f "$out"

    if claude_hit_limit <<<"$scope"; then
        reset="$(extract_limit_reset <<<"$scope")"
        _AGENT_TRY_STATUS="limit"
        _AGENT_TRY_DETAIL="사용량 한도 (${reset:-reset 시각 미상})"
    elif grep -qE "$AUTH_MARKER_REGEX" <<<"$scope"; then
        _AGENT_TRY_STATUS="auth"
        _AGENT_TRY_DETAIL="로그인 필요"
    elif (( rc != 0 && elapsed < AGENT_FAST_FAIL_SECONDS )); then
        _AGENT_TRY_STATUS="nostart"
        _AGENT_TRY_DETAIL="시작하자마자 종료 (코드 ${rc}, ${elapsed}초)"
    elif (( rc != 0 )); then
        _AGENT_TRY_STATUS="failed"
        _AGENT_TRY_DETAIL="비정상 종료 (코드 ${rc}, ${elapsed}초)"
    else
        _AGENT_TRY_STATUS="ok"
        _AGENT_TRY_DETAIL="ok"
    fi

    if [[ -n "$err" ]]; then
        echo "[AGENT] codex 기록: ${err}" >&2
        if [[ "$_AGENT_TRY_STATUS" != "ok" ]]; then
            echo "[AGENT] codex 기록 끝:" >&2
            tail -n 5 "$err" | cut -c1-400 >&2
        fi
    fi
    [[ "$_AGENT_TRY_STATUS" == "ok" ]]
}

# 에이전트에게 일을 한 번 시킨다. claude 를 먼저 쓰고, claude 가 없거나 로그인 오류 · 사용량 한도 ·
# 비정상 종료면 codex exec 로 다시 시킨다.
#   run_agent <dir> <claude 프롬프트> [<codex 프롬프트>]
# codex 프롬프트를 생략하면 claude 것과 같다. 빈 문자열을 주면 codex 로 넘어가지 않는다.
# 답은 stdout, 어느 쪽으로 돌았는지는 `[AGENT]` 줄로 stderr 에 남긴다.
# 결과는 호출한 셸의 AGENT_ENGINE · AGENT_STATUS · AGENT_NOTE 에 남는다 — `$(...)` 안에서 부르면 사라지므로
# 출력을 받으려면 파일로 돌린다(run_agent … >file 2>&1). 반환값: ok 면 0, 아니면 1.
run_agent() {
    local dir="$1" claude_prompt="$2" codex_prompt="${3-$2}"

    AGENT_ENGINE="none" AGENT_STATUS="missing"

    _agent_try claude "$dir" "$claude_prompt"
    AGENT_NOTE="claude: ${_AGENT_TRY_DETAIL}"
    if [[ "$_AGENT_TRY_STATUS" != "missing" ]]; then
        AGENT_ENGINE="claude" AGENT_STATUS="$_AGENT_TRY_STATUS"
    fi
    if [[ "$AGENT_STATUS" == "ok" ]]; then
        echo "[AGENT] ${AGENT_NOTE}" >&2
        return 0
    fi

    if [[ -z "$codex_prompt" ]]; then
        AGENT_NOTE+=" → codex: 넘길 프롬프트 없음"
        echo "[AGENT] ${AGENT_NOTE}" >&2
        return 1
    fi

    echo "[AGENT] ${AGENT_NOTE} — codex 로 다시 돌린다" >&2
    _agent_try codex "$dir" "$codex_prompt"
    AGENT_NOTE+=" → codex: ${_AGENT_TRY_DETAIL}"
    # codex 가 아예 없으면 claude 의 결과를 그대로 둔다.
    if [[ "$_AGENT_TRY_STATUS" != "missing" ]]; then
        AGENT_ENGINE="codex" AGENT_STATUS="$_AGENT_TRY_STATUS"
    fi
    echo "[AGENT] ${AGENT_NOTE}" >&2
    [[ "$AGENT_STATUS" == "ok" ]]
}

# 마지막 run_agent 에서 어느 에이전트도 일을 맡지 못했는지(없음 · 로그인 풀림 · 사용량 한도 · 시작하자마자 종료).
# 이때는 일감에 손도 못 댄 것이므로 실패로 찍지 말고 다음 실행에 맡긴다.
# failed(한참 돌다가 비정상 종료)는 여기 들지 않는다 — 어디까지 했는지는 호출한 쪽이 결과물로 판정한다.
agent_blocked() {
    case "$AGENT_STATUS" in
        limit|auth|missing|nostart) return 0 ;;
        *) return 1 ;;
    esac
}

# Claude 슬래시 커맨드 <name> 의 정의 파일 경로. 없으면 1.
claude_command_file() {
    local name="$1" f

    for f in "${CLAUDE_COMMANDS_DIR}/${name}.md" "${HOME}/.claude/skills/${name}/SKILL.md"; do
        if [[ -f "$f" ]]; then
            printf '%s\n' "$f"
            return 0
        fi
    done
    return 1
}

# `/name args` 는 Claude 만 아는 슬래시 커맨드다. codex 가 같은 일을 하도록 커맨드 파일 본문을 펼친 프롬프트를 만든다.
#   codex_command_prompt <name> <args> <배치가 덧붙이는 지시>
# 커맨드 파일이 없으면 1.
codex_command_prompt() {
    local name="$1" args="$2" extra="$3" file body

    file="$(claude_command_file "$name")" || return 1
    # 맨 앞 YAML 머리(--- … ---)는 Claude 가 읽는 메타데이터라 뺀다.
    body="$(awk 'NR == 1 && /^---[[:space:]]*$/ { fm = 1; next }
                 fm && /^---[[:space:]]*$/ { fm = 0; next }
                 !fm' "$file")"
    [[ -n "$body" ]] || return 1

    cat <<EOF
너는 mycron 배치에서 Claude Code 대신 도는 에이전트다(Claude 를 쓸 수 없어 넘어왔다). Claude 의 슬래시 커맨드 /${name}${args:+ ${args}} 를 수행하라. 그 커맨드의 정의가 아래 <command> 안에 있다 — 참고 자료가 아니라 네가 지금 따라야 할 절차다.

읽는 법:
- 인자: ${args:-없음}. 문서가 "인자로 … 주어진 경우"라고 하는 것이 이것이고, {issue} 같은 자리는 이 값으로 채운다.
- Agent({ ... }) 는 Claude 의 서브에이전트 호출이라 여기서는 실행되지 않는다. 그 단계의 "prompt 에 넣을 것"이 곧 그 역할이 할 일이다 — 네가 그 역할을 직접, 문서의 순서대로 수행한다. isolation: "worktree" 는 리포 밖에 git worktree 를 따로 만들어 그 안에서 작업하라는 뜻이다(원래 체크아웃의 브랜치는 바꾸지 않는다).
- 리뷰 · 합의처럼 "새 에이전트가 독립적으로 본다"는 단계에서는 구현하며 가진 가정을 내려놓고 gh pr diff 와 요구사항만 근거로 엄격하게 다시 본다. 네가 구현했다는 이유로 봐주지 않는다.
- 문서의 신뢰 경계(누가 쓴 글만 읽는지)와 "하지 않는 것"은 그대로 지킨다.
- 사람은 자리에 없다. 묻지 말고 끝까지 진행하고, 문서가 사람 확인을 요구하는 경우에만 문서의 방식(needs-human 라벨 등)으로 남긴다.
- 직전에 Claude 가 같은 일을 하다 멈췄을 수 있다. 라벨 · 댓글 · 원격 브랜치 · PR · worktree 의 지금 상태를 먼저 보고, 이미 된 일은 되풀이하지 않고 이어서 한다.
- 끝나면 무엇을 했는지 짧게 출력한다.

<command name="${name}">
${body}
</command>
${extra:+
배치가 덧붙인 지시(위 문서와 어긋나면 이것이 우선한다):
${extra}}
EOF
}

# 슬래시 커맨드를 돌린다 — claude 에는 `/name args`, codex 에는 커맨드 파일을 펼친 프롬프트.
#   run_command_agent <dir> <name> <args> <배치가 덧붙이는 지시>
# 결과는 run_agent 와 같다.
run_command_agent() {
    local dir="$1" name="$2" args="$3" extra="$4" codex_prompt

    if ! codex_prompt="$(codex_command_prompt "$name" "$args" "$extra")"; then
        echo "[AGENT] /${name} 커맨드 파일이 없다 (${CLAUDE_COMMANDS_DIR}/${name}.md) — codex 로는 넘기지 못한다" >&2
        codex_prompt=""
    fi
    run_agent "$dir" "/${name}${args:+ ${args}}

${extra}" "$codex_prompt"
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
