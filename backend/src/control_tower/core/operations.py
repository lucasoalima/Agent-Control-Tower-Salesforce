"""Operation catalogue.

An *operation* is the unit that policy is written against and the only thing an
agent can ask the gateway to do. Each one has a stable key, a risk class and a
strict parameter schema. Operations are defined in code, not in the database:
adding one means writing a schema and an adapter implementation, and both need
review.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from control_tower.core.enums import RiskClass


class OperationParams(BaseModel):
    """Base class for operation parameters.

    * ``extra="forbid"``: unknown fields are an error, never silently dropped.
    * ``strict=True``: no type coercion ("1" is not 1, 1 is not true).
    * ``frozen=True``: validated parameters cannot be modified afterwards.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    def canonical(self) -> dict[str, Any]:
        """JSON-safe dict with defaults filled in. This is what gets stored and hashed."""
        return self.model_dump(mode="json")


@dataclass(frozen=True, slots=True)
class OperationSpec:
    key: str
    platform: str
    title: str
    description: str
    risk: RiskClass
    params_model: type[OperationParams]


class OperationCatalog:
    def __init__(self) -> None:
        self._specs: dict[str, OperationSpec] = {}

    def register(self, spec: OperationSpec) -> None:
        if spec.key in self._specs:
            raise ValueError(f"operation '{spec.key}' is already registered")
        self._specs[spec.key] = spec

    def get(self, key: str) -> OperationSpec | None:
        return self._specs.get(key)

    def __contains__(self, key: object) -> bool:
        return key in self._specs

    def __iter__(self) -> Iterator[OperationSpec]:
        return iter(sorted(self._specs.values(), key=lambda s: s.key))

    def __len__(self) -> int:
        return len(self._specs)

    def keys(self) -> frozenset[str]:
        return frozenset(self._specs)
