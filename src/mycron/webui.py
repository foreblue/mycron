"""mycron web 의 화면(HTML). 바깥 자원 없이 CSS · JS 를 모두 인라인으로 넣는다."""

from datetime import datetime
from html import escape
from urllib.parse import quote

from .executor import DEFAULT_TIMEOUT_SECONDS
from .web import (
    DaemonView,
    JobDetail,
    JobView,
    OutputTail,
    Overview,
    RunningView,
    RunView,
    local_time,
)

_WEEKDAYS = "월화수목금토일"
_KIND_LABEL = {"ok": "성공", "alert": "경고", "timeout": "시간 초과", "fail": "실패"}
_HISTORY_CHOICES = (20, 50, 100)
# 이보다 짧은 한 줄 커맨드는 펼치기 없이 그대로 보여 준다
_SHORT_COMMAND = 48
_COMMAND_PREVIEW = 120
_CURRENT = ' aria-current="true"'


# ──────────────────────────── 시간 · 길이 표기 ────────────────────────────


def fmt_abs(dt: datetime, now: datetime, seconds: bool = False) -> str:
    dt = dt.astimezone(now.tzinfo)
    clock = dt.strftime("%H:%M:%S" if seconds else "%H:%M")
    days = (dt.date() - now.date()).days
    if days == 0:
        return f"오늘 {clock}"
    if days == 1:
        return f"내일 {clock}"
    if days == -1:
        return f"어제 {clock}"
    weekday = _WEEKDAYS[dt.weekday()]
    if dt.year == now.year:
        return f"{dt.month}/{dt.day}({weekday}) {clock}"
    return f"{dt.year}/{dt.month}/{dt.day} {clock}"


def fmt_rel(dt: datetime, now: datetime) -> str:
    seconds = (dt - now).total_seconds()
    if abs(seconds) < 45:
        return "곧" if seconds > 0 else "방금"
    span = fmt_span(abs(seconds))
    return f"{span} 뒤" if seconds > 0 else f"{span} 전"


def fmt_span(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{max(minutes, 1)}분"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}시간 {minutes}분" if minutes and hours < 6 else f"{hours}시간"
    days, hours = divmod(hours, 24)
    return f"{days}일 {hours}시간" if hours and days < 3 else f"{days}일"


def fmt_duration(ms: int) -> str:
    seconds = ms / 1000
    if seconds < 10:
        return f"{seconds:.1f}초"
    if seconds < 60:
        return f"{seconds:.0f}초"
    minutes, rest = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}분 {rest:02d}초"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}시간 {minutes:02d}분"


def exit_text(exit_code: int) -> str:
    # 음수 종료 코드는 신호로 죽은 것이다 (-1 은 제한 시간 초과 · 실행 실패에도 쓰인다)
    if exit_code < -1:
        return f"exit {exit_code} (신호 {-exit_code})"
    return f"exit {exit_code}"


def fmt_timeout(seconds: float) -> str:
    if not float(seconds).is_integer():
        return f"{seconds}초"
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours}시간")
    if minutes:
        parts.append(f"{minutes}분")
    if secs or not parts:
        parts.append(f"{secs}초")
    return " ".join(parts)


# ──────────────────────────── 조각 ────────────────────────────


def _badge(kind: str, text: str) -> str:
    return f'<span class="badge b-{kind}">{escape(text)}</span>'


def _result_badge(run: RunView) -> str:
    return _badge(run.kind, _KIND_LABEL[run.kind])


def _daemon_block(daemon: DaemonView, now: datetime) -> str:
    if daemon.running:
        bits = [f"PID {daemon.pid}"]
        if daemon.started:
            bits.append(f"{fmt_abs(daemon.started, now)} 시작 ({fmt_rel(daemon.started, now)})")
        bits.append("재시작 뒤 자동 실행 " + ("등록됨" if daemon.launch_agent else "등록 안 됨"))
        return (
            '<div class="daemon d-on"><strong><i class="dot"></i>데몬 실행 중</strong>'
            f"<span>{escape(' · '.join(bits))}</span></div>"
        )
    reason = "PID 파일만 남아 있고 프로세스가 없습니다. " if daemon.stale else ""
    return (
        '<div class="daemon d-off"><strong><i class="dot"></i>데몬 꺼짐</strong>'
        f"<span>{reason}등록된 스케줄이 실행되지 않습니다. 켜려면 <code>mycron start</code></span></div>"
    )


def _running_item(running: RunningView, now: datetime, link_prefix: str | None) -> str:
    name = escape(running.job_name)
    if link_prefix is not None:
        name = f'<a href="{link_prefix}{quote(running.job_name, safe="")}">{name}</a>'
    elapsed = fmt_span(max(0.0, (now - running.started).total_seconds()))
    warn = ' · <span class="warn-text">제한 시간을 넘겼습니다 (기록 확인 필요)</span>' if running.over_timeout else ""
    return (
        f'<li><i class="pulse"></i><strong>{name}</strong>'
        f"<span>{escape(fmt_abs(running.started, now, seconds=True))} 시작 · {elapsed}째{warn}</span></li>"
    )


def _dots(view: JobView, now: datetime) -> str:
    recent = view.recent
    if not recent:
        return '<span class="muted">기록 없음</span>'
    cells = []
    for run in reversed(recent):  # 왼쪽이 오래된 실행
        when = fmt_abs(run.started, now) if run.started else "?"
        title = f"{when} · {_KIND_LABEL[run.kind]} · {exit_text(run.exit_code)} · {fmt_duration(run.duration_ms)}"
        cells.append(f'<i class="k-{run.kind}" title="{escape(title)}"></i>')
    counts = []
    ok = sum(1 for run in recent if run.kind == "ok")
    alerts = sum(1 for run in recent if run.kind == "alert")
    fails = view.recent_failures
    if ok:
        counts.append(f"성공 {ok}")
    if alerts:
        counts.append(f"경고 {alerts}")
    if fails:
        counts.append(f'<b class="fail-text">실패 {fails}</b>')
    summary = " · ".join(counts)
    return (
        f'<span class="dots" role="img" aria-label="최근 {len(recent)}회 결과">{"".join(cells)}</span>'
        f'<span class="dot-sum">{summary}</span>'
    )


def _next_run(view: JobView, now: datetime, daemon_running: bool) -> str:
    if not view.job.enabled:
        return '<span class="muted">비활성이라 실행되지 않음</span>'
    if view.next_run is None:
        return '<span class="warn-text">계산하지 못함 (cron 식 확인)</span>'
    text = f"{escape(fmt_abs(view.next_run, now))} <span class=\"muted\">{escape(fmt_rel(view.next_run, now))}</span>"
    if not daemon_running:
        text += ' <span class="warn-text">데몬이 꺼져 있어 실행되지 않음</span>'
    return text


def _last_run(view: JobView, now: datetime) -> str:
    run = view.last_run
    if run is None:
        return '<span class="muted">기록 없음</span>'
    when = fmt_abs(run.started, now) if run.started else "?"
    return (
        f"{escape(when)} {_result_badge(run)} "
        f'<span class="muted">{fmt_duration(run.duration_ms)} · {exit_text(run.exit_code)}</span>'
    )


def _options(view: JobView) -> str:
    job = view.job
    chips = [
        ("겹치면 건너뜀", job.skip_if_running, "이전 실행이 끝나지 않았으면 새 실행을 건너뜁니다 (skip_if_running)")
        if job.skip_if_running
        else ("겹쳐도 실행", False, "이전 실행이 끝나지 않아도 새로 실행합니다"),
        ("성공 알림 켬", True, "성공해도 텔레그램으로 알립니다")
        if job.notify_on_success
        else ("성공 알림 끔", False, "실패 · 경고일 때만 텔레그램으로 알립니다"),
    ]
    timeout = fmt_timeout(job.timeout_seconds)
    default = " (기본)" if job.timeout_seconds == DEFAULT_TIMEOUT_SECONDS else ""
    html = "".join(
        f'<span class="chip{" chip-on" if on else ""}" title="{escape(title)}">{escape(label)}</span>'
        for label, on, title in chips
    )
    return html + f'<span class="chip" title="실행 제한 시간">제한 {escape(timeout)}{default}</span>'


def _facts(view: JobView, now: datetime, daemon_running: bool) -> str:
    rows = [
        ("다음 실행", _next_run(view, now, daemon_running)),
        ("마지막 실행", _last_run(view, now)),
        (f"최근 {len(view.recent)}회" if view.recent else "최근 실행", _dots(view, now)),
        ("옵션", _options(view)),
    ]
    return '<dl class="facts">' + "".join(f"<div><dt>{label}</dt><dd>{value}</dd></div>" for label, value in rows) + "</dl>"


def _status_badges(view: JobView) -> str:
    badges = []
    if view.running:
        badges.append(_badge("run", "실행 중"))
    badges.append(_badge("on", "활성") if view.job.enabled else _badge("off", "비활성"))
    if view.last_failed:
        badges.append(_badge("fail", "마지막 실행 실패"))
    elif view.recent_failures:
        badges.append(_badge("warn", f"최근 실패 {view.recent_failures}회"))
    if view.last_run and view.last_run.kind == "alert":
        badges.append(_badge("alert", "경고"))
    return "".join(badges)


def _schedule(view: JobView) -> str:
    return f'<p class="sched">{escape(view.schedule)} <code>{escape(view.job.cron_expr)}</code></p>'


def _command(command: str) -> str:
    first_line = command.strip().split("\n", 1)[0]
    if "\n" not in command.strip() and len(command) <= _SHORT_COMMAND:
        return f'<p class="cmd-short"><code>{escape(command)}</code></p>'
    preview = first_line[:_COMMAND_PREVIEW]
    return (
        f'<details class="cmd"><summary><code>{escape(preview)}</code></summary>'
        f"<pre>{escape(command)}</pre></details>"
    )


def _job_card(view: JobView, now: datetime, daemon_running: bool) -> str:
    job = view.job
    classes = ["job"]
    if not job.enabled:
        classes.append("is-off")
    if view.last_failed and job.enabled:
        classes.append("is-fail")
    if view.running:
        classes.append("is-run")
    search = " ".join([job.name, view.schedule, job.cron_expr, view.command]).lower()
    flags = (
        f'data-enabled="{int(job.enabled)}" data-disabled="{int(not job.enabled)}" '
        f'data-failed="{int(view.recent_failures > 0)}" data-running="{int(bool(view.running))}"'
    )
    return (
        f'<article class="{" ".join(classes)}" {flags} data-search="{escape(search)}">'
        f'<header><h3><a href="jobs/{quote(job.name, safe="")}">{escape(job.name)}</a></h3>'
        f'<div class="badges">{_status_badges(view)}</div></header>'
        f"{_schedule(view)}{_facts(view, now, daemon_running)}{_command(view.command)}"
        "</article>"
    )


def _output(label: str, tail: OutputTail | None) -> str:
    if tail is None:
        return ""
    note = f"마지막 {tail.shown_lines}줄 / 전체 {tail.total_lines}줄" if tail.truncated else f"{tail.total_lines}줄"
    return f'<h4>{label} <small>{note}</small></h4><pre class="k-{label}">{escape(tail.text)}</pre>'


def _run_row(run: RunView, now: datetime, opened: bool) -> str:
    when = fmt_abs(run.started, now, seconds=True) if run.started else "?"
    body = _output("stdout", run.stdout) + _output("stderr", run.stderr)
    if not body:
        body = '<p class="muted">출력 없음</p>'
    notified = '<span class="muted">알림 보냄</span>' if run.notified else ""
    return (
        f'<details class="run r-{run.kind}"{" open" if opened else ""}><summary>'
        f'<span class="r-when">{escape(when)}</span>{_result_badge(run)}'
        f'<span class="muted">{fmt_duration(run.duration_ms)} · {exit_text(run.exit_code)}</span>{notified}'
        f'</summary><div class="out">{body}</div></details>'
    )


# ──────────────────────────── 페이지 ────────────────────────────


def render_overview(overview: Overview) -> str:
    now = overview.now
    jobs = overview.jobs
    enabled = [view for view in jobs if view.job.enabled]
    failed = [view for view in jobs if view.recent_failures]
    # 꺼 둔 작업의 옛 실패는 요약 숫자에서 뺀다 (카드에는 그대로 표시된다)
    last_failed = [view for view in enabled if view.last_failed]
    recently_failed = [view for view in enabled if view.recent_failures]
    upcoming = min(
        (view for view in enabled if view.next_run is not None),
        key=lambda view: view.next_run,
        default=None,
    )

    if upcoming is not None:
        next_tile = (
            f"<b>{escape(fmt_abs(upcoming.next_run, now))}</b>"
            f"<span>{escape(upcoming.job.name)} · {escape(fmt_rel(upcoming.next_run, now))}</span>"
        )
    else:
        next_tile = "<b>—</b><span>예정된 실행 없음</span>"
    fail_class = " t-fail" if last_failed else ""
    tiles = (
        f'<div class="tile"><em>작업</em><b>{len(enabled)}</b><span>활성 · 전체 {len(jobs)}</span></div>'
        f'<div class="tile{" t-run" if overview.running else ""}"><em>실행 중</em><b>{len(overview.running)}</b>'
        f"<span>지금 돌고 있는 작업</span></div>"
        f'<div class="tile{fail_class}"><em>마지막 실행 실패</em><b>{len(last_failed)}</b>'
        f"<span>최근 10회 안 실패 {len(recently_failed)}개</span></div>"
        f'<div class="tile"><em>다음 실행</em>{next_tile}</div>'
    )

    if overview.running:
        running = '<ul class="running">' + "".join(_running_item(r, now, "jobs/") for r in overview.running) + "</ul>"
    else:
        running = '<p class="muted empty">지금 실행 중인 작업이 없습니다.</p>'

    filters = [
        ("all", "전체", len(jobs)),
        ("enabled", "활성", len(enabled)),
        ("failed", "최근 실패", len(failed)),
        ("running", "실행 중", sum(1 for view in jobs if view.running)),
        ("disabled", "비활성", len(jobs) - len(enabled)),
    ]
    filter_bar = "".join(
        f'<button type="button" data-filter="{key}" aria-pressed="{"true" if key == "all" else "false"}">'
        f"{label} <i>{count}</i></button>"
        for key, label, count in filters
    )

    if jobs:
        cards = "".join(_job_card(view, now, overview.daemon.running) for view in jobs)
    else:
        cards = ""
    no_jobs = "" if jobs else '<p class="muted empty">등록된 작업이 없습니다.</p>'

    body = f"""
<header class="top">
  <div><h1>mycron</h1><p class="muted">등록된 작업과 실행 상태 · 읽기 전용</p></div>
  {_daemon_block(overview.daemon, now)}
</header>
<section class="tiles" aria-label="요약">{tiles}</section>
<section>
  <h2>지금 실행 중</h2>
  {running}
</section>
<section>
  <h2>작업</h2>
  <div class="filters">
    <div class="chips" role="group" aria-label="작업 걸러 보기">{filter_bar}</div>
    <input id="q" type="search" placeholder="이름 · 커맨드 검색" aria-label="작업 검색" autocomplete="off">
  </div>
  {no_jobs}
  <div class="jobs" id="jobs">{cards}</div>
  <p class="muted empty" id="no-match" hidden>조건에 맞는 작업이 없습니다.</p>
</section>
<footer class="muted">{escape(now.strftime("%Y-%m-%d %H:%M:%S"))} 기준 · 60초마다 새로 고침(펼친 항목이 있으면 멈춤) · 출력과 커맨드의 토큰 · 키처럼 보이는 값은 [가림] 으로 바뀝니다</footer>
"""
    return _page("mycron", body, auto_refresh=True)


def render_job(detail: JobDetail) -> str:
    now = detail.now
    view = detail.view
    job = view.job
    runs = view.runs

    if view.running:
        running = '<ul class="running">' + "".join(_running_item(r, now, None) for r in view.running) + "</ul>"
    else:
        running = ""

    counts = {kind: sum(1 for run in runs if run.kind == kind) for kind in _KIND_LABEL}
    stats = [f"{_KIND_LABEL[kind]} {count}" for kind, count in counts.items() if count]
    if runs:
        average = sum(run.duration_ms for run in runs) // len(runs)
        stats.append(f"평균 {fmt_duration(average)}")
    choices = "".join(
        f'<a href="?n={n}"{_CURRENT if n == detail.limit else ""}>{n}건</a>' for n in _HISTORY_CHOICES
    )
    if runs:
        history = "".join(_run_row(run, now, opened=index == 0) for index, run in enumerate(runs))
    else:
        history = '<p class="muted empty">실행 기록이 없습니다.</p>'

    created = _stamp(job.created_at, now)
    updated = _stamp(job.updated_at, now)
    body = f"""
<nav class="crumb"><a href="../">← 작업 목록</a></nav>
<header class="top">
  <div><h1>{escape(job.name)}</h1><div class="badges">{_status_badges(view)}</div></div>
  {_daemon_block(detail.daemon, now)}
</header>
{running}
<section class="card">
  {_schedule(view)}
  {_facts(view, now, detail.daemon.running)}
  <h4>커맨드</h4>
  <pre class="cmd-full">{escape(view.command)}</pre>
  <p class="muted small">등록 {created} · 수정 {updated}</p>
</section>
<section>
  <div class="h-row"><h2>최근 실행 {len(runs)}건</h2><div class="pick">{choices}</div></div>
  <p class="muted">{escape(" · ".join(stats)) if stats else ""}</p>
  <div class="runs">{history}</div>
</section>
<footer class="muted">{escape(now.strftime("%Y-%m-%d %H:%M:%S"))} 기준 · 출력은 끝부분만 보여 주며, 토큰 · 키처럼 보이는 값은 [가림] 으로 바뀝니다</footer>
"""
    return _page(f"{job.name} · mycron", body, auto_refresh=False)


def render_message(title: str, message: str, home: str = "./") -> str:
    body = f"""
<header class="top"><div><h1>{escape(title)}</h1></div></header>
<section class="card"><p>{escape(message)}</p><p><a href="{escape(home)}">← 작업 목록</a></p></section>
"""
    return _page(title, body, auto_refresh=False)


def _stamp(ts: str, now: datetime) -> str:
    dt = local_time(ts)
    return escape(fmt_abs(dt, now)) if dt else escape(ts)


def _page(title: str, body: str, auto_refresh: bool) -> str:
    return (
        '<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex">\n'
        f"<title>{escape(title)}</title>\n"
        f'<link rel="icon" href="{_FAVICON}">\n'
        f"<style>{_CSS}</style>\n</head>\n"
        f'<body data-refresh="{int(auto_refresh)}">\n<main>{body}</main>\n'
        f"<script>{_JS}</script>\n</body>\n</html>\n"
    )


_FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
    "%3Ccircle cx='16' cy='16' r='14' fill='%230b63ce'/%3E"
    "%3Cpath d='M16 8v8l5 3' stroke='white' stroke-width='3' fill='none' stroke-linecap='round'/%3E%3C/svg%3E"
)

_CSS = """
:root{--bg:#f4f5f7;--card:#fff;--text:#1b1f24;--muted:#5f6875;--line:#e1e4e9;--code:#eef0f3;
--ok:#1a7f37;--ok-bg:#e3f3e8;--fail:#c5221f;--fail-bg:#fbe6e4;--alert:#8a5a00;--alert-bg:#fff1cc;
--run:#0b63ce;--run-bg:#e1eefd;--off:#5f6875;--off-bg:#e9ebee;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#0e1116;--card:#171b22;--text:#e7eaf0;--muted:#9aa4b2;
--line:#2a313b;--code:#20262f;--ok:#56d364;--ok-bg:#12301c;--fail:#ff7b72;--fail-bg:#3d1513;
--alert:#e3b341;--alert-bg:#3a2d0a;--run:#79b8ff;--run-bg:#0f2a4a;--off:#9aa4b2;--off-bg:#252b34}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR","Malgun Gothic",system-ui,sans-serif}
main{max-width:1120px;margin:0 auto;padding:16px 16px 48px}
h1{font-size:22px;margin:0;line-height:1.3;word-break:break-all}
h2{font-size:16px;margin:28px 0 10px}
h3{font-size:16px;margin:0;word-break:break-all}
h4{font-size:12px;margin:14px 0 6px;color:var(--muted);font-weight:600}
h4 small{font-weight:400}
p{margin:0}
a{color:var(--run);text-decoration:none}
a:hover{text-decoration:underline}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px}
code{background:var(--code);padding:1px 5px;border-radius:4px}
pre{margin:0;padding:10px 12px;background:var(--code);border-radius:8px;overflow:auto;
white-space:pre-wrap;word-break:break-all;max-height:420px}
.muted{color:var(--muted)}
.small{font-size:12.5px;margin-top:10px}
.empty{padding:14px 0}
.warn-text{color:var(--alert);font-weight:600}
.fail-text{color:var(--fail)}
.top{display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between;align-items:flex-start}
.crumb{margin-bottom:10px}
.daemon{display:flex;flex-direction:column;gap:2px;padding:8px 12px;border-radius:10px;font-size:13px;
border:1px solid var(--line);background:var(--card);max-width:100%}
.daemon strong{display:flex;align-items:center;gap:6px;font-size:14px}
.daemon span{color:var(--muted)}
.dot{width:9px;height:9px;border-radius:50%;background:var(--ok);display:inline-block}
.d-off{border-color:var(--fail);background:var(--fail-bg)}
.d-off strong{color:var(--fail)}
.d-off .dot{background:var(--fail)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:16px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;
display:flex;flex-direction:column;min-width:0}
.tile em{font-style:normal;font-size:12.5px;color:var(--muted)}
.tile b{font-size:22px;line-height:1.35}
.tile span{font-size:12.5px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.t-fail{border-color:var(--fail)}
.t-fail b{color:var(--fail)}
.t-run b{color:var(--run)}
.running{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.running li{background:var(--run-bg);border-radius:10px;padding:10px 12px;display:flex;flex-wrap:wrap;
gap:4px 10px;align-items:center}
.running span{color:var(--muted);font-size:13px}
.pulse{width:9px;height:9px;border-radius:50%;background:var(--run);animation:pulse 1.4s ease-in-out infinite}
@keyframes pulse{50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.pulse{animation:none}}
.filters{display:flex;flex-wrap:wrap;gap:8px;justify-content:space-between;margin-bottom:12px}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chips button{font:inherit;font-size:13px;color:var(--text);background:var(--card);border:1px solid var(--line);
border-radius:999px;padding:5px 12px;cursor:pointer}
.chips button i{font-style:normal;color:var(--muted);margin-left:2px}
.chips button[aria-pressed=true]{background:var(--text);color:var(--bg);border-color:var(--text)}
.chips button[aria-pressed=true] i{color:inherit;opacity:.75}
#q{font:inherit;font-size:14px;color:var(--text);background:var(--card);border:1px solid var(--line);
border-radius:999px;padding:5px 14px;flex:1 1 180px;max-width:320px;min-width:0}
.jobs{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(min(100%,470px),1fr))}
.job,.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;min-width:0}
.card{margin-top:14px}
.card+.card,.running+.card{margin-top:12px}
main>.running{margin-top:14px}
.job{border-left:4px solid var(--ok)}
.job.is-off{border-left-color:var(--line)}
.job.is-off h3 a{color:var(--muted)}
.job.is-fail{border-left-color:var(--fail)}
.job.is-run{border-left-color:var(--run)}
.job[hidden]{display:none}
.job header{display:flex;flex-wrap:wrap;gap:6px 10px;justify-content:space-between;align-items:center}
.job h3 a{color:var(--text)}
.badges{display:flex;flex-wrap:wrap;gap:5px}
.badge{font-size:12px;font-weight:600;padding:1px 8px;border-radius:999px;white-space:nowrap;
background:var(--off-bg);color:var(--off)}
.b-ok,.b-on{background:var(--ok-bg);color:var(--ok)}
.b-fail,.b-timeout{background:var(--fail-bg);color:var(--fail)}
.b-alert,.b-warn{background:var(--alert-bg);color:var(--alert)}
.b-run{background:var(--run-bg);color:var(--run)}
.sched{margin:6px 0 10px;font-weight:600}
.sched code{font-weight:400;margin-left:4px;color:var(--muted)}
.facts{margin:0;display:grid;gap:6px}
.facts div{display:grid;grid-template-columns:84px 1fr;gap:8px;align-items:baseline}
.facts dt{font-size:12.5px;color:var(--muted)}
.facts dd{margin:0;min-width:0;display:flex;flex-wrap:wrap;gap:4px 6px;align-items:center}
.dots{display:inline-flex;gap:3px;align-items:center}
.dots i{width:9px;height:16px;border-radius:3px;background:var(--ok)}
.dots .k-fail,.dots .k-timeout{background:var(--fail)}
.dots .k-alert{background:var(--alert)}
.dot-sum{font-size:12.5px;color:var(--muted)}
.chip{font-size:12px;padding:1px 8px;border-radius:6px;border:1px solid var(--line);color:var(--muted);white-space:nowrap}
.chip-on{color:var(--text)}
.cmd,.cmd-short{margin-top:10px}
.cmd-short code{word-break:break-all}
.cmd summary{cursor:pointer;display:flex;gap:6px;align-items:center;min-width:0;list-style:none}
.cmd summary::-webkit-details-marker{display:none}
.cmd summary::before{content:"▸";color:var(--muted);font-size:12px}
.cmd[open] summary::before{content:"▾"}
.cmd summary code{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.cmd[open] summary code{display:none}
.cmd[open] summary::after{content:"커맨드 접기";font-size:12.5px;color:var(--muted)}
.cmd pre{margin-top:6px}
.h-row{display:flex;flex-wrap:wrap;gap:8px;justify-content:space-between;align-items:baseline}
.pick{display:flex;gap:6px}
.pick a{font-size:13px;padding:2px 10px;border:1px solid var(--line);border-radius:999px;color:var(--text);background:var(--card)}
.pick a[aria-current]{background:var(--text);color:var(--bg);border-color:var(--text)}
.runs{display:grid;gap:8px;margin-top:10px}
.run{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--ok);border-radius:10px}
.run.r-fail,.run.r-timeout{border-left-color:var(--fail)}
.run.r-alert{border-left-color:var(--alert)}
.run summary{cursor:pointer;padding:10px 12px;display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center}
.run summary .muted{font-size:13px}
.r-when{font-weight:600;font-variant-numeric:tabular-nums}
.out{padding:0 12px 12px}
.out h4:first-child{margin-top:0}
pre.k-stderr{border-left:3px solid var(--alert)}
footer{margin-top:28px;font-size:12.5px}
@media (max-width:520px){main{padding:12px 12px 40px}.facts div{grid-template-columns:76px 1fr}
.tile b{font-size:19px}#q{max-width:none}}
"""

_JS = """
(function(){
var cards=[].slice.call(document.querySelectorAll('.job'));
var buttons=[].slice.call(document.querySelectorAll('[data-filter]'));
var q=document.getElementById('q'),none=document.getElementById('no-match');
var filter='all';
function apply(){
  var text=q?q.value.trim().toLowerCase():'',shown=0;
  cards.forEach(function(card){
    var ok=filter==='all'||card.getAttribute('data-'+filter)==='1';
    if(ok&&text)ok=card.getAttribute('data-search').indexOf(text)>=0;
    card.hidden=!ok;if(ok)shown++;
  });
  if(none)none.hidden=shown>0||!cards.length;
  buttons.forEach(function(b){b.setAttribute('aria-pressed',b.getAttribute('data-filter')===filter?'true':'false');});
}
buttons.forEach(function(b){b.addEventListener('click',function(){
  filter=b.getAttribute('data-filter');
  try{history.replaceState(null,'',filter==='all'?location.pathname+location.search:'#'+filter);}catch(e){}
  apply();
});});
if(q)q.addEventListener('input',apply);
var wanted=location.hash.slice(1);
if(buttons.some(function(b){return b.getAttribute('data-filter')===wanted;}))filter=wanted;
if(cards.length)apply();
if(document.body.getAttribute('data-refresh')==='1'){
  setInterval(function(){
    if(document.hidden||document.querySelector('details[open]')||(q&&q.value))return;
    location.reload();
  },60000);
}
})();
"""
