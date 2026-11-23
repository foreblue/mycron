#!/usr/bin/env bash
# Claude Code 세션 임시 폴더(claude-<uid>/<프로젝트>/<세션>)를 하루 한 번 치운다.
# 2026-11-19 세션 하나가 /private/tmp 에 QA 산출물 12G 를 쌓아 내장 디스크가 가득 차고
# Docker VM 이 멈춰 웹서비스가 35분 내려갔다. 이후 CLAUDE_CODE_TMPDIR 을 T9 으로 옮겼지만,
# 그 전에 뜬 세션과 SSD 쪽도 쌓이기만 하므로 함께 정리한다.
#
# 지우는 조건(세션 폴더 단위, 모두 만족해야 함):
#   - 세션 폴더 자체가 최근 KEEP_DAYS 일 안에 바뀌지 않았다
#   - 최근 KEEP_DAYS 일 안에 바뀐 파일이 없다 (미래 시각 파일은 "최근"으로 치지 않는다)
#   - 그 폴더 안 파일을 열어 둔 프로세스가 없다
# 프로젝트 폴더(이름이 '-' 로 시작)만 대상이고, bundled-skills 같은 Claude 내부 폴더는 건드리지 않는다.
#
# Usage: tasks/clean-claude-tmp.sh [--dry-run]  (mycron "clean-claude-tmp" 작업이 매일 실행)
set -uo pipefail

KEEP_DAYS="${CLEAN_CLAUDE_TMP_KEEP_DAYS:-2}"
ROOTS=(
  "/private/tmp/claude-$(id -u)"
  "/Volumes/T9/claude-tmp/claude-$(id -u)"
)
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

# 미래 시각 파일 판별용 기준 파일(지금 + 1시간)
future_ref="$(mktemp -t clean-claude-tmp)"
trap 'rm -f "$future_ref"' EXIT
touch -t "$(date -v+1H +%Y%m%d%H%M.%S)" "$future_ref"

# 열린 파일이 있는 세션 목록(<root>/<프로젝트>/<세션>)을 한 번에 모은다.
open_sessions="$(/usr/sbin/lsof -Fn 2>/dev/null | /usr/bin/sed -n 's/^n//p' \
  | /usr/bin/grep -E "^($(IFS='|'; echo "${ROOTS[*]}"))/-" \
  | /usr/bin/sed -E 's#^(.*/claude-[0-9]+/[^/]+/[^/]+).*#\1#' | sort -u)"

removed=0; kept=0; freed_kb=0
for root in "${ROOTS[@]}"; do
  [[ -d "$root" ]] || continue   # T9 이 빠져 있으면 건너뛴다
  for session in "$root"/-*/*/; do
    [[ -d "$session" ]] || continue
    session="${session%/}"
    if /usr/bin/grep -qxF -- "$session" <<<"$open_sessions"; then
      kept=$((kept + 1)); continue
    fi
    # 세션 폴더 자체가 최근에 생긴 경우(아직 파일이 없는 새 세션)도 남긴다.
    if [[ -n "$(find "$session" -maxdepth 0 -mtime "-${KEEP_DAYS}" -print 2>/dev/null)" ]] \
       || [[ -n "$(find "$session" -type f -mtime "-${KEEP_DAYS}" ! -newer "$future_ref" -print -quit 2>/dev/null)" ]]; then
      kept=$((kept + 1)); continue
    fi
    kb=$(du -sk "$session" 2>/dev/null | awk '{print $1}')
    if [[ "$DRY_RUN" -eq 1 ]]; then
      echo "[dry-run] 삭제 대상: $session (${kb}K)"
    else
      rm -rf -- "$session"
    fi
    removed=$((removed + 1)); freed_kb=$((freed_kb + ${kb:-0}))
  done
  # 비어 버린 프로젝트 폴더 정리
  [[ "$DRY_RUN" -eq 1 ]] || find "$root" -mindepth 1 -maxdepth 1 -name '-*' -type d -empty -delete 2>/dev/null
done

freed_gb=$(awk -v k="$freed_kb" 'BEGIN { printf "%.1f", k / 1048576 }')
echo "Claude 임시 폴더 정리: 세션 ${removed}개 삭제(${freed_gb}G), ${kept}개 유지 (기준 ${KEEP_DAYS}일)"
exit 0
