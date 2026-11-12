"""Agent-only stdio MCP bridge. All authority remains in the HTTP gateway."""

import asyncio
import json
import re
import sys
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx2 as httpx
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)
from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from control_tower.api.schemas import OperationOut, ToolResponse
from control_tower.core.enums import ExecutionStatus, RiskClass
from control_tower.core.redaction import redact
from control_tower.security.tokens import AGENT_TOKEN_PREFIX

STATUS_TOOL = "tower.request_status"
MAX_RESPONSE_BYTES = 1_048_576


class MCPSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ACT_MCP_", env_file=None, hide_input_in_errors=True
    )

    gateway_url: str = "http://127.0.0.1:8000"
    agent_token: SecretStr
    timeout_seconds: float = Field(default=30, ge=1, le=300)

    @field_validator("gateway_url")
    @classmethod
    def gateway_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            any(char.isspace() or ord(char) < 32 for char in value)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
            or not parsed.hostname
            or parsed.scheme not in ("http", "https")
            or (
                parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            )
        ):
            raise ValueError("Use an HTTPS origin or a loopback HTTP origin.")
        _ = parsed.port
        return value.rstrip("/")

    @field_validator("agent_token")
    @classmethod
    def agent_credential(cls, value: SecretStr) -> SecretStr:
        if not re.fullmatch(
            re.escape(AGENT_TOKEN_PREFIX) + r"[A-Za-z0-9_-]{43}", value.get_secret_value()
        ):
            raise ValueError("An agent credential is required.")
        return value


class GatewayFailure(Exception):
    def __init__(self, correlation_id: str, *, uncertain: bool = False) -> None:
        super().__init__("Gateway request could not be confirmed.")
        self.correlation_id = correlation_id
        self.uncertain = uncertain


class MCPBridge:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def request(
        self, method: str, path: str, payload: Any = None, *, correlation_id: str | None = None
    ) -> Any:
        correlation_id = correlation_id or "mcp-" + uuid4().hex
        try:
            async with self.client.stream(
                method,
                "/api/v1/gateway/" + path,
                json=payload,
                headers={"X-Correlation-ID": correlation_id},
            ) as response:
                if response.status_code not in (200, 202):
                    raise GatewayFailure(correlation_id, uncertain=method == "POST")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise GatewayFailure(correlation_id, uncertain=method == "POST")
                return json.loads(body)
        except (httpx.HTTPError, ValueError) as exc:
            raise GatewayFailure(correlation_id, uncertain=method == "POST") from exc

    async def operations(self) -> list[OperationOut]:
        payload = await self.request("GET", "operations")
        try:
            if not isinstance(payload, list):
                raise ValueError("Invalid catalogue.")
            operations = [OperationOut.model_validate(item) for item in payload]
            names = [operation.key for operation in operations]
            if (
                len(names) != len(set(names))
                or STATUS_TOOL in names
                or any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", name) for name in names)
            ):
                raise ValueError("Invalid tool names.")
            return operations
        except (ValidationError, ValueError) as exc:
            raise GatewayFailure("mcp-" + uuid4().hex) from exc

    async def list_tools(
        self, ctx: ServerRequestContext[None], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        try:
            operations = await self.operations()
        except GatewayFailure:
            raise ValueError(
                "Agent catalogue unavailable; check the gateway and agent access."
            ) from None
        tools = [
            Tool(
                name=operation.key,
                title=operation.title,
                description=operation.description,
                input_schema=operation.parameters_schema,
                annotations=ToolAnnotations(
                    read_only_hint=operation.risk is RiskClass.READ,
                    idempotent_hint=False,
                    open_world_hint=True,
                ),
            )
            for operation in operations
        ]
        tools.append(
            Tool(
                name=STATUS_TOOL,
                description="Read your own request status. This does not approve or execute it.",
                input_schema={
                    "type": "object",
                    "properties": {"execution_id": {"type": "string", "format": "uuid"}},
                    "required": ["execution_id"],
                    "additionalProperties": False,
                },
                annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
            )
        )
        safe, _ = redact(ListToolsResult(tools=tools).model_dump(mode="json", by_alias=True))
        return ListToolsResult.model_validate(safe)

    async def call_tool(
        self, ctx: ServerRequestContext[None], params: CallToolRequestParams
    ) -> CallToolResult:
        arguments = params.arguments or {}
        submitted = False
        correlation_id = "mcp-" + uuid4().hex
        try:
            if params.name == STATUS_TOOL:
                if set(arguments) != {"execution_id"} or not isinstance(
                    arguments["execution_id"], str
                ):
                    return self.error("Provide only an execution_id UUID.")
                execution_id = UUID(arguments["execution_id"])
                payload = await self.request(
                    "GET", "tool-requests/" + str(execution_id), correlation_id=correlation_id
                )
            else:
                if params.name not in {operation.key for operation in await self.operations()}:
                    return self.error("Tool unavailable for this agent.")
                submitted = True
                payload = await self.request(
                    "POST",
                    "tool-requests",
                    {"operation": params.name, "parameters": arguments},
                    correlation_id=correlation_id,
                )
            result = ToolResponse.model_validate(payload)
            if (params.name == STATUS_TOOL and result.execution_id != execution_id) or (
                params.name != STATUS_TOOL and result.operation != params.name
            ):
                raise ValueError("Unexpected gateway response binding.")
            safe, _ = redact(result.model_dump(mode="json"))
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(safe))],
                structured_content=safe,
                is_error=result.status
                in (
                    ExecutionStatus.DENIED,
                    ExecutionStatus.FAILED,
                    ExecutionStatus.REJECTED,
                    ExecutionStatus.EXPIRED,
                ),
            )
        except GatewayFailure as exc:
            message = "Gateway response unavailable."
            if exc.uncertain:
                message += " Outcome unknown; do not resubmit. Ask an operator to reconcile."
            return self.error(message, exc.correlation_id)
        except (ValidationError, ValueError):
            message = "Invalid request or gateway response."
            if submitted:
                message += " Outcome unknown; do not resubmit. Ask an operator to reconcile."
            return self.error(message, correlation_id)

    @staticmethod
    def error(message: str, correlation_id: str | None = None) -> CallToolResult:
        details = {"error": message}
        if correlation_id:
            details["correlation_id"] = correlation_id
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(details))],
            structured_content=details,
            is_error=True,
        )


def create_server(client: httpx.AsyncClient) -> Server[None]:
    bridge = MCPBridge(client)
    return Server(
        "Agent Control Tower",
        on_list_tools=bridge.list_tools,
        on_call_tool=bridge.call_tool,
        instructions=(
            "All actions use the configured agent and its bound environment. "
            "PENDING_APPROVAL and APPROVED do not mean executed. Poll tower.request_status. "
            "Never repeat a request with an unknown outcome; ask an operator to reconcile."
        ),
    )


async def serve(settings: MCPSettings) -> None:
    async with httpx.AsyncClient(
        base_url=settings.gateway_url,
        headers={"Authorization": "Bearer " + settings.agent_token.get_secret_value()},
        timeout=settings.timeout_seconds,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        server = create_server(client)
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> int:
    try:
        settings = MCPSettings()
    except ValueError:
        print("MCP configuration invalid. Check ACT_MCP_ settings.", file=sys.stderr)
        return 2
    with suppress(KeyboardInterrupt):
        asyncio.run(serve(settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
