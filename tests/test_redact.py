import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mycron.redact import MASK, redact

HEX40 = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b"
RANDOM = "Zx9Kq2Lm8Pw4Rt6Yv1Bn3Cd5Fg7Hj0Ks2Lp4Qw6E"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
TELEGRAM = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw8"


@pytest.mark.parametrize(
    "text, secret",
    [
        (f"OPENAI_API_KEY=sk-proj-{RANDOM}", RANDOM),
        ("export DB_PASSWORD='hunter2-very-secret'", "hunter2-very-secret"),
        ('KEY="abc123def"', "abc123def"),
        ("secret: p4ssw0rd!", "p4ssw0rd!"),
        ('{"api_key": "abcd-efgh-1234"}', "abcd-efgh-1234"),
        ("curl -H 'Authorization: Bearer abc.def.ghi-123' https://example.com", "abc.def.ghi-123"),
        ("got Bearer s3cr3tT0k3nValue back", "s3cr3tT0k3nValue"),
        ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
        (f"commit {HEX40} deployed", HEX40),
        (f"session blob {RANDOM} stored", RANDOM),
        (f"jwt={JWT}", JWT),
        (f"https://api.telegram.org/bot{TELEGRAM}/sendMessage", "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw8"),
        ("postgres://app:sup3rs3cret@db.internal:5432/app", "sup3rs3cret"),
        ("mytool --token ghp_abcdefghijklmnopqrstuvwxyz012345 --verbose", "ghp_abcdefghijklmnopqrstuvwxyz012345"),
        ("mytool --api-key=shortkey1 run", "shortkey1"),
        ("https://example.com/hook?token=abcDEF123&mode=fast", "abcDEF123"),
        (f"/Users/me/cache/{RANDOM}/data", RANDOM),
        ("env: API_KEY=nested-Value-77 mode=fast", "nested-Value-77"),
        ("a=b=c TOKEN=chained-Value-88", "chained-Value-88"),
        ("token=6f1c2a34-9b7d-4e21-8c55-0a1b2c3d4e5f", "6f1c2a34-9b7d-4e21-8c55-0a1b2c3d4e5f"),
        ("x-api-key: abcd1234efgh", "abcd1234efgh"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nIBAAKC\n-----END RSA PRIVATE KEY-----", "MIIEow"),
    ],
)
def test_masks_secret_looking_values(text, secret):
    result = redact(text)
    assert secret not in result
    assert MASK in result


@pytest.mark.parametrize(
    "text",
    [
        "/bin/bash /Users/dysim/workspace/mycron/tasks/inbox-flow.sh",
        "/Volumes/T9/orca/workspaces/mycron/glaucus/src/mycron/web",
        "bash /Users/dysim/workspace/invest/scripts/run_invest_job.sh youtube-watch",
        "max_tokens=4096 input_tokens: 1234 output_tokens: 88",
        "timeout=300 retries=3 mode=fast",
        "HEAD is now at 500cc2a flow: 배치 모델 변경",
        "작업 완료: 12건 처리, 0건 실패",
        'curl -H "Authorization: Bearer $TOKEN" https://example.com',
        "API_KEY=${API_KEY} PASSWORD=$PW",
        "request id 6f1c2a34-9b7d-4e21-8c55-0a1b2c3d4e5f",
        "invest-youtube-daily-summary-2026-11-23-report-final",
        "monkey=banana keyboard: qwerty",
        "password=<redacted> token=****",
    ],
)
def test_keeps_ordinary_text(text):
    assert redact(text) == text


def test_keeps_names_and_structure():
    result = redact("API_KEY=abc12345 DEBUG=1")
    assert result == f"API_KEY={MASK} DEBUG=1"
    assert redact('KEY="abc12345"') == f'KEY="{MASK}"'


def test_empty_input():
    assert redact(None) == ""
    assert redact("") == ""


def test_long_single_lines_stay_fast():
    import time

    size = 32_000
    samples = [
        "a" * size,
        "a." * (size // 2),
        "a=" * (size // 2),
        "a:" * (size // 2),
        "-" * size,
        "--a" * (size // 3),
        "ab-" * (size // 3),
        "0123456789abcdef" * (size // 16),
        "https://" * (size // 8),
        "Bearer " * (size // 7),
        "KEY=" * (size // 4),
        "eyJ" * (size // 3),
        "1234567:" * (size // 8),
    ]
    started = time.monotonic()
    for sample in samples:
        redact(sample)
    assert time.monotonic() - started < 2
