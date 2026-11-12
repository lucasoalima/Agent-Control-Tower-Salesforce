# Agent-only MCP server

| 🧭 Navegação rápida | Detail | Files |
| --- | --- | --- |
| Setup | [Configuration](#configuration) | backend/pyproject.toml |
| Agent tools | [Protocol](#protocol) | backend/src/control_tower/mcp_server.py |
| Verification | [Tests](#tests) | backend/tests/unit/test_mcp_server.py |
| Change evidence | [Decision](#decision) | README.md, docs/architecture.md, docs/security-model.md |

Related notes: [Architecture](architecture.md), [Security model](security-model.md),
[Development guide](development-guide.md), [Decisions](decisions.md).

## Configuration

Install the backend with `uv sync --frozen`. Run `uv run act-mcp` from backend,
or configure your MCP client to launch that command in the backend directory.
The process speaks stdio MCP using the official Python SDK. stdout is reserved
for protocol messages; configuration errors use a static message on stderr.

Provide these variables through the MCP client's secure process environment:

| Variable | Purpose |
| --- | --- |
| ACT_MCP_AGENT_TOKEN | Required agent token, supplied at runtime |
| ACT_MCP_GATEWAY_URL | Gateway origin; defaults to http://127.0.0.1:8000 |
| ACT_MCP_TIMEOUT_SECONDS | Request timeout, 1–300 seconds; default 30 |

No dotenv file is read by this process. Do not commit credentials in MCP client
configuration, pass them as command arguments, or give this process an operator
session. The HTTP API must already be running. The gateway selects the agent's
environment; MCP arguments cannot select an identity or target.

## Protocol

Operation tools keep the names and exact JSON schemas returned by
GET /api/v1/gateway/operations. The bridge refreshes discovery on an operation
call; submission also performs the gateway's own fresh authorization checks.

Each operation call sends one POST /api/v1/gateway/tool-requests. Responses
preserve execution_id, status, policy decision, approval deadline, result and
reconciliation marker. PENDING_APPROVAL means a human decision is needed;
APPROVED means approval is unused. Neither means the action has executed.
Operators review and dispatch approved actions in the dashboard.

The extra tool `tower.request_status` accepts only an execution_id UUID and
calls GET /api/v1/gateway/tool-requests/{execution_id}. The gateway restricts this
to the same agent. This tool cannot approve, reject, change policy or dispatch.

DENIED, FAILED, REJECTED and EXPIRED are tool errors with the stored response.
Other states retain their exact status. If a POST response is unavailable or
invalid, the bridge reports an unknown outcome and a correlation ID. Do not
repeat the operation: ask an operator to find and reconcile the execution.
There is no automatic retry, idempotency key or exactly-once guarantee.

## Tests

Protocol tests use the SDK's in-memory client and an HTTP mock transport. They
cover discovery schemas, agent-only request envelopes, truthful pending states,
polling validation, revoked tools, redaction and single-attempt uncertain writes.
A subprocess test also exercises stdio against a local mock HTTP gateway. These
are automated protocol tests, not a live Salesforce or desktop-client test.

Run the complete checks described in the development guide. All platform
operations continue to use the mock adapter. No Salesforce metadata is changed.

## Decision

Lucas, 12 November 2026: "Pode prosseguir entao, depois faco push".

The MCP process is a client of the existing gateway, rather than a second policy
engine. It needs an agent credential only. The SDK implements MCP framing and
stdio transport; existing HTTP contracts preserve approval and audit behaviour.

Original, backend/pyproject.toml, project.scripts, line 21:

```toml
act = "control_tower.cli:main" # Existing administrative and simulation CLI only.
```

Updated, project.scripts, lines 23–24:

```toml
act = "control_tower.cli:main" # Retain the existing CLI.
act-mcp = "control_tower.mcp_server:main" # Give stdio a separate protocol-only entry.
```

The new module and its tests did not exist before this change. The core,
gateway policy rules, approval endpoints and database schema are unchanged.
