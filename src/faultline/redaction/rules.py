"""Pattern rules that remove tokens, passwords, API keys, cookies, emails and card numbers.

IP addresses, commit SHAs, timestamps and paths are kept on purpose: they are evidence.
Replacements look like `[REDACTED:<kind>]`; names of headers and keys are kept for context.
"""

import re
from collections.abc import Callable
from typing import Any

_NOT_REDACTED = r"(?!\[REDACTED)"
_SECRET_KEYS = (
    r"password|passwd|pwd|secret|client[_-]?secret|api[_-]?key|apikey|"
    r"access[_-]?key|private[_-]?key|token|access[_-]?token|refresh[_-]?token"
)

Rule = tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]


def _m(kind: str) -> str:
    return f"[REDACTED:{kind}]"


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


# Issuer prefixes: Visa 4, Mastercard 51-55 and 22-27, Amex 34/37, Diners 30/36/38,
# JCB 35, Discover 6. Requiring one keeps epoch-millisecond timestamps and IDs intact.
_CARD_PREFIX = re.compile(r"^(4|5[1-5]|2[2-7]|3[0457-9]|6)")


def _card(match: re.Match[str]) -> str:
    digits = re.sub(r"[ -]", "", match.group(0))
    if 13 <= len(digits) <= 19 and _CARD_PREFIX.match(digits) and _luhn_ok(digits):
        return _m("card")
    return match.group(0)


_RULES: list[Rule] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     _m("private_key")),
    # scheme://user:password@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^:/\s@]+:)" + _NOT_REDACTED + r"[^@\s/]+@"),
     r"\1" + _m("secret") + "@"),
    (re.compile(
        r"(?i)(?<![A-Za-z0-9])((?:proxy-)?authorization[\"']?\s*[:=]\s*[\"']?)"
        r"((?:bearer|basic|token|digest)\s+)?" + _NOT_REDACTED + r"[^\s\"',;]+"),
     r"\1\2" + _m("token")),
    (re.compile(r"(?i)\b(bearer\s+)" + _NOT_REDACTED + r"[A-Za-z0-9._~+/=-]{8,}"),
     r"\1" + _m("token")),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), _m("token")),
    (re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
        r"|xox[abposr]-[A-Za-z0-9-]{10,}|xapp-[A-Za-z0-9-]{10,}"
        r"|sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35})"),
     _m("token")),
    (re.compile(r"(?i)(?<![A-Za-z0-9])((?:set-)?cookie[\"']?\s*[:=]\s*)[^\r\n\"]+"),
     r"\1" + _m("cookie")),
    (re.compile(
        r"(?i)(?<![A-Za-z0-9])((?:" + _SECRET_KEYS + r")(?![A-Za-z0-9])[\"']?\s*[:=]\s*[\"']?)"
        + _NOT_REDACTED + r"[^\s\"'&,;}]+"),
     r"\1" + _m("secret")),
    (re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b(?!:[\w/])"),
     _m("email")),
    (re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"), _card),
]

_SENSITIVE_KEY = re.compile(
    r"(?i)(password|passwd|secret|api[_-]?key|apikey|authorization|private[_-]?key"
    r"|(?:^|[_-])token$|cookie)"
)


def redact_text(text: str) -> str:
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text


def _redact_value_for_key(key: str) -> str | None:
    if not _SENSITIVE_KEY.search(key):
        return None
    return _m("cookie") if "cookie" in key.lower() else _m("secret")


def redact(value: Any) -> Any:
    """Return a redacted copy of strings, dicts, lists and tuples; other values pass through."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            marker = _redact_value_for_key(k) if isinstance(k, str) else None
            out[k] = marker if marker is not None and v is not None else redact(v)
        return out
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value
