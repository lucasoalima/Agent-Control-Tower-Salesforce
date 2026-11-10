"""Interfaces between the platform-agnostic core and platform adapters."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from control_tower.core.enums import EnvironmentKind
from control_tower.core.operations import OperationParams


@dataclass(frozen=True, slots=True)
class TargetEnvironment:
    """The environment an operation runs against, as seen by an adapter."""

    id: uuid.UUID
    name: str
    platform: str
    kind: EnvironmentKind
    external_id: str


class AdapterError(Exception):
    """An operation failed in a way the adapter understands and can describe."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@runtime_checkable
class PlatformAdapter(Protocol):
    """Executes operations against one enterprise platform.

    Adapters are the only components that hold platform credentials. They are
    called by the gateway *after* policy has been evaluated and never decide
    authorisation themselves.
    """

    adapter_key: str
    platform: str

    def supports(self, operation_key: str) -> bool: ...

    def execute(
        self, target: TargetEnvironment, operation_key: str, params: OperationParams
    ) -> dict[str, Any]:
        """Run the operation and return a JSON-serialisable result.

        Raises ``AdapterError`` for expected failures (failed deployment,
        missing component). Any other exception is treated as an adapter crash.
        """
        ...


class Clock(Protocol):
    def __call__(self) -> datetime: ...
