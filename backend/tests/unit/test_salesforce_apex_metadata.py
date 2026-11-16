"""Bounded Tooling API reads; all remote traffic uses MockTransport."""

from typing import Any

import httpx2 as httpx
import pytest

from control_tower.core.ports import AdapterError
from control_tower.platforms.salesforce import operations as ops
from tests.unit.test_salesforce_read_adapter import Harness


class MetadataHarness(Harness):
    def __init__(self) -> None:
        super().__init__()
        self.components: list[dict[str, Any]] = [
            {"Name": "Sample", "NamespacePrefix": None, "Body": "// Synthetic Apex source"}
        ]
        self.tooling_done = True
        self.tooling_failure: str | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/services/data/v66.0/tooling/query":
            return super().handle(request)
        self.calls.append(request)
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer " + self.oauth_secret
        assert "cookie" not in request.headers
        if self.tooling_failure == "timeout":
            raise httpx.ReadTimeout(self.oauth_secret, request=request)
        if self.tooling_failure == "redirect":
            return httpx.Response(307, headers={"Location": "https://elsewhere.example"})
        if self.tooling_failure == "http_error":
            return httpx.Response(403, text=self.oauth_secret)
        if self.tooling_failure == "invalid_json":
            return httpx.Response(200, text=self.oauth_secret)
        if self.tooling_failure == "oversize":
            return httpx.Response(200, content=b"x" * 1_048_577)
        return httpx.Response(
            200,
            json={
                "done": self.tooling_done,
                "totalSize": len(self.components),
                "records": self.components,
                "nextRecordsUrl": "https://elsewhere.example/not-followed",
            },
        )

    def metadata(
        self, operation: str, metadata_type: str = "ApexClass", full_name: str = "Sample"
    ) -> dict[str, Any]:
        parameters: dict[str, Any] = {"metadata_type": metadata_type}
        if operation == ops.METADATA_READ:
            parameters["full_name"] = full_name
            model = ops.ReadMetadataParams
        else:
            model = ops.ListMetadataParams
        return self.adapter.execute(self.target, operation, model.model_validate(parameters))


@pytest.mark.parametrize("metadata_type", ["ApexClass", "ApexTrigger"])
def test_lists_only_names_through_a_fixed_bounded_query(metadata_type: str) -> None:
    case = MetadataHarness()
    case.components = [
        {"Name": "Zulu", "NamespacePrefix": None},
        {"Name": "Alpha", "NamespacePrefix": "pkg"},
    ]
    result = case.metadata(ops.METADATA_LIST, metadata_type)
    assert result == {"metadata_type": metadata_type, "components": ["Zulu", "pkg__Alpha"]}
    assert case.calls[-1].url.params["q"] == (
        "SELECT Name, NamespacePrefix FROM "  # noqa: S608
        + metadata_type
        + " ORDER BY Name, NamespacePrefix LIMIT 201"
    )
    assert len(case.calls) == 3


@pytest.mark.parametrize("metadata_type", ["ApexClass", "ApexTrigger"])
@pytest.mark.parametrize("namespace", [None, "pkg"])
def test_reads_exact_component_including_namespace(
    metadata_type: str, namespace: str | None
) -> None:
    case = MetadataHarness()
    case.components[0]["NamespacePrefix"] = namespace
    full_name = "pkg__Sample" if namespace else "Sample"
    result = case.metadata(ops.METADATA_READ, metadata_type, full_name)
    assert result == {
        "metadata_type": metadata_type,
        "full_name": full_name,
        "body": "// Synthetic Apex source",
    }
    expected_namespace = "'pkg'" if namespace else "null"
    assert case.calls[-1].url.params["q"] == (
        "SELECT Name, NamespacePrefix, Body FROM "  # noqa: S608
        + metadata_type
        + " WHERE Name = 'Sample' AND NamespacePrefix = "
        + expected_namespace
        + " LIMIT 2"
    )
    assert len(case.calls) == 3


@pytest.mark.parametrize(
    "metadata_type", ["CustomObject", "Flow", "LightningComponentBundle", "PermissionSet"]
)
@pytest.mark.parametrize("operation", [ops.METADATA_LIST, ops.METADATA_READ])
def test_other_metadata_types_are_refused_before_oauth(metadata_type: str, operation: str) -> None:
    case = MetadataHarness()
    with pytest.raises(AdapterError) as caught:
        case.metadata(operation, metadata_type)
    assert caught.value.code == "UNSUPPORTED_METADATA_TYPE"
    assert case.calls == []


@pytest.mark.parametrize("operation", [ops.METADATA_LIST, ops.METADATA_READ])
def test_constructed_metadata_parameters_are_revalidated(operation: str) -> None:
    case = MetadataHarness()
    if operation == ops.METADATA_READ:
        params = ops.ReadMetadataParams.model_construct(
            metadata_type="ApexClass", full_name="Sample' OR Name != null"
        )
    else:
        params = ops.ListMetadataParams.model_construct(
            metadata_type="ApexClass WHERE Name != null"
        )
    with pytest.raises(AdapterError) as caught:
        case.adapter.execute(case.target, operation, params)
    assert caught.value.code == "INVALID_PARAMETERS"
    assert case.calls == []


@pytest.mark.parametrize("operation", [ops.METADATA_LIST, ops.METADATA_READ])
def test_org_identity_is_checked_before_metadata_access(operation: str) -> None:
    case = MetadataHarness()
    case.org_id = case.org_id[:3] + case.connection.environment_id.hex[:12]
    with pytest.raises(AdapterError) as caught:
        case.metadata(operation)
    assert caught.value.code == "TARGET_MISMATCH"
    assert len(case.calls) == 2


@pytest.mark.parametrize(
    "failure", ["timeout", "redirect", "http_error", "invalid_json", "oversize"]
)
def test_tooling_failures_are_sanitized_without_retry(failure: str) -> None:
    case = MetadataHarness()
    case.tooling_failure = failure
    with pytest.raises(AdapterError) as caught:
        case.metadata(ops.METADATA_READ)
    assert case.oauth_secret not in str(caught.value)
    assert len(case.calls) == 3


@pytest.mark.parametrize("operation", [ops.METADATA_LIST, ops.METADATA_READ])
def test_partial_results_do_not_follow_query_locators(operation: str) -> None:
    case = MetadataHarness()
    case.tooling_done = False
    with pytest.raises(AdapterError) as caught:
        case.metadata(operation)
    assert caught.value.code == "INVALID_RESPONSE"
    assert len(case.calls) == 3


def test_lists_at_most_200_components_and_refuses_overflow() -> None:
    case = MetadataHarness()
    case.components = [
        {"Name": "Sample" + str(index), "NamespacePrefix": None} for index in range(200)
    ]
    assert len(case.metadata(ops.METADATA_LIST)["components"]) == 200
    case.components.append({"Name": "Overflow", "NamespacePrefix": None})
    with pytest.raises(AdapterError) as caught:
        case.metadata(ops.METADATA_LIST)
    assert caught.value.code == "METADATA_RESULT_LIMIT"


def test_empty_list_is_valid_but_missing_source_is_not_found() -> None:
    case = MetadataHarness()
    case.components = []
    assert case.metadata(ops.METADATA_LIST)["components"] == []
    with pytest.raises(AdapterError) as caught:
        case.metadata(ops.METADATA_READ)
    assert caught.value.code == "NOT_FOUND"


@pytest.mark.parametrize(
    "change",
    [
        {"Name": "Other"},
        {"NamespacePrefix": "other"},
        {"Name": "bad' Name"},
        {"NamespacePrefix": ""},
    ],
)
def test_wrong_or_invalid_returned_component_is_refused(change: dict[str, Any]) -> None:
    case = MetadataHarness()
    case.components[0].update(change)
    with pytest.raises(AdapterError) as caught:
        case.metadata(ops.METADATA_READ)
    assert caught.value.code == "INVALID_RESPONSE"


def test_duplicate_list_and_ambiguous_read_are_refused() -> None:
    case = MetadataHarness()
    case.components *= 2
    for operation in (ops.METADATA_LIST, ops.METADATA_READ):
        with pytest.raises(AdapterError) as caught:
            case.metadata(operation)
        assert caught.value.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("body", [None, "", 123])
def test_unavailable_source_is_reported_explicitly(body: Any) -> None:
    case = MetadataHarness()
    case.components[0]["Body"] = body
    with pytest.raises(AdapterError) as caught:
        case.metadata(ops.METADATA_READ)
    assert caught.value.code == "SOURCE_UNAVAILABLE"


def test_source_uses_the_existing_exact_credential_redaction() -> None:
    case = MetadataHarness()
    values = [
        case.oauth_secret,
        case.connection.client_id.get_secret_value(),
        case.connection.client_secret.get_secret_value(),
    ]
    case.components[0]["Body"] = "// " + " ".join(values)
    body = case.metadata(ops.METADATA_READ)["body"]
    assert body == "// [REDACTED] [REDACTED] [REDACTED]"
    assert all(value not in body for value in values)


@pytest.mark.parametrize(
    "record",
    [
        {"Name": "Sample"},
        {"NamespacePrefix": None},
        {"Name": True, "NamespacePrefix": None},
        {"Name": "Sample", "NamespacePrefix": 17},
    ],
)
def test_incomplete_or_invalid_names_are_not_listed(record: dict[str, Any]) -> None:
    case = MetadataHarness()
    case.components = [record]
    with pytest.raises(AdapterError) as caught:
        case.metadata(ops.METADATA_LIST)
    assert caught.value.code == "INVALID_RESPONSE"
