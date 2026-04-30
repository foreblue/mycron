#!/usr/bin/env bash
# flow 스크립트 공용 헬퍼.
# source 해서 사용한다: source "$(dirname "$0")/_lib.sh"

CLAUDE_BIN="/Users/dysim/.local/bin/claude"
CODEX_BIN="/opt/homebrew/bin/codex"
PYTHON_BIN="/Users/dysim/workspace/mycron/.venv/bin/python3"
FLOW_ENGINE_FILE="/Users/dysim/.mycron/flow-engine"
CLAUDE_LIMIT_MARKER="You've hit your limit"

# LaunchAgent/mycron daemon environments are intentionally sparse on macOS.
# Codex is installed under Homebrew and uses `#!/usr/bin/env node`, so node
# must be discoverable through PATH even when the daemon starts with
# `/usr/bin:/bin:/usr/sbin:/sbin`.
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"

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

run_dev_flow() {
    case "$(flow_engine)" in
        claude)
            "$CLAUDE_BIN" --dangerously-skip-permissions -p "/dev-flow"
            ;;
        codex)
            "$CODEX_BIN" -a never exec \
                --dangerously-bypass-approvals-and-sandbox \
                "Use \$dev-flow. Process all eligible open issues."
            ;;
        *)
            echo "[ERROR] unknown FLOW_ENGINE: $(flow_engine)" >&2
            return 2
            ;;
    esac
}

# stdin 이 Claude 한도 메시지를 포함하는지 검사한다.
claude_hit_limit() {
    grep -qF "$CLAUDE_LIMIT_MARKER"
}

# stdin 에서 "resets …" 구간을 뽑아 리셋 시각 힌트를 돌려준다.
extract_limit_reset() {
    grep -oE "resets [^·]+" | head -1 | sed 's/[[:space:]]*$//'
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
