import pytest

from control_tower.security.passwords import hash_password, verify_password
from control_tower.security.throttle import LoginThrottle
from control_tower.security.tokens import (
    AGENT_TOKEN_PREFIX,
    hash_token,
    new_agent_token,
    new_session_token,
    token_hint,
)


def test_tokens_are_unique_prefixed_and_long() -> None:
    a, b = new_agent_token(), new_agent_token()
    assert a != b
    assert a.startswith(AGENT_TOKEN_PREFIX) and len(a) >= 40
    assert new_session_token().startswith("act_ses_")


def test_token_hash_is_deterministic_and_not_the_token() -> None:
    token = new_agent_token()
    assert hash_token(token) == hash_token(token)
    assert token not in hash_token(token) and len(hash_token(token)) == 64


def test_token_hint_reveals_only_a_short_prefix() -> None:
    token = new_agent_token()
    hint = token_hint(token)
    assert token.startswith(hint.rstrip("…"))
    assert len(hint) <= len(AGENT_TOKEN_PREFIX) + 5


def test_password_round_trip() -> None:
    hashed = hash_password("a-long-enough-password")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "a-long-enough-password")
    assert not verify_password(hashed, "a-long-enough-passwore")


def test_unknown_account_never_verifies() -> None:
    assert not verify_password(None, "not-a-real-password")
    assert not verify_password("garbage", "anything")


def test_short_passwords_are_rejected() -> None:
    with pytest.raises(ValueError, match="at least"):
        hash_password("short")


def test_throttle_blocks_after_repeated_failures_and_recovers() -> None:
    now = [0.0]
    throttle = LoginThrottle(max_failures=3, window_seconds=60, now=lambda: now[0])
    for _ in range(3):
        assert not throttle.is_blocked("op")
        throttle.record_failure("op")
    assert throttle.is_blocked("op")
    assert not throttle.is_blocked("someone-else")
    now[0] = 61.0
    assert not throttle.is_blocked("op")


def test_throttle_reset_clears_failures() -> None:
    throttle = LoginThrottle(max_failures=1)
    throttle.record_failure("op")
    assert throttle.is_blocked("op")
    throttle.reset("op")
    assert not throttle.is_blocked("op")
