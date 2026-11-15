#!/usr/bin/env bash
# 카드 3종 개편(11-16 ~ 12-13)의 매일 아이디어 배치.
#   1) deepheart-marketing/tool/funnel_report.py --json 으로 어제까지의 숫자를 읽는다 — 못 읽으면 텔레그램 「숫자 없음」만
#   2) Claude 가 deepheart-marketing/growth-daily.md 순서대로 아이디어를 RUN_DIR/ideas.json 에 쓴다 (이슈 · 알림 · 커밋은 하지 않는다)
#   3) 이 스크립트가 아이디어마다 foreblue/deepheart-dev 에 growth + needs-human 이슈를 올리고 텔레그램 한 통을 보낸다
# needs-human 이 붙은 이슈는 inbox-flow 가 가져가지 않는다. 채널 주인이 「한다」로 답하고 라벨을 떼면 그때 개발로 넘어간다.
#
#   mycron add --name growth-daily --cron "30 9 * * *" \
#     --command "/bin/bash /Users/dysim/workspace/mycron/tasks/growth-daily.sh" \
#     --skip-if-running --no-success-notify --timeout 3600
#
# 시험: GROWTH_DRY_RUN=1 bash tasks/growth-daily.sh  (또는 --dry-run) — 이슈 · 텔레그램을 내지 않고 낼 것을 화면에 찍는다.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"

export LANG=ko_KR.UTF-8
export PATH="/Users/dysim/.local/bin:${PATH}"

HUB_REPO="${GROWTH_HUB_REPO:-foreblue/deepheart-dev}"
MKT_DIR="/Users/dysim/workspace/deepheart-marketing"
PY="/opt/homebrew/bin/python3"   # funnel_report.py 와 같은 파이썬
DRY="${GROWTH_DRY_RUN:-0}"
[[ "${1:-}" == "--dry-run" ]] && DRY=1
TODAY="$(date +%Y-%m-%d)"
DAY="$(date +%m-%d)($(date +%a | sed 's/Mon/월/;s/Tue/화/;s/Wed/수/;s/Thu/목/;s/Fri/금/;s/Sat/토/;s/Sun/일/'))"
RUN_DIR="${HOME}/Library/Logs/deepheart-growth/${TODAY}$([[ "$DRY" == "1" ]] && echo "-dry")"
LOCK_FILE="${HOME}/.mycron/growth-daily.lock"

if [[ -z "${GROWTH_LOCKED:-}" ]]; then
    GROWTH_LOCKED=1 GROWTH_DRY_RUN="$DRY" lockf -s -t 0 "$LOCK_FILE" "$BASH" "$0" "$@"
    ec=$?
    if [[ $ec -eq 75 ]]; then
        echo "[SKIP] another growth-daily is running (lock: ${LOCK_FILE})"
        exit 0
    fi
    exit "$ec"
fi

kill_stray_orphans 0 "CLEANUP PRE"
install_stray_cleanup_trap

mkdir -p "$RUN_DIR"
echo "[RUN] ${TODAY} dry=${DRY} dir=${RUN_DIR}"

tell() {
    if [[ "$DRY" == "1" ]]; then
        printf '[DRY] 텔레그램:\n%s\n' "$1"
    else
        send_telegram "$1" || echo "[WARN] 텔레그램 실패"
    fi
}

# 같은 날 두 번 돌아도(수동 `mycron run`) 이슈를 두 번 올리지 않는다.
if [[ "$DRY" != "1" && -f "$RUN_DIR/posted.txt" ]]; then
    echo "[SKIP] 오늘 이미 올렸다: $(cat "$RUN_DIR/posted.txt")"
    exit 0
fi

git -C "$MKT_DIR" pull -q --ff-only 2>/dev/null || true

# ──────────────────────────── 1) 숫자 ────────────────────────────

numbers_problem() {
    # 비어 있으면 숫자가 다 있다. 세 앱 모두 GA · 스토어가 있어야 한다 — 망설여지면 내지 않는다.
    "$PY" - "$RUN_DIR/funnel.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as e:  # noqa: BLE001
    print(f'funnel.json을 못 읽었다 ({e.__class__.__name__})'); sys.exit()
miss = [f"{x['name']} {'GA' if not x.get('ga') else '스토어'}" for x in d.get('apps', [])
        if not x.get('ga') or not x.get('store')]
if len(d.get('apps', [])) < 3:
    miss.append('앱이 셋이 아니다')
print(' · '.join(miss))
PY
}

if ! (cd "$MKT_DIR" && "$PY" tool/funnel_report.py --json >"$RUN_DIR/funnel.json" 2>"$RUN_DIR/funnel.err"); then
    problem="funnel_report.py가 실패했다: $(tail -1 "$RUN_DIR/funnel.err" | cut -c1-160)"
else
    problem="$(numbers_problem)"
    (cd "$MKT_DIR" && "$PY" tool/funnel_report.py >"$RUN_DIR/funnel.txt" 2>&1) || true   # 같은 날 캐시라 GA에 다시 묻지 않는다
fi
if [[ -n "$problem" ]]; then
    echo "[NO-NUMBERS] ${problem}"
    tell "💡 growth ${DAY} — 숫자 없음, 오늘은 아이디어를 내지 않는다
${problem}"
    exit 0
fi

# ──────────────────────────── 2) 아이디어 (Claude) ────────────────────────────

# 허락은 프롬프트에 직접 적는다 — 파일 속 글은 사람의 지시로 읽히지 않는다(morning.sh 와 같다). 큰따옴표를 프롬프트 안에 쓰지 않는다.
prompt="오늘은 $(date '+%Y-%m-%d %H:%M %Z')이고, 카드 3종(FreeCell · 스파이더 · 클론다이크) 매일 아이디어 배치 차례다. ${MKT_DIR}/growth-daily.md를 읽고 그 순서를 한 번 돌아라. 사람은 자리에 없다 — 묻지 말고, 망설여지면 내지 말고 skipped에 적어라.

RUN_DIR=${RUN_DIR}
숫자는 이미 RUN_DIR/funnel.json · funnel.txt에 있다.

허락(채널 주인이 2026-11-13 · 11-15에 주었다 — 「매일 하나 이상 아이디어를 내고 검토해서 반영」):
1. 결과는 RUN_DIR/ideas.json에 쓴다. 임시 파일도 RUN_DIR 안에만 둔다. 다른 곳에 파일을 쓰거나 고치지 않는다.
2. gh로 ${HUB_REPO}의 이슈 · 댓글을 읽어도 된다. 이슈를 만들거나 고치거나 댓글을 달지 않는다 — 이슈는 이 스크립트가 올린다.
3. ~/workspace의 freecell · spider · solitaire · deepheart-marketing · artifacts를 읽어도 된다. 게임 저장소에서는 git show로만 읽고 checkout · pull · stash · 커밋을 하지 않는다 — 다른 에이전트가 짓는 중이다.
4. tool/funnel_report.py는 한 번까지 더 불러도 된다(GA 할당량). iTunes 검색 API로 경쟁 앱을 몇 번 봐도 된다.
텔레그램, 커밋, 푸시, 업로드, 앱 제출, 계정 · 콘솔 작업은 허락하지 않는다."

log="$RUN_DIR/agent.log"
(cd "$MKT_DIR" && "$CLAUDE_BIN" --model "$CLAUDE_MODEL" --dangerously-skip-permissions -p "$prompt" </dev/null >"$log" 2>&1)
rc=$?
if claude_hit_limit <"$log"; then
    tell "💡 growth ${DAY} — Claude 사용량 한도로 멈췄다 ($(extract_limit_reset <"$log"))"
    exit 0
fi
if [[ ! -s "$RUN_DIR/ideas.json" ]]; then
    tell "💡 growth ${DAY} — 아이디어 파일이 없다 (에이전트 종료 ${rc}) · 로그 ${log}"
    exit 0
fi

# ──────────────────────────── 3) 이슈 · 알림 ────────────────────────────

# ideas.json → 이슈마다 RUN_DIR/issue-<i>.md 와 RUN_DIR/issues.tsv(번호␉제목␉본문 파일␉한 줄 요약), 텔레그램 머리는 RUN_DIR/skipped.txt
if ! "$PY" - "$RUN_DIR" "$TODAY" <<'PY'
import json, sys
from pathlib import Path

run, today = Path(sys.argv[1]), sys.argv[2]
d = json.loads((run / 'ideas.json').read_text())
REPOS = {'freecell': 'FreeCell', 'spider': '스파이더', 'solitaire': '클론다이크', 'deepheart-marketing': '마케팅 도구'}
LANES = {'유입', '완성도', '재방문'}
WHERE = {'store': '스토어 글 · 검색어 — 그 저장소 main의 `store/`',
         'main': '지금 앱(Flutter 1.x, 가지 main) — 고침 · 측정만',
         'godot-2.0': '2.0 판(가지 `godot-2.0`) — dev-flow가 못 다루는 가지라 「한다」면 `for-2.0`으로 닫혀 2.0을 짓는 쪽이 가져간다',
         'switch': '2.0의 `site/switches.json` 값만',
         'marketing': 'deepheart-marketing 도구 · 보고'}
EFFORT = {'스위치', '작다', '중간', '크다'}
VERDICT = {'한다', '스위치만', '미룬다'}
rows, bad = [], []
for i, x in enumerate(d.get('ideas', [])[:3], 1):
    why = [k for k, ok in (('repo', x.get('repo') in REPOS), ('lane', x.get('lane') in LANES),
                           ('where', x.get('where') in WHERE), ('effort', x.get('effort') in EFFORT),
                           ('verdict', x.get('verdict') in VERDICT),
                           ('title', bool(str(x.get('title', '')).strip())),
                           ('evidence', bool(str(x.get('evidence', '')).strip())),
                           ('request', bool(str(x.get('request', '')).strip()))) if not ok]
    if why:
        bad.append(f"{i}번({', '.join(why)})")
        continue
    sw = x.get('switch') or None
    sw_line = (f"\n| 스위치 | `{sw.get('key')}`: `{json.dumps(sw.get('from'), ensure_ascii=False)}` → "
               f"`{json.dumps(sw.get('to'), ensure_ascii=False)}` |" if isinstance(sw, dict) else '')
    body = f"""> growth-daily 배치가 {today}에 낸 아이디어입니다. 댓글로 **한다 / 스위치만 / 미룬다 / 버린다** 가운데 하나를 적어 주세요.
> - **한다 · 스위치만** — 댓글을 단 뒤 `needs-human` 라벨을 떼면 다음 inbox-flow(15분마다)가 개발 · 리뷰 · 머지로 가져갑니다(배포는 하지 않음). 답 없이 라벨만 떼면 「한다」로 봅니다.
> - **미룬다 · 버린다** — 댓글을 달고 이슈를 닫아 주세요. 미룬 것은 숫자가 달라졌을 때만 다시 냅니다.

| | |
| --- | --- |
| 갈래 | {x['lane']} |
| 들어갈 곳 | `{x['where']}` — {WHERE[x['where']]} |
| 품 | {x['effort']} |
| 닿는 사람 | {x.get('reach') or '—'} |
| 잴 숫자 | {x.get('measure') or '—'} |
| 권하는 판정 | **{x['verdict']}** — {x.get('verdict_why') or ''} |{sw_line}

**근거** — {x['evidence']}

### 대상 프로젝트

{x['repo']}

### 작업 유형

개발

### 요청 내용

{x['request'].strip()}

들어갈 곳: `{x['where']}`

### 완료 기준

{(x.get('done') or '').strip() or '_No response_'}
"""
    f = run / f'issue-{i}.md'
    f.write_text(body)
    title = f"[growth · {REPOS[x['repo']]}] {x['title'].strip()}"
    short = f"{x['lane']} · {x['effort']} · 권함 {x['verdict']}"
    rows.append('\t'.join([str(i), title.replace('\t', ' '), str(f), short]))
(run / 'issues.tsv').write_text(''.join(r + '\n' for r in rows))
skipped = str(d.get('skipped') or '').strip()
if bad:
    skipped = (skipped + ' / ' if skipped else '') + '형식이 틀려 뺀 아이디어: ' + ', '.join(bad)
(run / 'skipped.txt').write_text(skipped)
PY
then
    tell "💡 growth ${DAY} — ideas.json을 읽지 못했다 · ${RUN_DIR}"
    exit 0
fi

skipped="$(cat "$RUN_DIR/skipped.txt")"
lines=()
count=0
if [[ "$DRY" != "1" ]]; then
    gh label create growth -R "$HUB_REPO" --color 5319e7 --description "growth-daily 배치가 낸 아이디어" 2>/dev/null || true
fi
while IFS=$'\t' read -r i title file short; do
    [[ -z "$i" ]] && continue
    if [[ "$DRY" == "1" ]]; then
        printf '[DRY] 이슈 %s — %s\n라벨: growth, needs-human\n-----\n%s\n-----\n' "$i" "$title" "$(cat "$file")"
        lines+=("· #? ${title} (${short})")
    else
        url=$(gh issue create -R "$HUB_REPO" --title "$title" --label growth --label needs-human --body-file "$file") || {
            echo "[WARN] 이슈를 못 올렸다: ${title}"
            lines+=("· (올리지 못함) ${title}")
            continue
        }
        echo "[ISSUE] ${url}"
        lines+=("· #${url##*/} ${title} (${short})")
    fi
    count=$((count + 1))
done <"$RUN_DIR/issues.tsv"

if (( count == 0 )); then
    msg="💡 growth ${DAY} — 오늘은 내지 않았다
${skipped:0:300}"
else
    msg="💡 growth ${DAY} · ${count}건 — 답: 한다 / 스위치만 / 미룬다 / 버린다
$(printf '%s\n' "${lines[@]}")
github.com/${HUB_REPO}/issues?q=is:open+label:growth+label:needs-human"
fi
tell "$msg"
[[ "$DRY" != "1" ]] && echo "${count}건 $(date +%H:%M)" >"$RUN_DIR/posted.txt"
echo "growth-daily done (${count}건)."
