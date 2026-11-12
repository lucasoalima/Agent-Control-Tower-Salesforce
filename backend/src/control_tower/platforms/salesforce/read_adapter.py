"""Opt-in Salesforce REST reads with server-held OAuth client credentials."""

import json
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from control_tower.core.enums import EnvironmentKind
from control_tower.core.operations import OperationParams
from control_tower.core.ports import AdapterError, TargetEnvironment
from control_tower.core.redaction import REDACTED, redact
from control_tower.platforms.salesforce import operations as ops

ORG_QUERY = "SELECT Id, Name, InstanceName, IsSandbox, OrganizationType FROM Organization LIMIT 1"
MAX_RESPONSE_BYTES = 1_048_576
ORG_ID = r"00D[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?"


class SalesforceConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    environment_id: UUID
    name: str = Field(min_length=1, max_length=120)
    kind: EnvironmentKind
    org_id: str = Field(pattern="^" + ORG_ID + "$")
    origin: str
    api_version: str = Field(pattern=r"^[1-9][0-9]{1,2}\.0$")
    client_id: SecretStr = Field(min_length=1, max_length=4096)
    client_secret: SecretStr = Field(min_length=1, max_length=4096)

    @field_validator("origin")
    @classmethod
    def salesforce_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or any(char.isspace() or ord(char) < 32 for char in value)
            or not hostname.endswith(".my.salesforce.com")
            or not re.fullmatch(r"[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?", hostname)
            or ".." in hostname
        ):
            raise ValueError("Use an HTTPS Salesforce My Domain origin.")
        return "https://" + hostname


class SalesforceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ACT_SALESFORCE_", env_file=None, extra="ignore", hide_input_in_errors=True
    )

    connections: list[SalesforceConnection] = Field(default_factory=list)
    timeout_seconds: float = Field(default=15, ge=1, le=60)

    @field_validator("connections")
    @classmethod
    def unique_bindings(cls, value: list[SalesforceConnection]) -> list[SalesforceConnection]:
        for keys in (
            [item.environment_id for item in value],
            [item.name for item in value],
            [item.org_id[:15] for item in value],
        ):
            if len(set(keys)) != len(keys):
                raise ValueError("Salesforce environment bindings must be unique.")
        return value


class OAuthResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    access_token: SecretStr = Field(min_length=1, max_length=4096)
    instance_url: str
    token_type: str


class Organization(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)

    org_id: str = Field(alias="Id", pattern="^" + ORG_ID + "$")
    name: str = Field(alias="Name", min_length=1, max_length=255)
    instance: str = Field(alias="InstanceName", min_length=1, max_length=80)
    is_sandbox: bool = Field(alias="IsSandbox")
    organization_type: str = Field(alias="OrganizationType", min_length=1, max_length=80)


class SalesforceReadAdapter:
    adapter_key = "salesforce-readonly"
    platform = ops.PLATFORM

    def __init__(
        self,
        connections: list[SalesforceConnection],
        *,
        timeout_seconds: float = 15,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        settings = SalesforceSettings(connections=connections, timeout_seconds=timeout_seconds)
        self._connections = {item.environment_id: item for item in settings.connections}
        self._timeout = settings.timeout_seconds
        self._transport = transport

    def supports(self, operation_key: str) -> bool:
        return operation_key in (ops.ORG_DESCRIBE, ops.DATA_QUERY)

    def execute(
        self, target: TargetEnvironment, operation_key: str, params: OperationParams
    ) -> dict[str, Any]:
        if not self.supports(operation_key):
            raise AdapterError(
                "UNSUPPORTED_OPERATION", "This adapter only supports org and data reads."
            )
        connection = self._connections.get(target.id)
        if (
            connection is None
            or target.platform != self.platform
            or target.kind is not connection.kind
            or re.fullmatch(ORG_ID, target.external_id) is None
            or target.external_id[:15] != connection.org_id[:15]
        ):
            raise AdapterError("TARGET_MISMATCH", "The target has no matching Salesforce binding.")
        try:
            validated: OperationParams
            if operation_key == ops.DATA_QUERY:
                validated = ops.QueryDataParams.model_validate(params.model_dump())
            else:
                validated = ops.DescribeOrgParams.model_validate(params.model_dump())
        except ValidationError:
            raise AdapterError(
                "INVALID_PARAMETERS", "Invalid Salesforce read parameters."
            ) from None
        with httpx.Client(
            base_url=connection.origin,
            timeout=self._timeout,
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
        ) as client:
            payload = self._request(
                client,
                "POST",
                "/services/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": connection.client_id.get_secret_value(),
                    "client_secret": connection.client_secret.get_secret_value(),
                },
            )
            try:
                oauth = OAuthResponse.model_validate(payload)
            except ValidationError:
                raise AdapterError("SALESFORCE_AUTH_FAILED", "Invalid OAuth response.") from None
            if (
                oauth.instance_url.rstrip("/") != connection.origin
                or oauth.token_type.lower() != "bearer"
            ):
                raise AdapterError("SALESFORCE_AUTH_FAILED", "OAuth instance binding was refused.")
            headers = {"Authorization": "Bearer " + oauth.access_token.get_secret_value()}
            path = "/services/data/v" + connection.api_version + "/query"
            records = self._records(
                self._request(client, "GET", path, params={"q": ORG_QUERY}, headers=headers), 1
            )
            try:
                if len(records) != 1:
                    raise ValueError("Missing organisation.")
                org = Organization.model_validate(records[0])
            except (ValueError, ValidationError):
                raise AdapterError("INVALID_RESPONSE", "Invalid organisation response.") from None
            if org.org_id[:15] != connection.org_id[:15]:
                raise AdapterError(
                    "TARGET_MISMATCH", "The authenticated organisation does not match."
                )
            if isinstance(validated, ops.QueryDataParams):
                query = (
                    "SELECT "  # noqa: S608
                    + ", ".join(validated.fields)
                    + " FROM "
                    + validated.sobject
                    + " LIMIT "
                    + str(validated.limit)
                )
                rows = self._records(
                    self._request(client, "GET", path, params={"q": query}, headers=headers),
                    validated.limit,
                )
                result = {"sobject": validated.sobject, "total_size": len(rows), "records": rows}
            else:
                result = {
                    "org_id": org.org_id,
                    "name": target.name,
                    "environment_kind": target.kind.value,
                    "is_mock": False,
                    "org_name": org.name,
                    "instance": org.instance,
                    "is_sandbox": org.is_sandbox,
                    "organization_type": org.organization_type,
                }
            safe, _ = redact(result)
            cleaned = self._clean(
                safe,
                (
                    connection.client_id.get_secret_value(),
                    connection.client_secret.get_secret_value(),
                    oauth.access_token.get_secret_value(),
                ),
            )
            assert isinstance(cleaned, dict)
            return cleaned

    @staticmethod
    def _request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            with client.stream(method, path, **kwargs) as response:
                if response.status_code != 200:
                    raise AdapterError(
                        "SALESFORCE_REQUEST_FAILED", "Salesforce refused the request."
                    )
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise AdapterError(
                            "RESPONSE_TOO_LARGE", "Salesforce response exceeded the limit."
                        )
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("Expected an object.")
                return payload
        except httpx.HTTPError:
            raise AdapterError(
                "SALESFORCE_UNAVAILABLE", "Salesforce request could not be confirmed."
            ) from None
        except (ValueError, RecursionError):
            raise AdapterError(
                "INVALID_RESPONSE", "Salesforce returned an invalid response."
            ) from None

    @staticmethod
    def _records(payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        records = payload.get("records")
        if (
            payload.get("done") is not True
            or not isinstance(records, list)
            or len(records) > limit
            or not all(isinstance(row, dict) for row in records)
            or type(payload.get("totalSize")) is not int
            or payload["totalSize"] != len(records)
        ):
            raise AdapterError(
                "INVALID_RESPONSE", "Salesforce returned an incomplete or invalid query."
            )
        return records

    @staticmethod
    def _clean(value: Any, credentials: tuple[str, ...]) -> Any:
        if isinstance(value, str):
            for credential in sorted(credentials, key=len, reverse=True):
                value = value.replace(credential, REDACTED)
            return value
        if isinstance(value, dict):
            return {
                SalesforceReadAdapter._clean(key, credentials): SalesforceReadAdapter._clean(
                    item, credentials
                )
                for key, item in value.items()
                if key != "attributes"
            }
        if isinstance(value, list):
            return [SalesforceReadAdapter._clean(item, credentials) for item in value]
        return value
