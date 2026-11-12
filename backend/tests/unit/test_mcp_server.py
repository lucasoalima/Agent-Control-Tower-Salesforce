"""Protocol and credential boundaries of the agent-only MCP bridge."""

import asyncio
import json
from typing import Any
from uuid import uuid4

import httpx2 as httpx
import pytest
from mcp import Client, MCPError
from mcp.types import CallToolRequestParams
from pydantic import SecretStr, ValidationError

from control_tower.mcp_server import STATUS_TOOL, MCPBridge, MCPSettings, create_server
from control_tower.security.tokens import new_agent_token

OPERATION = {
    "key": "salesforce.org.describe",
    "platform": "salesforce",
    "title": "Describe org",
    "description": "Read mock organisation information.",
    "risk": "READ",
    "parameters_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}


def response(status: str = "COMPLETED", **changes: Any) -> dict[str, Any]:
    return {
        "execution_id": str(uuid4()),
        "correlation_id": "mcp-test",
        "operation": OPERATION["key"],
        "status": status,
        "policy_effect": "REQUIRE_APPROVAL" if status == "PENDING_APPROVAL" else "ALLOW",
        "decision_reason": "RULE_MATCH",
        "decision_detail": "Matched policy.",
        "action_hash": None,
        "result": None,
        "error": None,
        "approval_expires_at": None,
        "reconciliation_required_at": None,
        **changes,
    }


@pytest.mark.parametrize("status", ["COMPLETED", "PENDING_APPROVAL"])
def test_discovery_and_submission_use_gateway_contract_only(status: str) -> None:
    async def scenario() -> None:
        requests = []
        credential = new_agent_token()

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            assert request.headers["Authorization"] == "Bearer " + credential
            assert "cookie" not in request.headers
            if request.url.path.endswith("/operations"):
                return httpx.Response(200, json=[OPERATION])
            assert request.url.path == "/api/v1/gateway/tool-requests"
            assert json.loads(request.content) == {"operation": OPERATION["key"], "parameters": {}}
            return httpx.Response(200, json=response(status))

        async with (
            httpx.AsyncClient(
                base_url="http://gateway.test",
                headers={"Authorization": "Bearer " + credential},
                transport=httpx.MockTransport(handle),
            ) as http,
            Client(create_server(http)) as client,
        ):
            tools = (await client.list_tools()).tools
            assert {tool.name for tool in tools} == {OPERATION["key"], STATUS_TOOL}
            assert tools[0].input_schema == OPERATION["parameters_schema"]
            result = await client.call_tool(OPERATION["key"], {})
            assert not result.is_error
            assert result.structured_content["status"] == status
        assert [request.method for request in requests].count("POST") == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status", "is_error"),
    [
        ("PENDING_APPROVAL", False),
        ("APPROVED", False),
        ("EXECUTING", False),
        ("COMPLETED", False),
        ("DENIED", True),
        ("FAILED", True),
        ("REJECTED", True),
        ("EXPIRED", True),
    ],
)
def test_status_is_preserved_without_claiming_execution(status: str, is_error: bool) -> None:
    async def scenario() -> None:
        execution = response(status)

        def handle(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            if request.url.path.endswith("/operations"):
                return httpx.Response(200, json=[])
            assert request.url.path.endswith("/" + execution["execution_id"])
            return httpx.Response(200, json=execution)

        async with (
            httpx.AsyncClient(
                base_url="http://gateway.test", transport=httpx.MockTransport(handle)
            ) as http,
            Client(create_server(http)) as client,
        ):
            result = await client.call_tool(
                STATUS_TOOL, {"execution_id": execution["execution_id"]}
            )
            assert result.is_error is is_error
            assert result.structured_content["status"] == status

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["timeout", "redirect", "invalid", "oversize"])
def test_uncertain_submission_is_never_retried_or_echoed(failure: str) -> None:
    async def scenario() -> None:
        writes = 0
        credential = new_agent_token()

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal writes
            if request.method == "GET":
                return httpx.Response(200, json=[OPERATION])
            writes += 1
            if failure == "timeout":
                raise httpx.ReadTimeout(credential, request=request)
            if failure == "redirect":
                return httpx.Response(307, headers={"Location": "https://elsewhere.test"})
            if failure == "invalid":
                return httpx.Response(200, text=credential)
            return httpx.Response(200, content=b"x" * 1_048_577)

        async with (
            httpx.AsyncClient(
                base_url="http://gateway.test", transport=httpx.MockTransport(handle)
            ) as http,
            Client(create_server(http)) as client,
        ):
            result = await client.call_tool(OPERATION["key"], {})
            assert result.is_error
            assert "do not resubmit" in result.content[0].text
            assert credential not in result.model_dump_json()
        assert writes == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "arguments",
    [{}, {"execution_id": "../operators"}, {"execution_id": str(uuid4()), "approve": True}],
)
def test_invalid_poll_parameters_never_reach_http(arguments: dict[str, Any]) -> None:
    async def scenario() -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/operations"):
                return httpx.Response(200, json=[])
            pytest.fail("Invalid status request reached the gateway.")

        async with (
            httpx.AsyncClient(
                base_url="http://gateway.test", transport=httpx.MockTransport(handle)
            ) as http,
            Client(create_server(http)) as client,
        ):
            direct = await MCPBridge(http).call_tool(
                None, CallToolRequestParams(name=STATUS_TOOL, arguments=arguments)
            )
            assert direct.is_error
            assert (await client.call_tool(STATUS_TOOL, arguments)).is_error

    asyncio.run(scenario())


def test_revoked_discovery_prevents_submission() -> None:
    async def scenario() -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            return httpx.Response(200, json=[])

        async with (
            httpx.AsyncClient(
                base_url="http://gateway.test", transport=httpx.MockTransport(handle)
            ) as http,
            Client(create_server(http)) as client,
        ):
            direct = await MCPBridge(http).call_tool(
                None, CallToolRequestParams(name=OPERATION["key"], arguments={})
            )
            assert direct.is_error
            assert (await client.call_tool(OPERATION["key"], {})).is_error

    asyncio.run(scenario())


def test_results_redact_credentials() -> None:
    async def scenario() -> None:
        credential = new_agent_token()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/operations"):
                return httpx.Response(200, json=[])
            return httpx.Response(
                200,
                json=response(
                    execution_id=request.url.path.rsplit("/", 1)[-1], result={"value": credential}
                ),
            )

        async with (
            httpx.AsyncClient(
                base_url="http://gateway.test", transport=httpx.MockTransport(handle)
            ) as http,
            Client(create_server(http)) as client,
        ):
            result = await client.call_tool(STATUS_TOOL, {"execution_id": str(uuid4())})
            assert credential not in result.model_dump_json()
            assert "[REDACTED]" in result.model_dump_json()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "origin",
    [
        "http://remote.test",
        f"https://{uuid4().hex}:{uuid4().hex}@host.test",
        "https://host.test/path",
        "https://host.test?value=1",
        "https://host.test#fragment",
        "https://host.test\n",
    ],
)
def test_unsafe_gateway_origins_are_refused(origin: str) -> None:
    with pytest.raises(ValidationError):
        MCPSettings(gateway_url=origin, agent_token=SecretStr(new_agent_token()))


def test_settings_hide_credential_and_accept_loopback_or_https() -> None:
    credential = new_agent_token()
    for origin in ("http://127.0.0.1:8000", "http://[::1]:8000", "https://tower.test/"):
        settings = MCPSettings(gateway_url=origin, agent_token=SecretStr(credential))
        assert credential not in repr(settings)
    with pytest.raises(ValidationError):
        MCPSettings(agent_token=SecretStr("invalid"))


def test_catalogue_failure_does_not_expose_exception_credentials() -> None:
    async def scenario() -> None:
        from contextlib import redirect_stderr
        from io import StringIO

        credential = new_agent_token()

        def handle(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout(credential, request=request)

        captured = StringIO()
        with redirect_stderr(captured):
            async with (
                httpx.AsyncClient(
                    base_url="http://gateway.test", transport=httpx.MockTransport(handle)
                ) as http,
                Client(create_server(http)) as client,
            ):
                with pytest.raises(MCPError):
                    await client.list_tools()
        assert credential not in captured.getvalue()

    asyncio.run(scenario())


def test_stdio_client_reaches_only_the_mock_gateway() -> None:
    import os
    import sys
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    from threading import Thread

    from mcp.client.stdio import StdioServerParameters

    credential = new_agent_token()
    execution = response("PENDING_APPROVAL")
    writes = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def reply(self, payload: Any) -> None:
            if self.headers.get("Authorization") != "Bearer " + credential:
                self.send_error(401)
                return
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self.reply([OPERATION] if self.path.endswith("/operations") else execution)

        def do_POST(self) -> None:
            writes.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.reply(execution)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def scenario() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "control_tower.mcp_server"],
            env={
                "ACT_MCP_AGENT_TOKEN": credential,
                "ACT_MCP_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}",
                "PYTHONPATH": str(Path("src").resolve())
                + os.pathsep
                + os.environ.get("PYTHONPATH", ""),
            },
        )
        async with Client(parameters, read_timeout_seconds=15) as client:
            assert OPERATION["key"] in {tool.name for tool in (await client.list_tools()).tools}
            result = await client.call_tool(OPERATION["key"], {})
            assert result.structured_content["status"] == "PENDING_APPROVAL"
            polled = await client.call_tool(
                STATUS_TOOL, {"execution_id": execution["execution_id"]}
            )
            assert polled.structured_content["execution_id"] == execution["execution_id"]
            assert credential not in result.model_dump_json()

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert writes == [{"operation": OPERATION["key"], "parameters": {}}]


def test_invalid_configuration_has_no_protocol_output() -> None:
    import os
    from contextlib import redirect_stderr, redirect_stdout
    from io import StringIO
    from unittest.mock import patch

    from control_tower.mcp_server import main

    stdout, stderr = StringIO(), StringIO()
    with (
        patch.dict(os.environ, {"ACT_MCP_AGENT_TOKEN": "invalid"}, clear=True),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        assert main() == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "MCP configuration invalid. Check ACT_MCP_ settings.\n"
