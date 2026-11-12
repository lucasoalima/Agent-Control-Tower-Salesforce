"""Salesforce reads use HTTP mocks only; credentials are generated at runtime."""

import json
import secrets
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs
from uuid import uuid4

import httpx2 as httpx
import pytest
from pydantic import SecretStr, ValidationError

from control_tower.core.enums import EnvironmentKind
from control_tower.core.ports import AdapterError, TargetEnvironment
from control_tower.platforms.salesforce import operations as ops
from control_tower.platforms.salesforce.read_adapter import (
    ORG_QUERY,
    SalesforceConnection,
    SalesforceReadAdapter,
    SalesforceSettings,
)


class Harness:
    def __init__(self) -> None:
        self.oauth_secret = secrets.token_urlsafe(32)
        self.connection = SalesforceConnection(
            environment_id=uuid4(),
            name="Synthetic-" + uuid4().hex[:8],
            kind=EnvironmentKind.TESTING,
            org_id="00D" + uuid4().hex[:12],
            origin="https://example.sandbox.my.salesforce.com",
            api_version="66.0",
            client_id=SecretStr(secrets.token_urlsafe(32)),
            client_secret=SecretStr(secrets.token_urlsafe(32)),
        )
        self.target = TargetEnvironment(
            id=self.connection.environment_id,
            name=self.connection.name,
            platform="salesforce",
            kind=self.connection.kind,
            external_id=self.connection.org_id,
        )
        self.calls: list[httpx.Request] = []
        self.failure: str | None = None
        self.org_id = self.connection.org_id
        self.instance_url = self.connection.origin
        self.rows: list[dict[str, Any]] = [
            {"Name": "Synthetic account", "attributes": {"url": "/internal"}}
        ]
        self.done = True
        self.transport = httpx.MockTransport(self.handle)
        self.adapter = SalesforceReadAdapter([self.connection], transport=self.transport)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.failure == "timeout":
            raise httpx.ReadTimeout(self.oauth_secret, request=request)
        if self.failure == "redirect":
            return httpx.Response(307, headers={"Location": "https://elsewhere.example"})
        if self.failure == "http_error":
            return httpx.Response(401, text=self.oauth_secret)
        if self.failure == "invalid_json":
            return httpx.Response(200, text=self.oauth_secret)
        if self.failure == "oversize":
            return httpx.Response(200, content=b"x" * 1_048_577)
        if request.url.path == "/services/oauth2/token":
            assert request.method == "POST"
            data = parse_qs(request.content.decode())
            assert data == {
                "grant_type": ["client_credentials"],
                "client_id": [self.connection.client_id.get_secret_value()],
                "client_secret": [self.connection.client_secret.get_secret_value()],
            }
            assert not request.url.query
            return httpx.Response(
                200,
                json={
                    "access_token": self.oauth_secret,
                    "instance_url": self.instance_url,
                    "token_type": "Bearer",
                },
            )
        assert request.method == "GET"
        assert request.url.path == "/services/data/v66.0/query"
        assert request.headers["Authorization"] == "Bearer " + self.oauth_secret
        assert "cookie" not in request.headers
        if request.url.params["q"] == ORG_QUERY:
            return httpx.Response(
                200,
                json={
                    "done": True,
                    "totalSize": 1,
                    "records": [
                        {
                            "Id": self.org_id,
                            "Name": "Synthetic organisation",
                            "InstanceName": "TEST",
                            "IsSandbox": True,
                            "OrganizationType": "Enterprise Edition",
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "done": self.done,
                "totalSize": len(self.rows),
                "records": self.rows,
                "nextRecordsUrl": "https://elsewhere.example/not-followed",
            },
        )

    def execute(self, operation: str = ops.ORG_DESCRIBE) -> dict[str, Any]:
        params = (
            ops.QueryDataParams(sobject="Account", fields=["Name"], limit=5)
            if operation == ops.DATA_QUERY
            else ops.DescribeOrgParams()
        )
        return self.adapter.execute(self.target, operation, params)

    def config_json(self) -> str:
        values = self.connection.model_dump(mode="json", exclude={"client_id", "client_secret"})
        values["client_id"] = self.connection.client_id.get_secret_value()
        values["client_secret"] = self.connection.client_secret.get_secret_value()
        return json.dumps([values])


def test_describe_authenticates_and_checks_org_identity() -> None:
    case = Harness()
    result = case.execute()
    assert result["org_id"] == case.org_id
    assert result["is_mock"] is False
    assert result["is_sandbox"] is True
    assert len(case.calls) == 2


def test_query_is_compiled_from_validated_fields_and_bounded() -> None:
    case = Harness()
    result = case.execute(ops.DATA_QUERY)
    assert case.calls[-1].url.params["q"] == "SELECT Name FROM Account LIMIT 5"
    assert result == {
        "sobject": "Account",
        "total_size": 1,
        "records": [{"Name": "Synthetic account"}],
    }
    assert len(case.calls) == 3


@pytest.mark.parametrize(
    "operation",
    [
        ops.METADATA_LIST,
        ops.METADATA_READ,
        ops.APEX_WRITE,
        ops.PERMISSION_SET_MODIFY,
        ops.DEPLOY,
        ops.TESTS_RUN,
        "unknown",
    ],
)
def test_unsupported_operations_never_make_http_calls(operation: str) -> None:
    case = Harness()
    assert not case.adapter.supports(operation)
    with pytest.raises(AdapterError, match="only supports"):
        case.execute(operation)
    assert case.calls == []


@pytest.mark.parametrize("field", ["id", "platform", "kind", "external_id"])
def test_target_mismatch_is_refused_before_authentication(field: str) -> None:
    case = Harness()
    changed = {
        "id": uuid4(),
        "platform": "other",
        "kind": EnvironmentKind.PRODUCTION,
        "external_id": "00D" + uuid4().hex[:12],
    }
    target = replace(case.target, **{field: changed[field]})
    with pytest.raises(AdapterError, match="matching Salesforce binding"):
        case.adapter.execute(target, ops.ORG_DESCRIBE, ops.DescribeOrgParams())
    assert case.calls == []


def test_wrong_authenticated_org_is_refused_before_data_query() -> None:
    case = Harness()
    case.org_id = "00D" + uuid4().hex[:12]
    with pytest.raises(AdapterError, match="authenticated organisation"):
        case.execute(ops.DATA_QUERY)
    assert len(case.calls) == 2


def test_oauth_cannot_retarget_the_instance() -> None:
    case = Harness()
    case.instance_url = "https://elsewhere.example"
    with pytest.raises(AdapterError, match="instance binding"):
        case.execute()
    assert len(case.calls) == 1


@pytest.mark.parametrize(
    "failure", ["timeout", "redirect", "http_error", "invalid_json", "oversize"]
)
def test_remote_failures_are_bounded_sanitized_and_not_retried(failure: str) -> None:
    case = Harness()
    case.failure = failure
    with pytest.raises(AdapterError) as caught:
        case.execute()
    assert case.oauth_secret not in str(caught.value)
    assert case.oauth_secret not in repr(caught.value)
    assert len(case.calls) == 1


@pytest.mark.parametrize("field", ["sobject", "fields", "limit"])
def test_adapter_revalidates_constructed_query_parameters(field: str) -> None:
    case = Harness()
    values = {"sobject": "Account", "fields": ["Name"], "limit": 5}
    values[field] = {
        "sobject": "Account WHERE Name != null",
        "fields": ["Name FROM User"],
        "limit": 201,
    }[field]
    params = ops.QueryDataParams.model_construct(**values)
    with pytest.raises(AdapterError, match="Invalid Salesforce read parameters"):
        case.adapter.execute(case.target, ops.DATA_QUERY, params)
    assert case.calls == []


def test_partial_query_does_not_follow_remote_urls_or_report_success() -> None:
    case = Harness()
    case.done = False
    with pytest.raises(AdapterError, match="incomplete or invalid query"):
        case.execute(ops.DATA_QUERY)
    assert len(case.calls) == 3


def test_raw_credentials_are_removed_even_without_a_known_secret_pattern() -> None:
    case = Harness()
    values = [
        case.connection.client_id.get_secret_value(),
        case.connection.client_secret.get_secret_value(),
        case.oauth_secret,
    ]
    case.rows = [{"Name": " ".join(values), "Owner": {"Name": case.oauth_secret, "attributes": {}}}]
    output = json.dumps(case.execute(ops.DATA_QUERY))
    assert all(value not in output for value in values)
    assert "[REDACTED]" in output
    assert "attributes" not in output


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.my.salesforce.com",
        "https://example.my.salesforce.com.evil.example",
        "https://user@example.my.salesforce.com",
        "https://example.my.salesforce.com/path",
        "https://example.my.salesforce.com?q=value",
        "https://example.my.salesforce.com#fragment",
        "https://example.my.salesforce.com:8443",
        "https://example.my.salesforce.com\n",
    ],
)
def test_invalid_origins_are_refused(origin: str) -> None:
    case = Harness()
    values = case.connection.model_dump()
    values["origin"] = origin
    with pytest.raises(ValidationError):
        SalesforceConnection.model_validate(values)


@pytest.mark.parametrize("field", ["environment_id", "name", "org_id"])
def test_duplicate_bindings_are_refused(field: str) -> None:
    first = Harness().connection
    other = Harness().connection
    other = other.model_copy(update={field: getattr(first, field)})
    with pytest.raises(ValidationError, match="must be unique"):
        SalesforceSettings(connections=[first, other])


def test_configuration_repr_and_validation_do_not_reveal_credentials() -> None:
    case = Harness()
    secret = case.connection.client_secret.get_secret_value()
    assert secret not in repr(case.connection)
    values = case.connection.model_dump()
    values["origin"] = "http://invalid.example"
    with pytest.raises(ValidationError) as caught:
        SalesforceConnection.model_validate(values)
    assert secret not in str(caught.value)


def test_default_registry_remains_mock_only_and_configured_adapter_is_opt_in() -> None:
    import os
    from unittest.mock import patch

    from control_tower.platforms.registry import build_adapters

    with patch.dict(os.environ, {"ACT_SALESFORCE_CONNECTIONS": "[]"}):
        assert build_adapters().keys() == {"mock-salesforce"}
    case = Harness()
    with patch.dict(os.environ, {"ACT_SALESFORCE_CONNECTIONS": case.config_json()}):
        registry = build_adapters()
        assert registry.keys() == {"mock-salesforce", "salesforce-readonly"}
    assert case.calls == []


def test_cli_missing_binding_does_not_open_a_database_session() -> None:
    import os
    from contextlib import redirect_stderr
    from io import StringIO
    from unittest.mock import patch

    from control_tower.cli import main

    with (
        patch.dict(os.environ, {"ACT_SALESFORCE_CONNECTIONS": "[]"}),
        patch("control_tower.persistence.session.get_sessionmaker") as sessions,
        redirect_stderr(StringIO()),
    ):
        assert main(["register-salesforce", str(uuid4())]) == 2
    sessions.assert_not_called()
