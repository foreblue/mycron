"""cron 식을 사람이 읽는 한국어로 푼다 (예: "30 8 * * *" → "매일 08:30").

요일 숫자는 데몬이 쓰는 APScheduler 규칙을 따른다: 0=월 … 6=일.
(표준 cron 의 0=일 과 다르다. 실제로 실행되는 날과 같게 보여 주려는 것이다.)
풀 수 없는 식은 원문을 그대로 돌려준다.
"""

import re

_DOW_NAMES = ["월", "화", "수", "목", "금", "토", "일"]
_DOW_ALIASES = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_MONTH_ALIASES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_STEP = re.compile(r"\*/(\d+)")
_RANGE = re.compile(r"(\d+)-(\d+)")
_RANGE_STEP = re.compile(r"(\d+)-(\d+)/(\d+)")

# 시각을 하나씩 나열해도 읽을 만한 개수
_MAX_LISTED_TIMES = 6


def describe(expr: str) -> str:
    fields = expr.split()
    if len(fields) != 5:
        return expr
    try:
        return _describe(*fields)
    except ValueError:
        return expr


def _describe(minute: str, hour: str, dom: str, month: str, dow: str) -> str:
    minutes = _expand(minute, 0, 59)
    hours = _expand(hour, 0, 23)
    days = _expand(dom, 1, 31)
    months = _expand(month, 1, 12, _MONTH_ALIASES)
    weekdays = _expand(dow, 0, 6, _DOW_ALIASES)

    every_day = dom == "*" and month == "*" and dow == "*"
    date_part = "매일" if every_day else _date_part(dom, days, month, months, dow, weekdays)

    if len(hours) * len(minutes) <= _MAX_LISTED_TIMES:
        times = ", ".join(f"{h:02d}:{m:02d}" for h in hours for m in minutes)
        return f"{date_part} {times}"

    time_part = _time_pattern(minute, minutes, hour, hours)
    # "15분마다" 처럼 하루 종일 되풀이되는 식에는 "매일" 을 붙이지 않는다
    if every_day and (hour == "*" or _STEP.fullmatch(hour)):
        return time_part
    return f"{date_part} {time_part}"


def _expand(field: str, low: int, high: int, aliases: dict[str, int] | None = None) -> list[int]:
    values: set[int] = set()
    for part in field.lower().split(","):
        step = 1
        has_step = "/" in part
        if has_step:
            part, step_text = part.split("/", 1)
            step = _number(step_text, 1, high)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            start_text, end_text = part.split("-", 1)
            start = _value(start_text, low, high, aliases)
            end = _value(end_text, low, high, aliases)
            if start > end:
                raise ValueError(field)
        else:
            start = _value(part, low, high, aliases)
            # "5/10" 은 5부터 끝까지 10 간격 (APScheduler 와 같다)
            end = high if has_step else start
        values.update(range(start, end + 1, step))
    return sorted(values)


def _value(text: str, low: int, high: int, aliases: dict[str, int] | None) -> int:
    if aliases and text in aliases:
        return aliases[text]
    return _number(text, low, high)


def _number(text: str, low: int, high: int) -> int:
    if not text.isdigit():
        raise ValueError(text)
    number = int(text)
    if not low <= number <= high:
        raise ValueError(text)
    return number


def _join(values: list[int]) -> str:
    return ", ".join(str(v) for v in values)


def _time_pattern(minute: str, minutes: list[int], hour: str, hours: list[int]) -> str:
    minute_text = _minute_pattern(minute, minutes)
    fixed_minute = len(minutes) == 1
    if hour == "*":
        if fixed_minute:
            return f"매시 {minute_text}"
        if minute == "*" or _STEP.fullmatch(minute):
            return minute_text
        return f"매시 {minute_text}"

    if match := _STEP.fullmatch(hour):
        hour_text = f"{int(match[1])}시간마다"
    elif match := _RANGE_STEP.fullmatch(hour):
        hour_text = f"{int(match[1])}~{int(match[2])}시 {int(match[3])}시간 간격"
    elif match := _RANGE.fullmatch(hour):
        hour_text = f"{int(match[1])}~{int(match[2])}시"
        if fixed_minute:
            hour_text += " 매시"
    else:
        hour_text = f"{_join(hours)}시"
        if fixed_minute:
            hour_text += "의"
    return f"{hour_text} {minute_text}"


def _minute_pattern(minute: str, minutes: list[int]) -> str:
    if minute == "*":
        return "매분"
    if match := _STEP.fullmatch(minute):
        return f"{int(match[1])}분마다"
    if match := _RANGE_STEP.fullmatch(minute):
        return f"{int(match[1])}~{int(match[2])}분, {int(match[3])}분 간격"
    if match := _RANGE.fullmatch(minute):
        return f"{int(match[1])}~{int(match[2])}분 매분"
    if minutes == [0]:
        return "정각"
    return f"{_join(minutes)}분"


def _date_part(
    dom: str,
    days: list[int],
    month: str,
    months: list[int],
    dow: str,
    weekdays: list[int],
) -> str:
    parts = []
    if month != "*":
        parts.append(f"매년 {_join(months)}월")
    if dom != "*":
        day_text = f"{_join(days)}일"
        parts.append(day_text if month != "*" else f"매월 {day_text}")
    elif month != "*" and dow == "*":
        parts.append("매일")
    if dow != "*":
        weekday_text = _weekday_text(weekdays)
        if dom != "*":
            # APScheduler 는 날짜와 요일을 둘 다 만족할 때만 실행한다
            parts.append(f"중 {weekday_text}")
        elif month != "*":
            parts.append(weekday_text)
        else:
            parts.append(weekday_text if weekday_text in ("평일", "주말") else f"매주 {weekday_text}")
    return " ".join(parts)


def _weekday_text(weekdays: list[int]) -> str:
    if weekdays == [0, 1, 2, 3, 4]:
        return "평일"
    if weekdays == [5, 6]:
        return "주말"
    if len(weekdays) == 7:
        return "매일"
    return "·".join(_DOW_NAMES[d] for d in weekdays) + "요일"
