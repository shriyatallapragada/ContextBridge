from __future__ import annotations

import pytest

from contextbridge.core.sanitizer import (
    is_sensitive_file,
    sanitize,
    sanitize_with_report,
    shannon_entropy,
)

# Fake credentials assembled at runtime so this file never contains a literal secret.
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCY" + "EXAMPLEKEY"
JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ"
    ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)
GH_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
SLACK = "xoxb-" + "123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx"
STRIPE = "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc"
OPENAI = "sk-proj-" + "Abc123Def456Ghi789Jkl012Mno345"
ANTHROPIC = "sk-ant-api03-" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0"
PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEA1c7+9z5Pad7OejecsQ0bu3aumK8\n"
    "-----END RSA PRIVATE KEY-----"
)
HIGH_ENTROPY = "Q7x9Kp2LmZ4vR8tY1wN6bH3jF5sD0gA7cE2uI9oP"


@pytest.mark.parametrize(
    ("text", "kind", "secret"),
    [
        (f"key={AWS_KEY}", "aws_access_key", AWS_KEY),
        (f"aws_secret_access_key = {AWS_SECRET}", "aws_secret_key", AWS_SECRET),
        (f"token: {JWT}", "jwt", JWT),
        ("Authorization: Bearer abcDEF123456ghiJKL", "bearer_token", "abcDEF123456ghiJKL"),
        (f"use {GH_TOKEN} for CI", "github_token", GH_TOKEN),
        (f"slack {SLACK}", "slack_token", SLACK),
        (f"stripe {STRIPE}", "stripe_key", STRIPE),
        (f"openai {OPENAI}", "openai_key", OPENAI),
        (f"anthropic {ANTHROPIC}", "anthropic_key", ANTHROPIC),
        (f"before\n{PEM}\nafter", "private_key", "MIIEowIBAAKCAQEA"),
        ("DB_PASSWORD=hunter2", "env_secret", "hunter2"),
        ("export GITHUB_TOKEN='abc def'", "env_secret", "abc def"),
        ('STRIPE_SECRET_KEY="whatever-1"', "env_secret", "whatever-1"),
        ("MY_API_KEY = plainvalue", "env_secret", "plainvalue"),
        ("PRIVATE_SIGNING=xyz", "env_secret", "xyz"),
        ("clone https://alice:s3cr3tpw@github.com/x/y.git", "basic_auth", "s3cr3tpw"),
        (f"value {HIGH_ENTROPY} end", "high_entropy", HIGH_ENTROPY),
    ],
)
def test_redacts_secrets(text: str, kind: str, secret: str) -> None:
    result = sanitize_with_report(text)
    assert secret not in result.text
    assert f"[REDACTED:{kind}]" in result.text
    assert result.redactions.get(kind, 0) >= 1


@pytest.mark.parametrize(
    "text",
    [
        "commit 3f786850e387550fdab836ed7e6dc881de23001b fixed it",  # git SHA-1
        "sha256 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "request id 550e8400-e29b-41d4-a716-446655440000",  # UUID
        "sess_a1b2c3d4e5f6 and dec_0123456789ab",
        "Refactored auth middleware to use JWT validation",
        "def test_sanitizer_does_not_redact_regular_identifiers_ever(): pass",
        "path /usr/local/lib/python3.12/site-packages/contextbridge/core/sanitizer.py",
        "password = os.environ['DB_PASSWORD']",  # code, not an .env literal
        "API_TOKEN = os.environ.get('API_TOKEN')",
        "The bearer of bad news",
        "sk-short",
        "https://github.com/org/repo.git",
        "Already [REDACTED:env_secret] value",
        "",
    ],
)
def test_leaves_benign_text_alone(text: str) -> None:
    result = sanitize_with_report(text)
    assert result.text == text
    assert result.total == 0


def test_env_redaction_keeps_key_name() -> None:
    assert sanitize("DB_PASSWORD=hunter2") == "DB_PASSWORD=[REDACTED:env_secret]"


def test_bearer_keeps_scheme_word() -> None:
    assert sanitize("Bearer abcDEF123456ghiJKL") == "Bearer [REDACTED:bearer_token]"


def test_basic_auth_keeps_host() -> None:
    out = sanitize("https://alice:pw123456@example.com/path")
    assert out == "https://[REDACTED:basic_auth]@example.com/path"


def test_anthropic_key_not_labelled_openai() -> None:
    out = sanitize(ANTHROPIC)
    assert out == "[REDACTED:anthropic_key]"


def test_unterminated_private_key_redacted_to_end() -> None:
    out = sanitize("x\n-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\nmore")
    assert out == "x\n[REDACTED:private_key]"


def test_multiple_secrets_counted() -> None:
    result = sanitize_with_report(f"{AWS_KEY} and {GH_TOKEN} and {AWS_KEY}")
    assert result.redactions == {"aws_access_key": 2, "github_token": 1}
    assert result.total == 3


def test_idempotent() -> None:
    once = sanitize(f"DB_PASSWORD=hunter2 {GH_TOKEN} {JWT}")
    assert sanitize(once) == once


def test_shannon_entropy() -> None:
    assert shannon_entropy("") == 0.0
    assert shannon_entropy("aaaa") == 0.0
    assert shannon_entropy("abcd") == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (".env", True),
        (".env.local", True),
        ("config/.env.production", True),
        ("certs/server.pem", True),
        ("keys/private.key", True),
        ("/home/u/.ssh/id_rsa", True),
        ("id_rsa.pub", True),
        ("src/env.py", False),
        ("README.md", False),
        ("keyboard.py", False),
        ("docs/environment.md", False),
    ],
)
def test_is_sensitive_file(path: str, expected: bool) -> None:
    assert is_sensitive_file(path) is expected
