#!/usr/bin/env bash
# 등록된 작업에 한 줄 설명을 채운다 (상태판 카드의 이름 아래에 보인다).
# 설명은 각 작업의 커맨드가 가리키는 스크립트의 첫 주석 · README 를 읽고 쓴 초안이다(2026-11-24).
#
#   tools/fill_descriptions.sh             # 설명이 빈 작업만 채운다
#   tools/fill_descriptions.sh --dry-run   # 설명을 바꾸지 않고 무엇을 할지만 찍는다
#   tools/fill_descriptions.sh --force     # 이미 있는 설명도 이 초안으로 덮어쓴다
#
# `mycron describe` 가 있는 버전을 설치한 뒤에 돌린다. 다른 바이너리로 돌리려면 MYCRON=.venv/bin/mycron
# 없는 작업(지워졌거나 이름이 바뀐 것)은 건너뛴다. 설명을 고치려면: mycron describe <이름> "<설명>"
set -uo pipefail

MYCRON="${MYCRON:-mycron}"
DRY_RUN=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --force) FORCE=1 ;;
    *) echo "모르는 옵션: $arg (쓸 수 있는 것: --dry-run, --force)" >&2; exit 2 ;;
  esac
done

if ! "$MYCRON" describe --help >/dev/null 2>&1; then
  echo "오류: '$MYCRON describe' 를 쓸 수 없습니다. describe 명령이 있는 mycron 을 먼저 설치하세요." >&2
  exit 1
fi

filled=0 kept=0 missing=0 failed=0

fill() {
  local name="$1" text="$2" current
  if ! current="$("$MYCRON" describe "$name" 2>/dev/null)"; then
    echo "없음    $name"
    missing=$((missing + 1))
    return
  fi
  if [[ -n "$current" && "$FORCE" != "1" ]]; then
    echo "그대로  $name — $current"
    kept=$((kept + 1))
    return
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "채울 것 $name — $text"
    filled=$((filled + 1))
    return
  fi
  if "$MYCRON" describe "$name" "$text" >/dev/null; then
    echo "채움    $name — $text"
    filled=$((filled + 1))
  else
    echo "실패    $name"
    failed=$((failed + 1))
  fi
}

# ── mycron/tasks ──
fill clean-claude-tmp "이틀 넘게 안 쓴 Claude Code 세션 임시 폴더를 지움"
fill growth-daily "퍼널 숫자로 성장 아이디어를 뽑아 deepheart-dev 이슈 · 텔레그램으로"
fill inbox-flow "deepheart-dev 요청 이슈를 분류해 답하거나 개발로 넘김"
fill reap-orphans "부모 없이 남은 개발 도구 프로세스를 정리, 치웠으면 경고 알림"

# ── deepheart-marketing ──
fill deepheart-daily "FreeCell(@deepheartgames) 영상을 만들어 올리고 성과를 텔레그램으로"
fill deepheart-doldari "돌다리도 영상을 만들어 올리고 성과를 텔레그램으로"
fill deepheart-long "일요일에 만들어 둔 FreeCell 긴 영상 중 그날 편을 유튜브에 올림"
fill deepheart-long-week "다음 주 FreeCell 긴 영상 일곱 편을 만들어 두고 텔레그램으로 알림"
fill deepheart-morning "유튜브 · 인스타 아침 배치: 숫자 확인, 한 편 게시, 텔레그램 보고"
fill deepheart-store-week "다섯 앱 스토어 숫자를 텔레그램으로, 검색어 순위는 커밋 (아침 배치가 대신함)"

# ── deepheart-gw ──
fill disk-guard "내장 디스크 여유가 15G 아래면 텔레그램 경고"
fill fix-watch "기다리는 보안 수정판이 나왔는지 확인, 나오면 텔레그램 경고"
fill prune-flutter-build "7일 넘게 안 쓴 Flutter 빌드 캐시 폴더를 지움"
fill rotate-logs "traefik 로그를 달별 파일로 떼어 내고 3개월치만 남김"
fill security-ai-review "보안 설정의 어제 이후 변경을 AI 가 검토해 아침 운영 리포트에 붙임"
fill security-scan "밖에 열린 서비스의 취약점 · 설정을 스캔해 아침 운영 리포트로"

# ── golf-memo ──
fill golf-memo-hole-geo "최근에 간 구장 한 곳의 홀 지도 좌표를 만들어 main 에 올림"
fill golf-memo-open-days "구장별 예약 오픈일을 받아 golf-memo DB 에 넣음"

# ── invest ──
fill invest-feeds-watch "해외 매체 RSS 의 새 기사를 텔레그램으로, 브리핑 입력으로도 저장"
fill invest-portfolio-brief "보유 종목 · 분석된 영상으로 장중 대응 방향을 텔레그램으로"
fill invest-youtube-daily-summary "하루치 투자 영상을 교차 분석한 종합 요약을 텔레그램으로"
fill invest-youtube-featured "3PRO 채널 추천 재생목록이 바뀌면 텔레그램으로 알림"
fill invest-youtube-watch "새로 올라온 투자 영상을 전사 · 분석해 영상별로 텔레그램으로"

# ── stueng ──
fill stueng-daily "NPR Up First 오늘 편을 분석해 영어 어휘를 텔레그램으로"
fill stueng-planetmoney-prepare "최신 Planet Money 편으로 평일 5일치 학습 계획을 만듦"
fill stueng-planetmoney-send "Planet Money 주간 학습의 오늘 차수를 텔레그램으로"
fill stueng-vocab-push "모은 영어 어휘를 복습 서버(Fly.io)로 올림"

# ── 한 번짜리 알림 ──
fill remind-1128 "11-28 하루만: 마케팅 점검 목록을 텔레그램으로 보내고 스스로 지움"

echo ""
if [[ "$DRY_RUN" == "1" ]]; then
  echo "시험 실행: 채울 것 ${filled} · 그대로 ${kept} · 없음 ${missing} (설명은 바꾸지 않았습니다)"
else
  echo "채움 ${filled} · 그대로 ${kept} · 없음 ${missing} · 실패 ${failed}"
fi
[[ "$failed" -eq 0 ]]
