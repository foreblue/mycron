import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mycron.crontext import describe


@pytest.mark.parametrize(
    "expr, expected",
    [
        ("30 8 * * *", "매일 08:30"),
        ("*/15 * * * *", "15분마다"),
        ("* * * * *", "매분"),
        ("0 * * * *", "매시 정각"),
        ("0 */6 * * *", "매일 00:00, 06:00, 12:00, 18:00"),
        ("40 8,12,20 * * *", "매일 08:40, 12:40, 20:40"),
        ("0 10,14 * * mon-fri", "평일 10:00, 14:00"),
        ("0 13 * * sun", "매주 일요일 13:00"),
        ("30 4 * * thu", "매주 목요일 04:30"),
        ("5-55/10 * * * *", "매시 5~55분, 10분 간격"),
        ("0 10 28 11 *", "매년 11월 28일 10:00"),
        ("0 0 1 * *", "매월 1일 00:00"),
        ("0 9 * * sat,sun", "주말 09:00"),
        ("30 9-18 * * *", "매일 9~18시 매시 30분"),
        ("15 */2 * * *", "2시간마다 15분"),
    ],
)
def test_describe(expr, expected):
    assert describe(expr) == expected


def test_weekday_numbers_follow_apscheduler():
    # 데몬(APScheduler)에서는 0 이 월요일이다
    assert describe("0 9 * * 0") == "매주 월요일 09:00"
    assert describe("0 9 * * 6") == "매주 일요일 09:00"


@pytest.mark.parametrize("expr", ["bad expr", "0 0 last * *", "61 * * * *", "* * *", ""])
def test_unknown_expression_is_shown_as_is(expr):
    assert describe(expr) == expr
