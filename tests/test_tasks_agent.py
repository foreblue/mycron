"""tasks/_lib.sh 의 run_agent(claude → codex 넘어가기)와 그것을 쓰는 배치 스크립트 시험.

실제 claude · codex · gh · 텔레그램은 부르지 않는다. 가짜 실행 파일을 임시 폴더에 만들고
CLAUDE_BIN · CODEX_BIN · PYTHON_BIN 과 gh 셸 함수로 끼운다. HOME 도 임시 폴더로 돌린다.
"""

from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TASKS = REPO / "tasks"
BASH = "/bin/bash"

pytestmark = pytest.mark.skipif(not Path(BASH).exists(), reason="bash 가 없다")

# 실제로 받은 문구 그대로다. codex 는 굽은 따옴표(’)를 쓴다 — codex-cli 0.169, 2026-11-23.
CLAUDE_LIMIT = "You've hit your limit · resets 3am (Asia/Seoul)"
CLAUDE_NOLOGIN = "Not logged in · Please run /login"
CODEX_LIMIT = (
    "ERROR: You’ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), "
    "visit https://chatgpt.com/settings/usage to purchase more credits or try again at "
    "Nov 24th, 2026 10:15 PM."
)
CODEX_UNSUPPORTED = "ERROR: The 'gpt-5.5' model is not supported when using Codex with a ChatGPT account."

FAKE_CLAUDE = r"""#!/bin/bash
# 가짜 claude. FAKE_CLAUDE_MODE: ok | limit | nologin | crash | limit-in-body
n=$(ls "$FAKE_DIR"/claude.*.argv 2>/dev/null | wc -l | tr -d ' ')
printf '%s\n' "$@" > "$FAKE_DIR/claude.$n.argv"
printf '%s' "${@: -1}" > "$FAKE_DIR/claude.$n.prompt"
env | grep -E '^(DEV_FLOW_|CLAUDE_CODE_)' > "$FAKE_DIR/claude.$n.env"
pwd > "$FAKE_DIR/claude.$n.cwd"
case "${FAKE_CLAUDE_MODE:-ok}" in
    ok) echo "claude 가 답했다" ;;
    limit) echo "$FAKE_CLAUDE_LIMIT"; exit 1 ;;
    nologin) echo "$FAKE_CLAUDE_NOLOGIN"; exit 1 ;;
    crash) echo "뭔가 하다가 죽었다" >&2; exit 2 ;;
    limit-in-body)
        # 본문 앞쪽에 한도 문구가 있을 뿐, 정상으로 끝났다.
        echo "주석에 You've hit your limit 이라고 적혀 있었다"
        for i in 1 2 3 4 5 6; do echo "요약 $i"; done ;;
esac
"""

FAKE_CODEX = r"""#!/bin/bash
# 가짜 codex. FAKE_CODEX_MODE: ok | limit | unsupported | ideas
n=$(ls "$FAKE_DIR"/codex.*.argv 2>/dev/null | wc -l | tr -d ' ')
printf '%s\n' "$@" > "$FAKE_DIR/codex.$n.argv"
prompt="${@: -1}"
printf '%s' "$prompt" > "$FAKE_DIR/codex.$n.prompt"
env | grep -E '^(DEV_FLOW_|CLAUDE_CODE_)' > "$FAKE_DIR/codex.$n.env"
pwd > "$FAKE_DIR/codex.$n.cwd"
echo "OpenAI Codex v0.169.0 (fake)" >&2
echo "user" >&2
printf '%s\n' "$prompt" >&2
case "${FAKE_CODEX_MODE:-ok}" in
    ok)
        # dev-flow 를 받았으면 "PR 이 머지됐다"는 상태를 남긴다(가짜 gh 가 읽는다).
        if [[ "$prompt" == *'<command name="dev-flow">'* ]]; then touch "$FAKE_DIR/devflow_done"; fi
        echo "codex 가 답했다" ;;
    limit) echo "$FAKE_CODEX_LIMIT" >&2; echo "$FAKE_CODEX_LIMIT" >&2; exit 1 ;;
    unsupported) echo "$FAKE_CODEX_UNSUPPORTED" >&2; exit 1 ;;
    ideas)
        run_dir=$(printf '%s\n' "$prompt" | sed -n 's/^RUN_DIR=//p' | head -1)
        cat > "$run_dir/ideas.json" <<'JSON'
{"ideas": [{"repo": "freecell", "lane": "유입", "where": "store", "effort": "작다", "verdict": "한다",
            "title": "스토어 첫 줄을 바꾼다", "evidence": "노출 대비 설치가 낮다", "request": "store/ko.txt 첫 줄을 고친다"}],
 "skipped": ""}
JSON
        echo "ideas.json 을 썼다" ;;
esac
"""

FAKE_PYTHON = r"""#!/bin/bash
# 가짜 PYTHON_BIN — send_telegram 이 `"$PYTHON_BIN" - "<글>"` 로 부른다. 보내지 않고 적어 둔다.
cat >/dev/null
printf '%s\n---\n' "$2" >> "$FAKE_DIR/telegram.log"
"""

FAKE_GH = r"""#!/bin/bash
# 가짜 gh. 스크립트가 --jq 로 뽑아 쓰는 최종 문자열만 돌려준다. 모든 호출을 gh.log 에 남긴다.
printf '%s\n' "$*" >> "$FAKE_DIR/gh.log"
HUB="fake-owner/fake-hub"; PROJ="fake-owner/proj"; S=$'\x1f'
case "$*" in
    "issue list -R $HUB --state open --label in-progress"*) ;;
    "issue list -R $HUB --state open --author fake-owner"*) cat "$FAKE_DIR/new_issues" 2>/dev/null ;;
    "issue list -R $HUB --state open --label dispatched"*) cat "$FAKE_DIR/dispatched_issues" 2>/dev/null ;;
    "issue view 7 -R $HUB --json title,labels,url"*) printf '질문 하나%s%s%shttps://example.test/hub/7\n' "$S" "$(cat "$FAKE_DIR/label7" 2>/dev/null)" "$S" ;;
    "issue view 9 -R $HUB --json title,labels,url"*) printf '기능 하나%sdispatched%shttps://example.test/hub/9\n' "$S" "$S" ;;
    "issue view 9 -R $HUB --json comments"*) echo "넘겼습니다 <!-- inbox-dispatch: fake-owner/proj#3 -->" ;;
    "issue view 3 -R $PROJ --json state,labels"*)
        if [[ -f "$FAKE_DIR/devflow_done" ]]; then printf 'CLOSED%sinbox-managed\n' "$S"; else printf 'OPEN%sinbox-managed\n' "$S"; fi ;;
    "pr list -R $PROJ --head issue-3"*) [[ -f "$FAKE_DIR/devflow_done" ]] && echo 5 ;;
    "pr view 5 -R $PROJ --json state,labels,url"*) printf 'MERGED%s%shttps://example.test/proj/pull/5\n' "$S" "$S" ;;
    "repo view $PROJ --json defaultBranchRef"*) echo main ;;
    "issue comment "*|"issue edit "*|"issue close "*) ;;
    *) echo "fake gh: 모르는 호출: $*" >&2; exit 1 ;;
esac
exit 0
"""

COMMAND_FILE = """---
name: {name}
description: 시험용 커맨드
user_invocable: true
---

# {title}

이것은 {name} 커맨드의 본문이다. `{{issue}}` 를 처리한다.

```
Agent({{ description: "분석", prompt }})
```
"""


def _write_exec(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def world(tmp_path: Path) -> dict:
    """가짜 실행 파일과 임시 HOME. env 는 배치 스크립트에 그대로 넘길 환경이다."""
    fake = tmp_path / "fake"
    home = tmp_path / "home"
    work = tmp_path / "work"
    for d in (fake, home / ".mycron", home / ".claude" / "commands", work):
        d.mkdir(parents=True)
    _write_exec(fake / "claude", FAKE_CLAUDE)
    _write_exec(fake / "codex", FAKE_CODEX)
    _write_exec(fake / "python3", FAKE_PYTHON)
    _write_exec(fake / "gh", FAKE_GH)
    for name in ("inbox-flow", "dev-flow"):
        (home / ".claude" / "commands" / f"{name}.md").write_text(
            COMMAND_FILE.format(name=name, title=name.title())
        )
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(home),
        "TMPDIR": str(tmp_path),
        "LANG": "ko_KR.UTF-8",
        "FAKE_DIR": str(fake),
        "FAKE_GH": str(fake / "gh"),
        "FAKE_CLAUDE_LIMIT": CLAUDE_LIMIT,
        "FAKE_CLAUDE_NOLOGIN": CLAUDE_NOLOGIN,
        "FAKE_CODEX_LIMIT": CODEX_LIMIT,
        "FAKE_CODEX_UNSUPPORTED": CODEX_UNSUPPORTED,
        "CLAUDE_BIN": str(fake / "claude"),
        "CODEX_BIN": str(fake / "codex"),
        "PYTHON_BIN": str(fake / "python3"),
        # 진짜 고아 프로세스를 건드리지 않게 아무것에도 안 걸리는 식을 준다.
        "STRAY_PROCESS_REGEX": "mycron-test-never-matches-anything",
        "STRAY_KILL_GRACE_SECONDS": "0",
    }
    return {"fake": fake, "home": home, "work": work, "env": env, "tmp": tmp_path}


def _lib(world: dict, body: str, **env: str) -> subprocess.CompletedProcess:
    """_lib.sh 를 읽어 들인 bash 에서 body 를 돌린다. stdout · stderr 를 따로 받는다."""
    script = f'set -uo pipefail\nsource "{TASKS}/_lib.sh"\n{body}\n'
    return subprocess.run(
        [BASH, "-c", script],
        env={**world["env"], **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


RUN = 'run_agent "{work}" "할 일"; echo "rc=$? engine=$AGENT_ENGINE status=$AGENT_STATUS"; echo "note=$AGENT_NOTE"; agent_blocked && echo blocked=1 || echo blocked=0'


def _run_agent(world: dict, **env: str) -> subprocess.CompletedProcess:
    return _lib(world, RUN.format(work=world["work"]), **env)


def _calls(world: dict, engine: str) -> list[Path]:
    return sorted(world["fake"].glob(f"{engine}.*.argv"))


# ──────────────────────────── run_agent ────────────────────────────


def test_claude_ok_does_not_touch_codex(world):
    r = _run_agent(world)

    assert "claude 가 답했다" in r.stdout
    assert "rc=0 engine=claude status=ok" in r.stdout
    assert "blocked=0" in r.stdout
    assert "[AGENT] claude: ok" in r.stderr
    assert _calls(world, "codex") == []
    argv = _calls(world, "claude")[0].read_text().splitlines()
    assert argv[:4] == ["--model", "claude-opus-5-5", "--dangerously-skip-permissions", "-p"]
    assert (world["fake"] / "claude.0.cwd").read_text().strip() == str(world["work"].resolve())


def test_claude_missing_runs_codex_with_same_prompt(world):
    r = _run_agent(world, CLAUDE_BIN="/nonexistent/claude")

    assert "codex 가 답했다" in r.stdout
    assert "rc=0 engine=codex status=ok" in r.stdout
    assert "[AGENT] claude: 없음 (/nonexistent/claude) — codex 로 다시 돌린다" in r.stderr
    assert "→ codex: ok" in r.stderr
    assert _calls(world, "claude") == []
    argv = _calls(world, "codex")[0].read_text().splitlines()
    # codex exec --help (0.169): 모델 명시, 승인 · 샌드박스 우회, 리포 밖 허용, 색 없음, 프롬프트는 마지막 인자.
    assert argv[:7] == [
        "exec",
        "-m",
        "gpt-6.2-astra",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--color",
        "never",
    ]
    assert (world["fake"] / "codex.0.prompt").read_text() == "할 일"
    assert (world["fake"] / "codex.0.cwd").read_text().strip() == str(world["work"].resolve())


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("limit", r"claude: 사용량 한도 \(resets 3am \(Asia/Seoul\)\)"),
        ("nologin", r"claude: 로그인 필요"),
        ("crash", r"claude: 시작하자마자 종료 \(코드 2, \d초\)"),
    ],
)
def test_claude_failure_falls_back_to_codex(world, mode, reason):
    r = _run_agent(world, FAKE_CLAUDE_MODE=mode)

    assert "rc=0 engine=codex status=ok" in r.stdout
    assert re.search(rf"\[AGENT\] {reason} — codex 로 다시 돌린다", r.stderr)
    assert len(_calls(world, "claude")) == 1
    assert len(_calls(world, "codex")) == 1


def test_slow_crash_is_failed_not_blocked_when_codex_absent(world):
    # AGENT_FAST_FAIL_SECONDS=0 이면 모든 비정상 종료가 "한참 돌다 죽은 것"이다.
    r = _run_agent(world, FAKE_CLAUDE_MODE="crash", CODEX_BIN="/nonexistent/codex", AGENT_FAST_FAIL_SECONDS="0")

    assert "rc=1 engine=claude status=failed" in r.stdout
    assert "blocked=0" in r.stdout
    assert re.search(r"claude: 비정상 종료 \(코드 2, \d초\) → codex: 없음 \(/nonexistent/codex\)", r.stdout)


def test_both_at_limit_is_blocked_and_logged(world):
    r = _run_agent(world, FAKE_CLAUDE_MODE="limit", FAKE_CODEX_MODE="limit")

    assert "rc=1 engine=codex status=limit" in r.stdout
    assert "blocked=1" in r.stdout
    # 굽은 따옴표 문구도 한도로 잡고, 풀리는 시각을 남긴다.
    assert "→ codex: 사용량 한도 (try again at Nov 24th, 2026 10:15 PM)" in r.stdout
    assert "[AGENT] codex 기록 끝" in r.stderr
    assert "hit your usage limit" in r.stderr
    logs = list((world["home"] / ".mycron" / "agent-logs").glob("codex-*.log"))
    assert len(logs) == 1
    assert "hit your usage limit" in logs[0].read_text()
    assert f"[AGENT] codex 기록: {logs[0]}" in r.stderr


def test_codex_model_unsupported_is_blocked(world):
    r = _run_agent(world, CLAUDE_BIN="/nonexistent/claude", FAKE_CODEX_MODE="unsupported")

    assert "rc=1 engine=codex status=nostart" in r.stdout
    assert "blocked=1" in r.stdout
    assert "not supported when using Codex" in r.stderr


def test_claude_limit_and_no_codex_keeps_claude_result(world):
    r = _run_agent(world, FAKE_CLAUDE_MODE="limit", CODEX_BIN="/nonexistent/codex")

    assert "rc=1 engine=claude status=limit" in r.stdout
    assert "blocked=1" in r.stdout


def test_neither_installed(world):
    r = _run_agent(world, CLAUDE_BIN="/nonexistent/claude", CODEX_BIN="/nonexistent/codex")

    assert "rc=1 engine=none status=missing" in r.stdout
    assert "blocked=1" in r.stdout


def test_limit_phrase_in_body_of_successful_run_is_not_a_limit(world):
    r = _run_agent(world, FAKE_CLAUDE_MODE="limit-in-body")

    assert "rc=0 engine=claude status=ok" in r.stdout
    assert _calls(world, "codex") == []


def test_codex_prompt_echo_in_log_is_not_a_limit(world):
    # codex 는 프롬프트를 기록에 되풀이한다. 프롬프트에 한도 문구가 있어도 성공은 성공이다.
    body = 'run_agent "{work}" "You\'ve hit your limit 을 감지하는 코드를 고쳐라"; echo "status=$AGENT_STATUS"'
    r = _lib(world, body.format(work=world["work"]), CLAUDE_BIN="/nonexistent/claude")

    assert "status=ok" in r.stdout


# ──────────────────────────── 슬래시 커맨드 펼치기 ────────────────────────────


def test_codex_command_prompt_expands_command_file(world):
    r = _lib(world, 'codex_command_prompt dev-flow 42 "배치 지시 한 줄"')

    assert r.returncode == 0
    out = r.stdout
    assert "/dev-flow 42 를 수행하라" in out
    assert "- 인자: 42." in out
    assert '<command name="dev-flow">' in out
    assert "이것은 dev-flow 커맨드의 본문이다. `{issue}` 를 처리한다." in out
    assert 'Agent({ description: "분석", prompt })' in out
    assert "배치가 덧붙인 지시(위 문서와 어긋나면 이것이 우선한다):\n배치 지시 한 줄" in out
    # YAML 머리는 뺀다.
    assert "user_invocable" not in out
    assert "description: 시험용 커맨드" not in out


def test_codex_command_prompt_without_args_or_extra(world):
    r = _lib(world, 'codex_command_prompt inbox-flow "" ""')

    assert "/inbox-flow 를 수행하라" in r.stdout
    assert "- 인자: 없음." in r.stdout
    assert "배치가 덧붙인 지시" not in r.stdout


def test_codex_command_prompt_missing_file(world):
    r = _lib(world, 'codex_command_prompt no-such-command "" ""; echo "rc=$?"')

    assert r.stdout.strip() == "rc=1"


def test_run_command_agent_sends_slash_command_to_claude(world):
    body = 'DEV_FLOW_NOTIFY=none run_command_agent "{work}" dev-flow 42 "배치 지시"; echo "engine=$AGENT_ENGINE"'
    r = _lib(world, body.format(work=world["work"]))

    assert "engine=claude" in r.stdout
    assert (world["fake"] / "claude.0.prompt").read_text() == "/dev-flow 42\n\n배치 지시"
    assert "DEV_FLOW_NOTIFY=none" in (world["fake"] / "claude.0.env").read_text()


def test_run_command_agent_sends_expanded_command_to_codex(world):
    body = 'DEV_FLOW_NOTIFY=none run_command_agent "{work}" dev-flow 42 "배치 지시"; echo "engine=$AGENT_ENGINE"'
    r = _lib(world, body.format(work=world["work"]), FAKE_CLAUDE_MODE="limit")

    assert "engine=codex" in r.stdout
    prompt = (world["fake"] / "codex.0.prompt").read_text()
    assert not prompt.startswith("/dev-flow")
    assert '<command name="dev-flow">' in prompt
    assert "이것은 dev-flow 커맨드의 본문이다." in prompt
    assert prompt.rstrip().endswith("배치 지시")
    # DEV_FLOW_* 는 codex 에도 환경 변수로 간다.
    assert "DEV_FLOW_NOTIFY=none" in (world["fake"] / "codex.0.env").read_text()


def test_run_command_agent_without_command_file_does_not_fall_back(world):
    body = 'run_command_agent "{work}" no-such-command "" "x"; echo "rc=$? engine=$AGENT_ENGINE status=$AGENT_STATUS"'
    r = _lib(world, body.format(work=world["work"]), FAKE_CLAUDE_MODE="limit")

    assert "rc=1 engine=claude status=limit" in r.stdout
    assert "커맨드 파일이 없다" in r.stderr
    assert _calls(world, "codex") == []


# ──────────────────────────── 배치 스크립트 (가짜 gh · 텔레그램) ────────────────────────────

needs_lockf = pytest.mark.skipif(shutil.which("lockf") is None, reason="lockf 가 없다 (macOS 전용)")


def _inbox_flow(world: dict, **env: str) -> subprocess.CompletedProcess:
    hub = world["tmp"] / "ws" / "fake-hub"
    proj = world["tmp"] / "ws" / "proj"
    hub.mkdir(parents=True, exist_ok=True)
    if not proj.exists():
        proj.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(proj)], check=True)
    # _lib.sh 가 PATH 앞에 /opt/homebrew/bin 을 붙이므로 PATH 로는 진짜 gh 를 가릴 수 없다. 셸 함수로 가린다.
    wrapper = f'gh() {{ "$FAKE_GH" "$@"; }}; export -f gh; exec {BASH} "{TASKS}/inbox-flow.sh"'
    return subprocess.run(
        [BASH, "-c", wrapper],
        env={
            **world["env"],
            "INBOX_HUB_REPO": "fake-owner/fake-hub",
            "INBOX_HUB_DIR": str(hub),
            "INBOX_WORKSPACE": str(world["tmp"] / "ws"),
            **env,
        },
        capture_output=True,
        text=True,
        timeout=120,
    )


def _log(world: dict, name: str) -> str:
    path = world["fake"] / name
    return path.read_text() if path.exists() else ""


@needs_lockf
def test_inbox_flow_runs_on_codex_when_claude_is_missing(world):
    (world["fake"] / "new_issues").write_text("7\n")
    (world["fake"] / "label7").write_text("done")
    (world["fake"] / "dispatched_issues").write_text("9\n")

    r = _inbox_flow(world, CLAUDE_BIN="/nonexistent/claude")

    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "fake gh: 모르는 호출" not in out
    assert _calls(world, "claude") == []
    assert len(_calls(world, "codex")) == 2

    # 1) 분류 — /inbox-flow 본문을 펼쳐 허브 리포에서 돌린다.
    classify = (world["fake"] / "codex.0.prompt").read_text()
    assert '<command name="inbox-flow">' in classify
    assert "이것은 inbox-flow 커맨드의 본문이다." in classify
    assert "process only these hub issues, in this order: 7." in classify
    assert (world["fake"] / "codex.0.cwd").read_text().strip().endswith("/ws/fake-hub")
    assert "[CLASSIFY] 1건: 7" in out
    assert "[COMMENTED] #7" in out

    # 2) 개발 — /dev-flow 3 본문을 펼쳐 대상 리포에서 돌리고, DEV_FLOW_* 를 넘긴다.
    dev = (world["fake"] / "codex.1.prompt").read_text()
    assert "/dev-flow 3 를 수행하라" in dev
    assert '<command name="dev-flow">' in dev
    assert 'The PR body must contain "Closes #3".' in dev
    assert "Merge the PR when review approves." in dev
    assert (world["fake"] / "codex.1.cwd").read_text().strip().endswith("/ws/proj")
    dev_env = (world["fake"] / "codex.1.env").read_text()
    for var in ("DEV_FLOW_DEPLOY=0", "DEV_FLOW_NOTIFY=none", "DEV_FLOW_NO_MERGE=0", "DEV_FLOW_REQUIRE_APPROVE=1"):
        assert var in dev_env
    assert "[DEV-FLOW] #9 → fake-owner/proj#3" in out
    assert "[DONE] #9: ✅ 머지 완료" in out

    # 어느 쪽으로 돌았는지 로그에 남는다.
    assert out.count("[AGENT] claude: 없음 (/nonexistent/claude) — codex 로 다시 돌린다") == 2
    assert "→ codex: ok" in out

    telegram = _log(world, "telegram.log")
    assert "💬 답변 완료: #7 질문 하나" in telegram
    assert "✅ 머지 완료: #9 기능 하나" in telegram
    gh = _log(world, "gh.log")
    assert "issue edit 9 -R fake-owner/fake-hub --remove-label dispatched --add-label done" in gh
    assert "--add-label failed" not in gh


@needs_lockf
def test_inbox_flow_uses_claude_when_available(world):
    (world["fake"] / "new_issues").write_text("7\n")
    (world["fake"] / "label7").write_text("needs-human")

    r = _inbox_flow(world)

    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert _calls(world, "codex") == []
    prompt = (world["fake"] / "claude.0.prompt").read_text()
    assert prompt.startswith("/inbox-flow\n\nBatch mode (inbox-flow.sh): process only these hub issues, in this order: 7.")
    assert "[AGENT] claude: ok" in out
    assert "[NEEDS-HUMAN] #7" in out
    assert "🙋 확인 필요: #7 질문 하나" in _log(world, "telegram.log")


@needs_lockf
def test_inbox_flow_stops_without_marking_failed_when_both_are_at_limit(world):
    (world["fake"] / "new_issues").write_text("7\n")
    (world["fake"] / "dispatched_issues").write_text("9\n")

    r = _inbox_flow(world, FAKE_CLAUDE_MODE="limit", FAKE_CODEX_MODE="limit")

    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "[STOP] 이슈 분류: claude: 사용량 한도 (resets 3am (Asia/Seoul)) → codex: 사용량 한도 (try again at Nov 24th, 2026 10:15 PM)" in out
    assert "inbox-flow done." not in out
    telegram = _log(world, "telegram.log")
    assert "⚠️ inbox-flow 중단 (이슈 분류 중): 에이전트를 돌리지 못했다 — claude: 사용량 한도" in telegram
    assert "codex: 사용량 한도 (try again at Nov 24th, 2026 10:15 PM)" in telegram
    # 손도 못 댔으므로 이슈를 실패로 찍지 않는다 — 다음 실행이 다시 시도한다.
    gh = _log(world, "gh.log")
    assert "issue edit" not in gh
    assert "issue comment" not in gh


@needs_lockf
def test_inbox_flow_marks_failed_when_codex_ran_but_left_no_result(world):
    (world["fake"] / "new_issues").write_text("7\n")
    (world["fake"] / "label7").write_text("")  # 에이전트가 끝났는데 결과 라벨이 없다

    r = _inbox_flow(world, CLAUDE_BIN="/nonexistent/claude")

    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "[FAILED] #7" in out
    assert "issue edit 7 -R fake-owner/fake-hub --remove-label in-progress --add-label failed" in _log(world, "gh.log")


FAKE_FUNNEL = """import json, sys
apps = [{"name": n, "ga": {"users": 10}, "store": {"installs": 3}} for n in ("FreeCell", "스파이더", "클론다이크")]
print(json.dumps({"apps": apps}, ensure_ascii=False) if "--json" in sys.argv else "숫자 표")
"""

needs_growth_python = pytest.mark.skipif(
    not Path("/opt/homebrew/bin/python3").exists(), reason="growth-daily.sh 가 쓰는 파이썬이 없다"
)


def _growth_daily(world: dict, **env: str) -> subprocess.CompletedProcess:
    mkt = world["tmp"] / "mkt"
    (mkt / "tool").mkdir(parents=True, exist_ok=True)
    (mkt / "tool" / "funnel_report.py").write_text(FAKE_FUNNEL)
    wrapper = f'gh() {{ "$FAKE_GH" "$@"; }}; export -f gh; exec {BASH} "{TASKS}/growth-daily.sh" --dry-run'
    return subprocess.run(
        [BASH, "-c", wrapper],
        env={**world["env"], "GROWTH_MKT_DIR": str(mkt), "GROWTH_HUB_REPO": "fake-owner/fake-hub", **env},
        capture_output=True,
        text=True,
        timeout=120,
    )


@needs_lockf
@needs_growth_python
def test_growth_daily_dry_run_runs_on_codex_when_claude_is_missing(world):
    r = _growth_daily(world, CLAUDE_BIN="/nonexistent/claude", FAKE_CODEX_MODE="ideas")

    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert _calls(world, "claude") == []
    assert len(_calls(world, "codex")) == 1
    prompt = (world["fake"] / "codex.0.prompt").read_text()
    assert "growth-daily.md를 읽고 그 순서를 한 번 돌아라" in prompt
    assert (world["fake"] / "codex.0.cwd").read_text().strip().endswith("/mkt")
    assert "[AGENT] claude: 없음 (/nonexistent/claude) → codex: ok" in out
    assert "[DRY] 이슈 1 — [growth · FreeCell] 스토어 첫 줄을 바꾼다" in out
    assert "growth-daily done (1건)." in out
    # DRY 라 이슈도 텔레그램도 내지 않는다.
    assert _log(world, "gh.log") == ""
    assert _log(world, "telegram.log") == ""
    run_dirs = list((world["home"] / "Library" / "Logs" / "deepheart-growth").glob("*-dry"))
    assert len(run_dirs) == 1
    assert json.loads((run_dirs[0] / "ideas.json").read_text())["ideas"][0]["repo"] == "freecell"
    assert "ideas.json 을 썼다" in (run_dirs[0] / "agent.log").read_text()


@needs_lockf
@needs_growth_python
def test_growth_daily_dry_run_reports_when_both_are_at_limit(world):
    r = _growth_daily(world, FAKE_CLAUDE_MODE="limit", FAKE_CODEX_MODE="limit")

    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "[DRY] 텔레그램:" in out
    assert "에이전트를 돌리지 못해 멈췄다 (claude: 사용량 한도 (resets 3am (Asia/Seoul)) → codex: 사용량 한도" in out
    assert "growth-daily done" not in out
    assert _log(world, "telegram.log") == ""
