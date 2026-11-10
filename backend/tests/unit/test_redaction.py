import pytest

from control_tower.core.redaction import REDACTED, contains_secret, redact, redact_text

SF_TOKEN = "00D5g00000ABCDE!AQEAQPw4x.Zt8Q2y7mN1bVc3Xz5Lk9Jh7Gf4Ds2Aq"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nABC\n-----END RSA PRIVATE KEY-----"


@pytest.mark.parametrize(
    "secret",
    [
        SF_TOKEN,
        JWT,
        PEM,
        "Bearer abcdefghijklmnopqrstuvwxyz012345",
        "force://PlatformCLI::" + "FAKE-REFRESH-TOKEN" + "@example.my.salesforce.com",
        "act_agt_0123456789abcdefghijklmnopqrstuv",
        "act_ses_0123456789abcdefghijklmnopqrstuv",
    ],
)
def test_secret_shapes_are_redacted(secret: str) -> None:
    text, count = redact_text(f"before {secret} after")
    assert count == 1
    assert secret not in text
    assert text.startswith("before ") and text.endswith(" after")
    assert REDACTED in text


def test_ordinary_text_is_untouched() -> None:
    apex = (
        "public class TokenService { String label = 'password reset'; Id org = '00D5g00000ABCDE'; }"
    )
    assert redact_text(apex) == (apex, 0)


def test_nested_structures_are_walked() -> None:
    value = {"a": [{"b": f"x {SF_TOKEN}"}, "clean"], "n": 3, "none": None}
    redacted, count = redact(value)
    assert count == 1
    assert redacted == {"a": [{"b": f"x {REDACTED}"}, "clean"], "n": 3, "none": None}
    assert value["a"][0]["b"].endswith(SF_TOKEN), "input must not be mutated"  # type: ignore[index]


@pytest.mark.parametrize(
    "key", ["password", "client_secret", "access_token", "apiKey", "Authorization", "session_id"]
)
def test_values_under_sensitive_keys_are_redacted(key: str) -> None:
    redacted, count = redact({key: "hunter2", "other": "hunter2"})
    assert redacted == {key: REDACTED, "other": "hunter2"}
    assert count == 1


def test_contains_secret() -> None:
    assert contains_secret({"body": f"String t = '{SF_TOKEN}';"})
    assert not contains_secret({"body": "String t = 'hello';"})
