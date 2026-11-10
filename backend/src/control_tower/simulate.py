"""Replay a fixed set of agent tool requests through the gateway.

This is a development aid for filling the dashboard with real records. It calls
the same ``Gateway.submit`` the HTTP API calls, so every record it produces is
the result of an actual policy decision and an actual (mock) execution. It does
not exercise token authentication; the API tests cover that.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from control_tower.core.operations import OperationCatalog
from control_tower.gateway.service import Gateway
from control_tower.persistence.models import Agent, Execution
from control_tower.platforms.registry import AdapterRegistry
from control_tower.platforms.salesforce import operations as sf

_INVOICE_SERVICE = (
    "public with sharing class InvoiceService {\n"
    "    public static Decimal total(List<Invoice__c> invoices) {\n"
    "        Decimal sum = 0;\n"
    "        for (Invoice__c i : invoices) { sum += i.Amount__c; }\n"
    "        return sum;\n"
    "    }\n"
    "}\n"
)

SCRIPT: list[tuple[str, str, dict[str, Any]]] = [
    ("claude-code-dev", sf.ORG_DESCRIBE, {}),
    ("claude-code-dev", sf.METADATA_LIST, {"metadata_type": "ApexClass"}),
    (
        "claude-code-dev",
        sf.METADATA_READ,
        {"metadata_type": "ApexClass", "full_name": "AccountService"},
    ),
    ("claude-code-dev", sf.DATA_QUERY, {"sobject": "Account", "fields": ["Id", "Name"]}),
    ("claude-code-dev", sf.TESTS_RUN, {}),
    ("claude-code-dev", sf.APEX_WRITE, {"class_name": "InvoiceService", "body": _INVOICE_SERVICE}),
    (
        "claude-code-dev",
        sf.METADATA_READ,
        {"metadata_type": "ApexClass", "full_name": "DoesNotExist"},
    ),
    ("codex-uat", sf.ORG_DESCRIBE, {}),
    (
        "codex-uat",
        sf.DEPLOY,
        {
            "components": [
                {
                    "metadata_type": "ApexClass",
                    "full_name": "InvoiceService",
                    "body": _INVOICE_SERVICE,
                }
            ],
            "test_level": "RunLocalTests",
        },
    ),
    (
        "codex-uat",
        sf.PERMISSION_SET_MODIFY,
        {
            "permission_set": "Sales_Operations",
            "object_permissions": [
                {"sobject": "Invoice__c", "allow_read": True, "allow_edit": True}
            ],
        },
    ),
    ("codex-uat", "salesforce.shell.exec", {"command": "sf project deploy start"}),
    ("codex-uat", sf.METADATA_READ, {"metadata_type": "ApexClass", "full_name": "../etc/passwd"}),
    ("codex-uat", sf.DATA_QUERY, {"sobject": "Account", "fields": ["Id", "AnnualRevenue"]}),
    ("claude-code-prod", sf.ORG_DESCRIBE, {}),
    (
        "claude-code-prod",
        sf.DEPLOY,
        {
            "components": [
                {
                    "metadata_type": "ApexClass",
                    "full_name": "InvoiceService",
                    "body": _INVOICE_SERVICE,
                }
            ],
            "test_level": "RunLocalTests",
        },
    ),
    ("claude-code-prod", sf.DATA_QUERY, {"sobject": "Account", "fields": ["Id", "Name"]}),
]


def simulate(
    session: Session,
    catalog: OperationCatalog,
    adapters: AdapterRegistry,
    *,
    approval_ttl_seconds: int = 300,
) -> list[Execution]:
    gateway = Gateway(session, catalog, adapters, approval_ttl_seconds=approval_ttl_seconds)
    agents = {a.name: a for a in session.scalars(select(Agent))}
    missing = {name for name, _, _ in SCRIPT} - agents.keys()
    if missing:
        raise RuntimeError(f"demo agents not found ({', '.join(sorted(missing))}); run `act seed`")
    return [
        gateway.submit(agents[name], operation, parameters, f"sim-{uuid.uuid4().hex[:12]}")
        for name, operation, parameters in SCRIPT
    ]
