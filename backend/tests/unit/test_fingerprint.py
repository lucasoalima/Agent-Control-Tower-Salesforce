import uuid

import pytest

from control_tower.core.fingerprint import action_fingerprint, canonical_json

AGENT = uuid.UUID("11111111-1111-1111-1111-111111111111")
ENV = uuid.UUID("22222222-2222-2222-2222-222222222222")


def fp(**overrides: object) -> str:
    args: dict[str, object] = {
        "agent_id": AGENT,
        "environment_id": ENV,
        "operation_key": "salesforce.deploy",
        "parameters": {"a": 1, "b": ["x", {"c": True}]},
    }
    args.update(overrides)
    return action_fingerprint(**args)  # type: ignore[arg-type]


def test_canonical_json_ignores_key_order_and_whitespace() -> None:
    assert canonical_json({"b": 1, "a": {"d": 2, "c": 3}}) == '{"a":{"c":3,"d":2},"b":1}'


def test_same_action_same_fingerprint_regardless_of_key_order() -> None:
    assert fp(parameters={"a": 1, "b": 2}) == fp(parameters={"b": 2, "a": 1})


def test_list_order_is_significant() -> None:
    assert fp(parameters={"a": [1, 2]}) != fp(parameters={"a": [2, 1]})


@pytest.mark.parametrize(
    "override",
    [
        {"agent_id": uuid.UUID(int=3)},
        {"environment_id": uuid.UUID(int=4)},
        {"operation_key": "salesforce.apex.write"},
        {"parameters": {"a": 2, "b": ["x", {"c": True}]}},
        {"parameters": {"a": 1, "b": ["x", {"c": False}]}},
        {"parameters": {"a": 1, "b": ["x", {"c": True}], "extra": None}},
    ],
)
def test_any_change_to_the_action_changes_the_fingerprint(override: dict[str, object]) -> None:
    assert fp(**override) != fp()


def test_fingerprint_is_stable_across_releases() -> None:
    # Pinned value. If this changes, stored hashes and outstanding approvals no
    # longer match: bump FINGERPRINT_VERSION and write a migration note instead.
    assert fp() == "b72ce6e40a9f4af6aba0de895d63090b85d2cd83e3268e0a6b91b702ddab29a0"


def test_floats_are_rejected() -> None:
    with pytest.raises(TypeError):
        canonical_json({"ratio": 0.1})


def test_non_ascii_is_hashed_as_utf8_not_escaped() -> None:
    assert canonical_json({"name": "São Paulo"}) == '{"name":"São Paulo"}'
