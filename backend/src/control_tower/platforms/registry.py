"""Wires operation catalogues and adapters together.

Mock and real connectors are isolated by construction: an environment row names
exactly one adapter key, and an adapter only exists in the registry if it was
explicitly registered at start-up.
"""

from dataclasses import dataclass, field

from control_tower.core.operations import OperationCatalog
from control_tower.core.ports import PlatformAdapter
from control_tower.platforms.salesforce.mock_adapter import MockSalesforceAdapter
from control_tower.platforms.salesforce.operations import register_salesforce_operations


@dataclass
class AdapterRegistry:
    _adapters: dict[str, PlatformAdapter] = field(default_factory=dict)

    def register(self, adapter: PlatformAdapter) -> None:
        if adapter.adapter_key in self._adapters:
            raise ValueError(f"adapter '{adapter.adapter_key}' is already registered")
        self._adapters[adapter.adapter_key] = adapter

    def get(self, adapter_key: str) -> PlatformAdapter | None:
        return self._adapters.get(adapter_key)

    def keys(self) -> frozenset[str]:
        return frozenset(self._adapters)


def build_catalog() -> OperationCatalog:
    catalog = OperationCatalog()
    register_salesforce_operations(catalog)
    return catalog


def build_adapters() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(MockSalesforceAdapter())
    return registry
