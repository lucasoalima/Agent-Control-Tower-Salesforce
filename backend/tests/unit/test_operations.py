import pytest
from pydantic import ValidationError

from control_tower.bootstrap import DEFAULT_POLICIES
from control_tower.core.enums import RiskClass
from control_tower.core.operations import OperationCatalog
from control_tower.platforms.registry import build_catalog
from control_tower.platforms.salesforce import operations as sf
from control_tower.platforms.salesforce.mock_adapter import MockSalesforceAdapter


def test_catalog_rejects_duplicate_keys() -> None:
    catalog = OperationCatalog()
    sf.register_salesforce_operations(catalog)
    with pytest.raises(ValueError, match="already registered"):
        sf.register_salesforce_operations(catalog)


def test_every_operation_is_implemented_by_the_mock_adapter() -> None:
    adapter = MockSalesforceAdapter()
    for spec in build_catalog():
        assert adapter.supports(spec.key), spec.key


def test_every_operation_has_a_default_policy_and_vice_versa() -> None:
    assert set(DEFAULT_POLICIES) == build_catalog().keys()


def test_only_reads_are_classified_as_read() -> None:
    risks = {spec.key: spec.risk for spec in build_catalog()}
    assert risks[sf.DEPLOY] is RiskClass.DEPLOY
    assert risks[sf.APEX_WRITE] is RiskClass.WRITE
    assert risks[sf.PERMISSION_SET_MODIFY] is RiskClass.WRITE
    assert risks[sf.TESTS_RUN] is RiskClass.EXECUTE
    assert {k for k, r in risks.items() if r is RiskClass.READ} == {
        sf.ORG_DESCRIBE,
        sf.METADATA_LIST,
        sf.METADATA_READ,
        sf.DATA_QUERY,
    }


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        sf.ReadMetadataParams.model_validate(
            {"metadata_type": "ApexClass", "full_name": "A", "target_org": "prod"}
        )


def test_types_are_not_coerced() -> None:
    with pytest.raises(ValidationError):
        sf.QueryDataParams.model_validate({"sobject": "Account", "fields": ["Id"], "limit": "10"})
    with pytest.raises(ValidationError):
        sf.DeployParams.model_validate(
            {
                "components": [{"metadata_type": "ApexClass", "full_name": "A", "body": "x"}],
                "check_only": "false",
            }
        )


@pytest.mark.parametrize(
    "name", ["../etc/passwd", "A; DROP", "1Leading", "", "has space", "a" * 81, "x'--"]
)
def test_api_names_are_strictly_validated(name: str) -> None:
    with pytest.raises(ValidationError):
        sf.ReadMetadataParams.model_validate({"metadata_type": "ApexClass", "full_name": name})


def test_unknown_metadata_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        sf.ListMetadataParams.model_validate({"metadata_type": "Profile"})


@pytest.mark.parametrize("limit", [0, 201, -1])
def test_query_limit_is_bounded(limit: int) -> None:
    with pytest.raises(ValidationError):
        sf.QueryDataParams.model_validate({"sobject": "Account", "fields": ["Id"], "limit": limit})


@pytest.mark.parametrize(
    "field", ["Id, Name", "(SELECT Id FROM Contacts)", "COUNT()", "a.b.c.d.e.f"]
)
def test_query_fields_cannot_smuggle_soql(field: str) -> None:
    with pytest.raises(ValidationError):
        sf.QueryDataParams.model_validate({"sobject": "Account", "fields": [field]})


def test_relationship_field_paths_are_accepted() -> None:
    params = sf.QueryDataParams.model_validate(
        {"sobject": "Contact", "fields": ["Account.Owner.Name"]}
    )
    assert params.limit == 50


def test_run_all_tests_in_org_is_not_offered() -> None:
    with pytest.raises(ValidationError):
        sf.DeployParams.model_validate(
            {
                "components": [{"metadata_type": "ApexClass", "full_name": "A", "body": "x"}],
                "test_level": "RunAllTestsInOrg",
            }
        )


def test_specified_tests_must_match_test_level() -> None:
    component = {"metadata_type": "ApexClass", "full_name": "A", "body": "x"}
    with pytest.raises(ValidationError, match="specified_tests is required"):
        sf.DeployParams.model_validate(
            {"components": [component], "test_level": "RunSpecifiedTests"}
        )
    with pytest.raises(ValidationError, match="only valid when"):
        sf.DeployParams.model_validate({"components": [component], "specified_tests": ["ATest"]})


def test_canonical_form_includes_defaults() -> None:
    params = sf.DeployParams.model_validate(
        {"components": [{"metadata_type": "ApexClass", "full_name": "A", "body": "x"}]}
    )
    assert params.canonical() == {
        "components": [{"metadata_type": "ApexClass", "full_name": "A", "body": "x"}],
        "test_level": "RunLocalTests",
        "specified_tests": [],
        "check_only": False,
    }


def test_validated_parameters_are_immutable() -> None:
    params = sf.WriteApexParams.model_validate({"class_name": "A", "body": "x"})
    with pytest.raises(ValidationError):
        params.body = "y"  # type: ignore[misc]
