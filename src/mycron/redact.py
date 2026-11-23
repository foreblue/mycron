"""화면에 내보내기 전에 비밀값처럼 생긴 문자열을 가린다.

실행 출력과 커맨드에는 토큰 · 키가 섞여 있을 수 있다. 여기서는 "의심스러우면 가린다"
쪽으로 기운다 — 40자리 git SHA 같은 무해한 값이 가려질 수 있지만, 비밀값이 새는 것보다 낫다.
"""

import re

MASK = "[가림]"

# 가릴 필요가 없는 값: 짧은 숫자, 불리언, 셸 변수 참조, 자리표시자, 이미 가린 값
_HARMLESS_VALUE = re.compile(
    r"-?\d{1,8}(?:\.\d+)?|true|false|null|none|yes|no|on|off|"
    r"\$.*|<[^>]*>|\*+|\.+|x+|" + re.escape(MASK),
    re.IGNORECASE,
)

_PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)

# scheme://user:password@host
_URL_CREDENTIAL = re.compile(
    r"(?P<pre>(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@]+:)[^\s/@]+(?=@)"
)

# Authorization: ... / Cookie: ... 헤더는 값 전체를 가린다
_SECRET_HEADER = re.compile(
    r"(?P<pre>\b(?:Proxy-Authorization|Authorization|X-Api-Key|X-Auth-Token|Set-Cookie|Cookie)"
    r"\s*[:=]\s*)(?P<val>[^\r\n\"']+)",
    re.IGNORECASE,
)

_AUTH_SCHEME = re.compile(r"(?:Bearer|Basic|Token)\s+\S", re.IGNORECASE)
_BEARER = re.compile(r"(?P<pre>\bBearer\s+)[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_BASIC = re.compile(r"(?P<pre>\bBasic\s+)[A-Za-z0-9+/=]{16,}", re.IGNORECASE)

# NAME=value, NAME: value, "name": "value" — 이름과 구분자까지만 잡고 값은 따로 본다.
# 이름은 낱말이 시작하는 자리에서만 찾는다 (긴 한 줄에서도 느려지지 않게).
_NAME_SEP = re.compile(
    r"(?<![A-Za-z0-9_.-])(?P<name>[A-Za-z0-9_][A-Za-z0-9_.-]*)[\"']?\s*(?:=|:(?!//))\s*"
)
_VALUE = re.compile(r"\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s\"'`,;&|)]+")

# --token value, --api-key=value
_FLAG = re.compile(
    r"(?P<pre>(?<![A-Za-z0-9-])--(?P<name>[A-Za-z0-9][A-Za-z0-9-]*)(?:=|\s+))"
    r"(?P<val>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s\"'`,;&|)-][^\s\"'`,;&|)]*)"
)

_KNOWN_TOKENS = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"  # JWT
    r"|\b\d{6,12}:[A-Za-z0-9_-]{30,}"  # 텔레그램 봇 토큰
    r"|\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bAIza[0-9A-Za-z_-]{30,}"
)

_LONG_HEX = re.compile(r"(?<![A-Za-z0-9])[0-9a-fA-F]{32,}(?![A-Za-z0-9])")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_LONG_RUN = re.compile(r"[A-Za-z0-9+/=_-]{32,}")

_SECRET_WORDS = (
    "token",
    "secret",
    "password",
    "passwd",
    "apikey",
    "api_key",
    "api-key",
    "access_key",
    "access-key",
    "private_key",
    "private-key",
    "credential",
    "cookie",
)


def _is_secret_name(name: str) -> bool:
    lower = name.lower()
    # max_tokens, input_tokens 같은 사용량 숫자 이름은 비밀이 아니다
    if lower.endswith("tokens"):
        return False
    if any(word in lower for word in _SECRET_WORDS):
        return True
    if lower in ("auth", "pass", "key") or lower.endswith(("_auth", "_pwd", "_pass")):
        return True
    # API_KEY, api-key, apiKey, APIKEY — "monkey" 같은 낱말은 제외
    if lower.endswith(("_key", "-key", ".key")) or name.endswith("Key"):
        return True
    return name.isupper() and name.endswith("KEY")


def _mask_value(value: str) -> str | None:
    """가려야 하면 가린 값을, 그대로 둬도 되면 None 을 돌려준다."""
    quote = value[0] if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) >= 2 else ""
    inner = value[1:-1] if quote else value
    if not inner or _HARMLESS_VALUE.fullmatch(inner):
        return None
    return f"{quote}{MASK}{quote}"


def _redact_assignments(text: str) -> str:
    parts = []
    pos = 0
    for match in _NAME_SEP.finditer(text):
        if match.start() < pos or not _is_secret_name(match["name"]):
            continue
        value = _VALUE.match(text, match.end())
        if value is None:
            continue
        masked = _mask_value(value[0])
        if masked is None:
            continue
        parts.append(text[pos : match.end()])
        parts.append(masked)
        pos = value.end()
    parts.append(text[pos:])
    return "".join(parts)


def _redact_flag(match: re.Match) -> str:
    if not _is_secret_name(match["name"].replace("-", "_")):
        return match[0]
    masked = _mask_value(match["val"])
    if masked is None:
        return match[0]
    return f"{match['pre']}{masked}"


def _redact_header(match: re.Match) -> str:
    value = match["val"].strip()
    credential = value.split(None, 1)[1] if _AUTH_SCHEME.match(value) else value
    if _HARMLESS_VALUE.fullmatch(credential.strip()):
        return match[0]
    return match["pre"] + MASK


def _looks_random(text: str) -> bool:
    """글자 종류(소문자/대문자/숫자)가 자주 바뀌면 무작위 토큰으로 본다.

    경로 · 단어 · 날짜는 같은 종류가 길게 이어져서 바뀌는 비율이 낮다.
    """
    pairs = 0
    changes = 0
    prev = ""
    for ch in text:
        if ch.isdigit():
            kind = "d"
        elif ch.isupper():
            kind = "u"
        elif ch.islower():
            kind = "l"
        else:
            kind = ""
        if kind and prev:
            pairs += 1
            if kind != prev:
                changes += 1
        prev = kind
    return pairs >= 16 and changes / pairs >= 0.2


def _redact_long_run(match: re.Match) -> str:
    run = match[0]
    # UUID 는 요청 · 세션 식별자로 흔해서 그대로 둔다 (비밀 이름에 붙은 값은 위에서 이미 가렸다)
    if _UUID.fullmatch(run):
        return run
    if _looks_random(run):
        return MASK
    if "/" not in run:
        return run
    # 경로 안에 긴 무작위 조각이 끼어 있으면 그 조각만 가린다
    return "/".join(
        MASK if len(part) >= 32 and _looks_random(part) else part for part in run.split("/")
    )


def redact(text: str | None) -> str:
    """토큰 · 키처럼 생긴 값을 가린 문자열을 돌려준다."""
    if not text:
        return ""
    text = _PEM_BLOCK.sub(MASK, text)
    text = _URL_CREDENTIAL.sub(lambda m: m["pre"] + MASK, text)
    text = _SECRET_HEADER.sub(_redact_header, text)
    text = _BEARER.sub(lambda m: m["pre"] + MASK, text)
    text = _BASIC.sub(lambda m: m["pre"] + MASK, text)
    text = _redact_assignments(text)
    text = _FLAG.sub(_redact_flag, text)
    text = _KNOWN_TOKENS.sub(MASK, text)
    text = _LONG_HEX.sub(MASK, text)
    text = _LONG_RUN.sub(_redact_long_run, text)
    return text
