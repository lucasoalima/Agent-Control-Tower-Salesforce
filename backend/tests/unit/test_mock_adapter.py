import uuid

import pytest

from control_tower.core.enums import EnvironmentKind
from control_tower.core.ports import AdapterError, PlatformAdapter, TargetEnvironment
from control_tower.platforms.salesforce import operations as sf
from control_tower.platforms.salesforce.mock_adapter import FAIL_MARKER, MockSalesforceAdapter

TARGET = TargetEnvironment(
    id=uuid.uuid4(),
    name="Dev",
    platform="salesforce",
    kind=EnvironmentKind.DEVELOPMENT,
    external_id="MOCK-DEV",
)


@pytest.fixture
def adapter() -> MockSalesforceAdapter:
    return MockSalesforceAdapter()


def component(name: str, body: str = "public class X {}") -> dict[str, str]:
    return {"metadata_type": "ApexClass", "full_name": name, "body": body}


def deploy(adapter: MockSalesforceAdapter, **params: object) -> dict[str, object]:
    return adapter.execute(TARGET, sf.DEPLOY, sf.DeployParams.model_validate(params))


def test_satisfies_the_adapter_protocol(adapter: MockSalesforceAdapter) -> None:
    assert isinstance(adapter, PlatformAdapter)


def test_describe_identifies_the_org_as_mock(adapter: MockSalesforceAdapter) -> None:
    result = adapter.execute(TARGET, sf.ORG_DESCRIBE, sf.DescribeOrgParams())
    assert result["org_id"] == "MOCK-DEV"
    assert result["is_mock"] is True


def test_list_and_read_metadata(adapter: MockSalesforceAdapter) -> None:
    listed = adapter.execute(
        TARGET, sf.METADATA_LIST, sf.ListMetadataParams(metadata_type="ApexClass")
    )
    assert listed["components"] == ["AccountService", "AccountServiceTest"]
    read = adapter.execute(
        TARGET,
        sf.METADATA_READ,
        sf.ReadMetadataParams(metadata_type="ApexClass", full_name="AccountService"),
    )
    assert "class AccountService" in read["body"]


def test_reading_a_missing_component_fails(adapter: MockSalesforceAdapter) -> None:
    with pytest.raises(AdapterError) as exc:
        adapter.execute(
            TARGET,
            sf.METADATA_READ,
            sf.ReadMetadataParams(metadata_type="ApexClass", full_name="Nope"),
        )
    assert exc.value.code == "NOT_FOUND"


def test_query_returns_only_requested_fields_up_to_limit(adapter: MockSalesforceAdapter) -> None:
    result = adapter.execute(
        TARGET, sf.DATA_QUERY, sf.QueryDataParams(sobject="Account", fields=["Name"], limit=2)
    )
    assert result["total_size"] == 2
    assert all(set(r) == {"Name"} for r in result["records"])


def test_query_rejects_unknown_object_and_field(adapter: MockSalesforceAdapter) -> None:
    with pytest.raises(AdapterError) as exc:
        adapter.execute(TARGET, sf.DATA_QUERY, sf.QueryDataParams(sobject="Lead", fields=["Id"]))
    assert exc.value.code == "INVALID_TYPE"
    with pytest.raises(AdapterError) as exc:
        adapter.execute(
            TARGET, sf.DATA_QUERY, sf.QueryDataParams(sobject="Account", fields=["Nope"])
        )
    assert exc.value.code == "INVALID_FIELD"


def test_write_apex_changes_the_org(adapter: MockSalesforceAdapter) -> None:
    result = adapter.execute(
        TARGET,
        sf.APEX_WRITE,
        sf.WriteApexParams(class_name="NewThing", body="public class NewThing {}"),
    )
    assert result == {"class_name": "NewThing", "created": True}
    assert adapter.org(TARGET).components[("ApexClass", "NewThing")] == "public class NewThing {}"


def test_successful_deployment_applies_components_and_is_recorded(
    adapter: MockSalesforceAdapter,
) -> None:
    result = deploy(adapter, components=[component("Billing")])
    org = adapter.org(TARGET)
    assert result["success"] is True
    assert ("ApexClass", "Billing") in org.components
    assert [d["deployment_id"] for d in org.deployments] == [result["deployment_id"]]
    assert result["tests"] == {
        "tests_run": 1,
        "passed": 1,
        "failed": 0,
        "outcomes": [{"class_name": "AccountServiceTest", "outcome": "Pass"}],
    }


def test_deployment_with_failing_tests_changes_nothing(adapter: MockSalesforceAdapter) -> None:
    before = dict(adapter.org(TARGET).components)
    with pytest.raises(AdapterError) as exc:
        deploy(
            adapter,
            components=[component("Billing"), component("BillingTest", f"// {FAIL_MARKER}")],
        )
    assert exc.value.code == "DEPLOY_FAILED"
    org = adapter.org(TARGET)
    assert org.components == before
    assert len(org.deployments) == 1 and org.deployments[0]["success"] is False


def test_check_only_deployment_changes_nothing(adapter: MockSalesforceAdapter) -> None:
    before = dict(adapter.org(TARGET).components)
    result = deploy(adapter, components=[component("Billing")], check_only=True)
    assert result["success"] is True
    assert adapter.org(TARGET).components == before


def test_no_test_run_skips_tests(adapter: MockSalesforceAdapter) -> None:
    result = deploy(
        adapter,
        components=[component("BrokenTest", f"// {FAIL_MARKER}")],
        test_level="NoTestRun",
    )
    assert result["tests"] is None and result["success"] is True


def test_run_tests_reports_per_class_outcomes(adapter: MockSalesforceAdapter) -> None:
    adapter.execute(
        TARGET, sf.APEX_WRITE, sf.WriteApexParams(class_name="FlakyTest", body=f"// {FAIL_MARKER}")
    )
    result = adapter.execute(TARGET, sf.TESTS_RUN, sf.RunTestsParams())
    assert (result["tests_run"], result["passed"], result["failed"]) == (2, 1, 1)
    assert len(adapter.org(TARGET).test_runs) == 1


def test_modify_permission_set_requires_existing_set(adapter: MockSalesforceAdapter) -> None:
    perms = [sf.ObjectPermission(sobject="Account", allow_read=True)]
    with pytest.raises(AdapterError):
        adapter.execute(
            TARGET,
            sf.PERMISSION_SET_MODIFY,
            sf.ModifyPermissionSetParams(permission_set="Missing", object_permissions=perms),
        )
    adapter.execute(
        TARGET,
        sf.PERMISSION_SET_MODIFY,
        sf.ModifyPermissionSetParams(permission_set="Sales_Operations", object_permissions=perms),
    )
    assert adapter.org(TARGET).object_permissions["Sales_Operations"]["Account"]["read"] is True


def test_orgs_are_isolated_per_environment(adapter: MockSalesforceAdapter) -> None:
    other = TargetEnvironment(
        uuid.uuid4(), "UAT", "salesforce", EnvironmentKind.TESTING, "MOCK-UAT"
    )
    adapter.execute(TARGET, sf.APEX_WRITE, sf.WriteApexParams(class_name="OnlyInDev", body="x"))
    assert ("ApexClass", "OnlyInDev") not in adapter.org(other).components
