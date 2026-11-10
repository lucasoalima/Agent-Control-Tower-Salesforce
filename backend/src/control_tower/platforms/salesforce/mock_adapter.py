"""In-memory Salesforce adapter.

Lets the whole application run without Salesforce credentials and gives tests
and evaluation scenarios an organisation whose state can be inspected directly
("did a deployment actually happen?").

Behaviour is deterministic:

* a class whose name ends in ``Test`` is a test class;
* a test class whose body contains ``MOCK:FAIL`` fails when run;
* a deployment that runs tests fails, and changes nothing, if any test fails.

State lives in process memory and is lost on restart. Nothing here talks to
Salesforce, and the data is synthetic.
"""

import itertools
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from control_tower.core.operations import OperationParams
from control_tower.core.ports import AdapterError, TargetEnvironment
from control_tower.platforms.salesforce import operations as ops

FAIL_MARKER = "MOCK:FAIL"

ComponentKey = tuple[str, str]  # (metadata type, full name)


def _seed_components() -> dict[ComponentKey, str]:
    return {
        ("ApexClass", "AccountService"): (
            "public with sharing class AccountService {\n"
            "    public static Integer countAccounts() {\n"
            "        return [SELECT COUNT() FROM Account];\n"
            "    }\n"
            "}\n"
        ),
        ("ApexClass", "AccountServiceTest"): (
            "@IsTest\n"
            "private class AccountServiceTest {\n"
            "    @IsTest static void countsAccounts() {\n"
            "        System.assertEquals(0, AccountService.countAccounts());\n"
            "    }\n"
            "}\n"
        ),
        ("ApexTrigger", "AccountTrigger"): (
            "trigger AccountTrigger on Account (before insert) {}\n"
        ),
        ("CustomObject", "Invoice__c"): "<CustomObject><label>Invoice</label></CustomObject>",
        (
            "PermissionSet",
            "Sales_Operations",
        ): "<PermissionSet><label>Sales Operations</label></PermissionSet>",
    }


def _seed_records() -> dict[str, list[dict[str, Any]]]:
    return {
        "Account": [
            {"Id": "001MOCK000000001", "Name": "Example Retail (mock)", "Industry": "Retail"},
            {
                "Id": "001MOCK000000002",
                "Name": "Example Logistics (mock)",
                "Industry": "Transportation",
            },
            {"Id": "001MOCK000000003", "Name": "Example Health (mock)", "Industry": "Healthcare"},
        ],
        "Contact": [
            {"Id": "003MOCK000000001", "LastName": "Sample", "AccountId": "001MOCK000000001"},
        ],
    }


@dataclass
class MockOrg:
    """State of one simulated organisation."""

    org_id: str
    components: dict[ComponentKey, str] = field(default_factory=_seed_components)
    records: dict[str, list[dict[str, Any]]] = field(default_factory=_seed_records)
    object_permissions: dict[str, dict[str, dict[str, bool]]] = field(default_factory=dict)
    deployments: list[dict[str, Any]] = field(default_factory=list)
    test_runs: list[dict[str, Any]] = field(default_factory=list)

    def test_classes(self) -> list[str]:
        return sorted(
            name
            for (mtype, name) in self.components
            if mtype == "ApexClass" and name.endswith("Test")
        )


class MockSalesforceAdapter:
    adapter_key = "mock-salesforce"
    platform = ops.PLATFORM

    def __init__(self) -> None:
        self._orgs: dict[uuid.UUID, MockOrg] = {}
        self._lock = threading.Lock()
        self._sequence = itertools.count(1)
        self._handlers: dict[str, Callable[[MockOrg, TargetEnvironment, Any], dict[str, Any]]] = {
            ops.ORG_DESCRIBE: self._describe_org,
            ops.METADATA_LIST: self._list_metadata,
            ops.METADATA_READ: self._read_metadata,
            ops.DATA_QUERY: self._query,
            ops.APEX_WRITE: self._write_apex,
            ops.PERMISSION_SET_MODIFY: self._modify_permission_set,
            ops.DEPLOY: self._deploy,
            ops.TESTS_RUN: self._run_tests_operation,
        }
        # Test hook: operation key -> exception to raise instead of running it.
        self.faults: dict[str, Exception] = {}

    # -- PlatformAdapter ---------------------------------------------------

    def supports(self, operation_key: str) -> bool:
        return operation_key in self._handlers

    def execute(
        self, target: TargetEnvironment, operation_key: str, params: OperationParams
    ) -> dict[str, Any]:
        handler = self._handlers.get(operation_key)
        if handler is None:
            raise AdapterError("UNSUPPORTED_OPERATION", f"'{operation_key}' is not supported")
        if operation_key in self.faults:
            raise self.faults[operation_key]
        with self._lock:
            return handler(self.org(target), target, params)

    # -- inspection (tests and evaluation scenarios) ------------------------

    def org(self, target: TargetEnvironment) -> MockOrg:
        org = self._orgs.get(target.id)
        if org is None:
            org = self._orgs[target.id] = MockOrg(org_id=target.external_id)
        return org

    # -- handlers ----------------------------------------------------------

    def _describe_org(
        self, org: MockOrg, target: TargetEnvironment, _: ops.DescribeOrgParams
    ) -> dict[str, Any]:
        return {
            "org_id": org.org_id,
            "name": target.name,
            "environment_kind": target.kind.value,
            "is_mock": True,
            "component_count": len(org.components),
        }

    def _list_metadata(
        self, org: MockOrg, _: TargetEnvironment, params: ops.ListMetadataParams
    ) -> dict[str, Any]:
        names = sorted(n for (t, n) in org.components if t == params.metadata_type)
        return {"metadata_type": params.metadata_type, "components": names}

    def _read_metadata(
        self, org: MockOrg, _: TargetEnvironment, params: ops.ReadMetadataParams
    ) -> dict[str, Any]:
        body = org.components.get((params.metadata_type, params.full_name))
        if body is None:
            raise AdapterError(
                "NOT_FOUND", f"{params.metadata_type} '{params.full_name}' does not exist"
            )
        return {"metadata_type": params.metadata_type, "full_name": params.full_name, "body": body}

    def _query(
        self, org: MockOrg, _: TargetEnvironment, params: ops.QueryDataParams
    ) -> dict[str, Any]:
        rows = org.records.get(params.sobject)
        if rows is None:
            raise AdapterError("INVALID_TYPE", f"sObject '{params.sobject}' is not queryable")
        known = set().union(*(row.keys() for row in rows)) if rows else set()
        unknown = [f for f in params.fields if f not in known]
        if unknown:
            raise AdapterError(
                "INVALID_FIELD", f"No such field on {params.sobject}: {', '.join(unknown)}"
            )
        selected = [{f: row.get(f) for f in params.fields} for row in rows[: params.limit]]
        return {"sobject": params.sobject, "total_size": len(selected), "records": selected}

    def _write_apex(
        self, org: MockOrg, _: TargetEnvironment, params: ops.WriteApexParams
    ) -> dict[str, Any]:
        key = ("ApexClass", params.class_name)
        created = key not in org.components
        org.components[key] = params.body
        return {"class_name": params.class_name, "created": created}

    def _modify_permission_set(
        self, org: MockOrg, _: TargetEnvironment, params: ops.ModifyPermissionSetParams
    ) -> dict[str, Any]:
        if ("PermissionSet", params.permission_set) not in org.components:
            raise AdapterError(
                "NOT_FOUND", f"PermissionSet '{params.permission_set}' does not exist"
            )
        grants = org.object_permissions.setdefault(params.permission_set, {})
        for perm in params.object_permissions:
            grants[perm.sobject] = {
                "read": perm.allow_read,
                "create": perm.allow_create,
                "edit": perm.allow_edit,
                "delete": perm.allow_delete,
            }
        return {
            "permission_set": params.permission_set,
            "objects_changed": [p.sobject for p in params.object_permissions],
        }

    def _deploy(
        self, org: MockOrg, _: TargetEnvironment, params: ops.DeployParams
    ) -> dict[str, Any]:
        deployment_id = f"0AfMOCK{next(self._sequence):09d}"
        staged = dict(org.components)
        for component in params.components:
            staged[(component.metadata_type, component.full_name)] = component.body

        tests: dict[str, Any] | None = None
        if params.test_level != "NoTestRun":
            selected = (
                list(params.specified_tests)
                if params.test_level == "RunSpecifiedTests"
                else sorted(n for (t, n) in staged if t == "ApexClass" and n.endswith("Test"))
            )
            tests = self._run_tests(staged, selected)

        succeeded = tests is None or tests["failed"] == 0
        record = {
            "deployment_id": deployment_id,
            "check_only": params.check_only,
            "test_level": params.test_level,
            "components": [
                {"metadata_type": c.metadata_type, "full_name": c.full_name}
                for c in params.components
            ],
            "tests": tests,
            "success": succeeded,
        }
        org.deployments.append(record)

        if not succeeded:
            raise AdapterError(
                "DEPLOY_FAILED",
                "Deployment failed because Apex tests failed. Nothing was changed.",
                details={"deployment_id": deployment_id, "tests": tests},
            )
        if not params.check_only:
            org.components = staged
        return record

    def _run_tests_operation(
        self, org: MockOrg, _: TargetEnvironment, params: ops.RunTestsParams
    ) -> dict[str, Any]:
        selected = list(params.class_names) or org.test_classes()
        result = self._run_tests(org.components, selected)
        run = {"test_run_id": f"707MOCK{next(self._sequence):09d}", **result}
        org.test_runs.append(run)
        return run

    @staticmethod
    def _run_tests(components: dict[ComponentKey, str], class_names: list[str]) -> dict[str, Any]:
        outcomes = []
        for name in class_names:
            body = components.get(("ApexClass", name))
            if body is None:
                raise AdapterError("NOT_FOUND", f"Apex test class '{name}' does not exist")
            outcomes.append(
                {"class_name": name, "outcome": "Fail" if FAIL_MARKER in body else "Pass"}
            )
        failed = sum(1 for o in outcomes if o["outcome"] == "Fail")
        return {
            "tests_run": len(outcomes),
            "passed": len(outcomes) - failed,
            "failed": failed,
            "outcomes": outcomes,
        }
