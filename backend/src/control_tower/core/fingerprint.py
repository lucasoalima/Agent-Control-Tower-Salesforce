"""Action fingerprinting.

The fingerprint identifies *exactly one* action: this agent, this target
environment, this operation, these parameters. Approvals (Phase 2) are bound to
it, so an approval for one payload can never authorise a different one.
"""

import hashlib
import json
import uuid
from typing import Any

FINGERPRINT_VERSION = 1


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no insignificant whitespace, UTF-8 as is.

    Floats are rejected because their textual form is not stable across
    producers; operation schemas do not use them.
    """
    _reject_floats(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _reject_floats(value: Any) -> None:
    if isinstance(value, float):
        raise TypeError("floats cannot be fingerprinted deterministically")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("only string keys can be fingerprinted")
            _reject_floats(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _reject_floats(item)


def action_fingerprint(
    *,
    agent_id: uuid.UUID,
    environment_id: uuid.UUID,
    operation_key: str,
    parameters: dict[str, Any],
) -> str:
    envelope = {
        "v": FINGERPRINT_VERSION,
        "agent_id": str(agent_id),
        "environment_id": str(environment_id),
        "operation": operation_key,
        "parameters": parameters,
    }
    return hashlib.sha256(canonical_json(envelope).encode("utf-8")).hexdigest()
