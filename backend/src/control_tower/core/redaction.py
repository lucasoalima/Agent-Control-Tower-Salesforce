"""Secret detection and redaction.

Used in three places:

* request intake: a payload that contains a credential-shaped value is refused,
  so what is stored is exactly what was requested and contains no known secret
  pattern;
* adapter results and errors, before they are stored;
* every log event.

Pattern matching cannot recognise every secret. It is a safety net behind the
primary control, which is that agents never hold privileged credentials.
"""

import re
from typing import Any

REDACTED = "[REDACTED]"

_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Salesforce session id / access token: <15-char org id>!<opaque token>
    re.compile(r"00D[A-Za-z0-9]{12}![A-Za-z0-9._]{20,}"),
    # Salesforce CLI auth URL (contains a refresh token)
    re.compile(r"force://[^\s\"']+"),
    # HTTP bearer credentials
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    # JSON Web Tokens
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    # PEM private keys
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    # Agent Control Tower agent tokens and operator session tokens
    re.compile(r"\bact_(?:agt|ses)_[A-Za-z0-9_-]{20,}"),
)

_SENSITIVE_KEY = re.compile(
    r"(?i)(pass(word|wd)?|secret|token|api[_-]?key|authorization|cookie|session[_-]?id|\bsid\b)"
)


def redact_text(text: str) -> tuple[str, int]:
    """Return ``text`` with secrets replaced, and how many replacements were made."""
    total = 0
    for pattern in _VALUE_PATTERNS:
        text, count = pattern.subn(REDACTED, text)
        total += count
    return text, total


def redact(value: Any) -> tuple[Any, int]:
    """Recursively redact strings, and values stored under sensitive-looking keys."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        total = 0
        for key, item in value.items():
            if isinstance(key, str) and _SENSITIVE_KEY.search(key) and item not in (None, ""):
                out[key] = REDACTED
                total += 1
            else:
                out[key], count = redact(item)
                total += count
        return out, total
    if isinstance(value, list | tuple):
        items = [redact(item) for item in value]
        return [item for item, _ in items], sum(count for _, count in items)
    return value, 0


def contains_secret(value: Any) -> bool:
    return bool(redact(value)[1] > 0)
