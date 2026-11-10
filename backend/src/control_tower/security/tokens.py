"""Opaque bearer tokens for agents and operator sessions.

Tokens are 256 bits of randomness, so a plain SHA-256 digest is an adequate
at-rest representation: there is nothing to brute-force. Only the digest is
stored; the token itself is shown exactly once, when it is created.
"""

import hashlib
import secrets

AGENT_TOKEN_PREFIX = "act_agt_"  # noqa: S105 - a prefix, not a credential
SESSION_TOKEN_PREFIX = "act_ses_"  # noqa: S105


def new_agent_token() -> str:
    return AGENT_TOKEN_PREFIX + secrets.token_urlsafe(32)


def new_session_token() -> str:
    return SESSION_TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_hint(token: str) -> str:
    """A short, non-secret label that helps a human recognise which token is which."""
    return token[: len(AGENT_TOKEN_PREFIX) + 4] + "…"
