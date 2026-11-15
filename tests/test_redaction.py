"""Redaction rules: tokens, passwords, API keys, cookies, emails and card numbers are removed
before any LLM call (DESIGN: Size limits and redaction). Written before the code."""

import json

import pytest

from faultline.redaction import redact, redact_text

# ---- things that must be removed ----

SECRETS = [
    # (input, secret substring that must disappear, expected marker)
    ("Authorization: Bearer abcdef1234567890XYZ", "abcdef1234567890XYZ", "[REDACTED:token]"),
    ("curl -H 'authorization: Basic dXNlcjpwYXNzd29yZA=='", "dXNlcjpwYXNzd29yZA==",
     "[REDACTED:token]"),
    ("jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
     "eyJhbGciOiJIUzI1NiJ9", "[REDACTED:"),
    ("token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 leaked",
     "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "[REDACTED:token]"),
    ("slack xoxb-1234567890-abcdefghijkl", "xoxb-1234567890-abcdefghijkl", "[REDACTED:token]"),
    ("aws AKIAIOSFODNN7EXAMPLE key", "AKIAIOSFODNN7EXAMPLE", "[REDACTED:token]"),
    ("OPENAI_KEY sk-proj-abcdefghijklmnop1234", "sk-proj-abcdefghijklmnop1234", "[REDACTED:token]"),
    ("password=hunter2&user=bob", "hunter2", "[REDACTED:secret]"),
    ('{"password": "s3cr3t!"}', "s3cr3t!", "[REDACTED:secret]"),
    ("DB_PASSWORD: correcthorse", "correcthorse", "[REDACTED:secret]"),
    ("api_key=AbC123xyz", "AbC123xyz", "[REDACTED:secret]"),
    ("X-Api-Key: 0f0f0f0f-key", "0f0f0f0f-key", "[REDACTED:secret]"),
    ("client_secret=topsecretvalue", "topsecretvalue", "[REDACTED:secret]"),
    ("postgresql://faultline:pa55word@db.faultline.svc:5432/faultline", "pa55word",
     "[REDACTED:secret]"),
    ("Cookie: session=abc123; cart=xyz789", "abc123", "[REDACTED:cookie]"),
    ("Set-Cookie: sid=deadbeefcafe; Path=/; HttpOnly", "deadbeefcafe", "[REDACTED:cookie]"),
    ("contact jane.doe+ops@example.com today", "jane.doe+ops@example.com", "[REDACTED:email]"),
    ("card 4111 1111 1111 1111 declined", "4111 1111 1111 1111", "[REDACTED:card]"),
    ("card=5500-0000-0000-0004", "5500-0000-0000-0004", "[REDACTED:card]"),
    ("amex 378282246310005", "378282246310005", "[REDACTED:card]"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nIBAAKC\n-----END RSA PRIVATE KEY-----",
     "MIIEow", "[REDACTED:private_key]"),
]


@pytest.mark.parametrize(("text", "secret", "marker"), SECRETS)
def test_secret_removed(text: str, secret: str, marker: str) -> None:
    out = redact_text(text)
    assert secret not in out
    assert marker in out


def test_header_names_kept_for_context() -> None:
    out = redact_text("Authorization: Bearer abcdef1234567890")
    assert out.startswith("Authorization: Bearer ")
    assert redact_text("password=hunter2").startswith("password=")


def test_no_double_redaction() -> None:
    out = redact_text("token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    assert out == "token=[REDACTED:token]"


# ---- things that must survive (the agent needs them) ----

KEEP = [
    "client 172.18.5.23 sent 4000 requests",           # IPs are evidence for block_source
    "172.18.5.0/24",
    "commit 9f3c2a1 reverted 9f3c2a1b4d5e6f708192a3b4c5d6e7f809112233",
    "2026-11-08T10:14:02Z",
    "GET /cart/checkout 503",
    "pod checkoutservice-7d9f8b6c5-x2k4p restarted 3 times",
    "incident 3f1c2b4a-1d2e-4f5a-9b8c-7d6e5f4a3b2c",
    "epoch_ms 1731060842000",                          # not a card (no card prefix)
    "order 1234567890123",                             # fails Luhn / prefix
    "git@github.com:owner/faultline-gitops.git",       # a repo address, not an email
    "max_tokens=4000 input_tokens=812",
    "author=alice",
]


@pytest.mark.parametrize("text", KEEP)
def test_benign_text_unchanged(text: str) -> None:
    assert redact_text(text) == text


# ---- structured data ----

def test_redact_nested_structures() -> None:
    data = {
        "request_Cookie": "session=abc",
        "headers": {"Authorization": "Bearer abcdef1234567890", "Accept": "text/html"},
        "items": ["mail bob@example.com", 42, None, True],
        "ClientHost": "172.18.5.23",
        "input_tokens": 812,
        "api_key": 12345,
    }
    out = redact(data)
    assert out["request_Cookie"] == "[REDACTED:cookie]"
    assert out["headers"]["Authorization"] == "[REDACTED:secret]"
    assert out["headers"]["Accept"] == "text/html"
    assert out["items"] == ["mail [REDACTED:email]", 42, None, True]
    assert out["ClientHost"] == "172.18.5.23"
    assert out["input_tokens"] == 812
    assert out["api_key"] == "[REDACTED:secret]"
    assert data["request_Cookie"] == "session=abc"  # input not mutated


def test_traefik_access_log_line() -> None:
    line = json.dumps({
        "ClientHost": "172.18.5.40",
        "RequestMethod": "POST",
        "RequestPath": "/cart/checkout?email=eve@example.com&card=4111111111111111",
        "DownstreamStatus": 402,
        "request_User-Agent": "k6/0.54",
        "request_Authorization": "Bearer zzzzzzzzzzzzzzzz",
    })
    out = redact(json.loads(line))
    flat = json.dumps(out)
    for secret in ("eve@example.com", "4111111111111111", "zzzzzzzzzzzzzzzz"):
        assert secret not in flat
    assert out["ClientHost"] == "172.18.5.40"
    assert out["DownstreamStatus"] == 402


def test_pydantic_like_tuples_and_lists() -> None:
    assert redact(("a@b.io", "ok")) == ["[REDACTED:email]", "ok"]
