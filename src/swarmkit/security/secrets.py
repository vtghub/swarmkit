"""Pattern-based secret redaction. Used before anything (subprocess stdout/
stderr, tool call arguments) is written to the audit log — a sandboxed
command that happens to echo an API key or token shouldn't leave it sitting
in plaintext in a durable log forever.

This is regex/allowlist redaction, not ML-based PII detection — matching
the architecture's stated approach for federation-adjacent security (start
with real, auditable patterns; don't claim a detector that hasn't been
built). It will not catch every possible secret shape, but every pattern
here is a real, testable rule, not a placeholder.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic_api_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai_api_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,255}")),
    ("aws_access_key_id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key_block", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"
        r"[\s\S]+?"
        r"-----END (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"
    )),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9\-_.=]{8,}")),
    ("key_value_secret", re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?key|secret|password|token)\b\s*[:=]\s*"
        r"['\"]?[A-Za-z0-9\-_./+=]{8,}['\"]?"
    )),
]


def redact(text: str) -> str:
    """Replace every recognized secret pattern in `text` with a
    `[REDACTED:<kind>]` marker. Order matters: more specific patterns (e.g.
    `sk-ant-...`) run before more general ones (`sk-...`) so a specific match
    isn't double-redacted by a broader pattern afterward."""
    if not text:
        return text
    redacted = text
    for name, pattern in _PATTERNS:
        redacted = pattern.sub(f"[REDACTED:{name}]", redacted)
    return redacted
