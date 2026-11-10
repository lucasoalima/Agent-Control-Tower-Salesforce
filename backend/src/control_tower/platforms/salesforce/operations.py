"""Salesforce operations exposed through the gateway.

These are Agent Control Tower operation keys, not Salesforce API names. The
adapter decides how each one maps onto the Salesforce REST, Tooling or Metadata
API (or the Salesforce CLI); the mock adapter implements them in memory.
"""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from control_tower.core.enums import RiskClass
from control_tower.core.operations import OperationCatalog, OperationParams, OperationSpec

PLATFORM = "salesforce"

# Salesforce API names: start with a letter, then letters, digits and underscores.
ApiName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,79}$")]
# A field reference, optionally traversing parent relationships (Account.Owner.Name).
FieldPath = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,79}(\.[A-Za-z][A-Za-z0-9_]{0,79}){0,4}$"),
]
SourceBody = Annotated[str, StringConstraints(min_length=1, max_length=131_072)]

MetadataType = Literal[
    "ApexClass",
    "ApexTrigger",
    "CustomObject",
    "Flow",
    "LightningComponentBundle",
    "PermissionSet",
]
# Test levels as defined by the Salesforce Metadata API deploy options.
# RunAllTestsInOrg is intentionally not offered.
TestLevel = Literal["NoTestRun", "RunSpecifiedTests", "RunLocalTests"]


class DescribeOrgParams(OperationParams):
    pass


class ListMetadataParams(OperationParams):
    metadata_type: MetadataType


class ReadMetadataParams(OperationParams):
    metadata_type: MetadataType
    full_name: ApiName


class QueryDataParams(OperationParams):
    """A deliberately small, structured query. Free-form SOQL is not accepted."""

    sobject: ApiName
    fields: Annotated[list[FieldPath], Field(min_length=1, max_length=50)]
    limit: Annotated[int, Field(ge=1, le=200)] = 50


class WriteApexParams(OperationParams):
    class_name: ApiName
    body: SourceBody


class ObjectPermission(OperationParams):
    sobject: ApiName
    allow_read: bool = False
    allow_create: bool = False
    allow_edit: bool = False
    allow_delete: bool = False


class ModifyPermissionSetParams(OperationParams):
    permission_set: ApiName
    object_permissions: Annotated[list[ObjectPermission], Field(min_length=1, max_length=50)]


class DeployComponent(OperationParams):
    metadata_type: MetadataType
    full_name: ApiName
    body: SourceBody


class DeployParams(OperationParams):
    components: Annotated[list[DeployComponent], Field(min_length=1, max_length=200)]
    test_level: TestLevel = "RunLocalTests"
    specified_tests: Annotated[list[ApiName], Field(max_length=200)] = Field(default_factory=list)
    check_only: bool = False

    @model_validator(mode="after")
    def _specified_tests_match_level(self) -> "DeployParams":
        if self.test_level == "RunSpecifiedTests" and not self.specified_tests:
            raise ValueError("specified_tests is required when test_level is RunSpecifiedTests")
        if self.test_level != "RunSpecifiedTests" and self.specified_tests:
            raise ValueError("specified_tests is only valid when test_level is RunSpecifiedTests")
        return self


class RunTestsParams(OperationParams):
    """Run Apex tests. An empty ``class_names`` runs every local test class."""

    class_names: Annotated[list[ApiName], Field(max_length=200)] = Field(default_factory=list)


ORG_DESCRIBE = "salesforce.org.describe"
METADATA_LIST = "salesforce.metadata.list"
METADATA_READ = "salesforce.metadata.read"
DATA_QUERY = "salesforce.data.query"
APEX_WRITE = "salesforce.apex.write"
PERMISSION_SET_MODIFY = "salesforce.permission_set.modify"
DEPLOY = "salesforce.deploy"
TESTS_RUN = "salesforce.tests.run"

SALESFORCE_OPERATIONS: tuple[OperationSpec, ...] = (
    OperationSpec(
        ORG_DESCRIBE,
        PLATFORM,
        "Describe organisation",
        "Identify the target organisation: its id, type and instance.",
        RiskClass.READ,
        DescribeOrgParams,
    ),
    OperationSpec(
        METADATA_LIST,
        PLATFORM,
        "List metadata",
        "List the components of one metadata type.",
        RiskClass.READ,
        ListMetadataParams,
    ),
    OperationSpec(
        METADATA_READ,
        PLATFORM,
        "Read metadata",
        "Read the source of one metadata component.",
        RiskClass.READ,
        ReadMetadataParams,
    ),
    OperationSpec(
        DATA_QUERY,
        PLATFORM,
        "Query records",
        "Read up to 200 records of one object, selecting named fields.",
        RiskClass.READ,
        QueryDataParams,
    ),
    OperationSpec(
        APEX_WRITE,
        PLATFORM,
        "Write Apex class",
        "Create or replace one Apex class in the organisation.",
        RiskClass.WRITE,
        WriteApexParams,
    ),
    OperationSpec(
        PERMISSION_SET_MODIFY,
        PLATFORM,
        "Modify permission set",
        "Change the object permissions granted by a permission set.",
        RiskClass.WRITE,
        ModifyPermissionSetParams,
    ),
    OperationSpec(
        DEPLOY,
        PLATFORM,
        "Deploy metadata",
        "Deploy a set of metadata components as one deployment, optionally running tests.",
        RiskClass.DEPLOY,
        DeployParams,
    ),
    OperationSpec(
        TESTS_RUN,
        PLATFORM,
        "Run Apex tests",
        "Run Apex test classes and return per-class results.",
        RiskClass.EXECUTE,
        RunTestsParams,
    ),
)


def register_salesforce_operations(catalog: OperationCatalog) -> None:
    for spec in SALESFORCE_OPERATIONS:
        catalog.register(spec)
