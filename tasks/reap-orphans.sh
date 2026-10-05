#!/usr/bin/env bash
# 부모를 잃은 채 남은 개발 도구 프로세스(PPID=1)를 하루 한 번 치운다.
# flow 스크립트의 trap 정리는 flow 작업이 돌 때만 동작하고, 대화형 Claude 세션이
# 끊기며 남긴 flutter_tester 등은 아무도 치우지 않아 몇 주씩 쌓였다.
# 정리한 게 있으면 exit 10(ALERT)으로 끝내 무엇을 치웠는지 알린다.
# Usage: tasks/reap-orphans.sh  (mycron "reap-orphans" 작업이 매일 실행)
set -uo pipefail
source "$(dirname "$0")/_lib.sh"

# 방금 끊긴 실행이 스스로 정리될 여유를 두고, 2시간 넘은 것만 대상으로 한다.
MIN_AGE_SECONDS="${REAP_MIN_AGE_SECONDS:-7200}"

report="$(kill_stray_orphans 0 "REAP" "$MIN_AGE_SECONDS" 2>&1)"

if [[ -z "$report" ]]; then
  echo "남은 고아 프로세스 없음"
  exit 0
fi

echo "부모 없이 남아 있던 프로세스를 정리했습니다."
echo ""
echo "$report" | cut -c1-200
exit 10
