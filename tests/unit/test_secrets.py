"""Proves security/secrets.py actually redacts realistic-shaped secrets rather
than just matching a toy pattern."""

from __future__ import annotations

from swarmkit.security.secrets import redact


def test_redacts_anthropic_api_key():
    text = "using key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789 for auth"
    out = redact(text)
    assert "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789" not in out
    assert "REDACTED" in out


def test_redacts_openai_style_key():
    text = "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz0123456789ABCD"
    out = redact(text)
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789ABCD" not in out


def test_redacts_github_token():
    text = "token: ghp_" + "a" * 40
    out = redact(text)
    assert "ghp_" + "a" * 40 not in out


def test_redacts_aws_access_key_id():
    text = "AWS_ACCESS_KEY_ID=AKIAABCDEFGHIJKLMNOP"
    out = redact(text)
    assert "AKIAABCDEFGHIJKLMNOP" not in out


def test_redacts_private_key_block():
    text = "-----BEGIN PRIVATE KEY-----\nMIIBVQ...\n-----END PRIVATE KEY-----"
    out = redact(text)
    assert "MIIBVQ" not in out


def test_redacts_bearer_token():
    text = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345"
    out = redact(text)
    assert "abcdefghijklmnopqrstuvwxyz012345" not in out


def test_leaves_ordinary_text_untouched():
    text = "hello world, this is a normal log line with no secrets in it"
    assert redact(text) == text


def test_anthropic_key_not_double_redacted_as_generic_sk():
    # sk-ant- is more specific than the generic sk- pattern; make sure the
    # anthropic pattern fires first so we don't get double-mangled output.
    text = "sk-ant-api03-" + "x" * 30
    out = redact(text)
    assert out.count("REDACTED") == 1
