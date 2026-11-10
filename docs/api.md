# API

Base path: `/api/v1`. Interactive documentation is served at `/api/v1/docs`
and the schema at `/api/v1/openapi.json`.

Errors always have this shape:

```json
{ "error": { "code": "validation_error", "message": "The request is not valid.",
             "details": [{ "field": "body.operation", "message": "Field required" }] } }
```

Every response carries `X-Correlation-ID`. A caller may supply one (8 to 64
characters of `A-Z a-z 0-9 . _ -`); anything else is replaced.

## Gateway API (agents)

Authentication: `Authorization: Bearer act_agt_…`

### `POST /gateway/tool-requests`

```json
{ "operation": "salesforce.metadata.read",
  "parameters": { "metadata_type": "ApexClass", "full_name": "AccountService" } }
```

No other fields are accepted. The target environment is the one the agent is
bound to.

| HTTP | `status`           | Meaning                                              |
| ---- | ------------------ | ---------------------------------------------------- |
| 200  | `COMPLETED`        | Ran. `result` holds what the target system returned. |
| 200  | `FAILED`           | Ran and failed. `error` has `code` and `message`.    |
| 200  | `DENIED`           | Did not run. `decision_reason` says why.             |
| 202  | `PENDING_APPROVAL` | Did not run. Waiting for a person.                   |
| 401  |                    | Missing or unknown token. Nothing is recorded.       |
| 413  |                    | Body larger than 1 MiB.                              |
| 422  |                    | The envelope itself is malformed.                    |

A denied request is `200`, not `403`: the caller was authenticated and the
request was processed and recorded. The outcome is in the body.

```json
{ "execution_id": "1924e7b1-…", "correlation_id": "071fde15…",
  "operation": "salesforce.deploy", "status": "PENDING_APPROVAL",
  "policy_effect": "REQUIRE_APPROVAL", "decision_reason": "RULE_MATCH",
  "decision_detail": "Policy for 'salesforce.deploy' on TESTING environments is REQUIRE_APPROVAL.",
  "action_hash": "796ee716…", "result": null, "error": null }
```

`decision_reason` is one of `RULE_MATCH`, `NO_MATCHING_RULE`,
`PRODUCTION_GUARDRAIL`, `OPERATION_NOT_GRANTED`, `AGENT_DISABLED`,
`UNKNOWN_OPERATION`, `UNSUPPORTED_BY_ADAPTER`, `INVALID_PARAMETERS`,
`CREDENTIAL_IN_PAYLOAD`.

### `GET /gateway/tool-requests/{execution_id}`

The same body, for one of the calling agent's own requests. Another agent's
request and a non-existent one both return an identical `404`.

## Operations

| Key | Parameters |
| --- | --- |
| `salesforce.org.describe` | none |
| `salesforce.metadata.list` | `metadata_type` |
| `salesforce.metadata.read` | `metadata_type`, `full_name` |
| `salesforce.data.query` | `sobject`, `fields` (1 to 50), `limit` (1 to 200, default 50) |
| `salesforce.tests.run` | `class_names` (empty runs all local tests) |
| `salesforce.apex.write` | `class_name`, `body` |
| `salesforce.permission_set.modify` | `permission_set`, `object_permissions[]` of `{sobject, allow_read, allow_create, allow_edit, allow_delete}` |
| `salesforce.deploy` | `components[]` of `{metadata_type, full_name, body}`, `test_level` (`NoTestRun`, `RunSpecifiedTests`, `RunLocalTests`), `specified_tests`, `check_only` |

`metadata_type` is one of `ApexClass`, `ApexTrigger`, `CustomObject`, `Flow`,
`LightningComponentBundle`, `PermissionSet`. Names must match
`^[A-Za-z][A-Za-z0-9_]{0,79}$`. Queries are structured; free-form SOQL is not
accepted. The full JSON Schema of each operation is returned by `GET /operations`.

These are Agent Control Tower operation names. How each maps onto Salesforce
APIs is the adapter's concern and will be documented with the real adapter.

## Management API (operators)

Authentication: session cookie from `POST /auth/login`.

| Method and path | Purpose |
| --- | --- |
| `POST /auth/login` | `{username, password}`; sets the `act_session` cookie. `429` after 5 failures in 5 minutes. |
| `POST /auth/logout` | Revokes the session. |
| `GET /auth/me` | The signed-in operator. |
| `GET /overview` | Counts: agents, executions, waiting, failed, blocked. |
| `GET /agents`, `GET /agents/{id}` | Registered agents. Never includes a token or its hash. |
| `POST /agents` | Register an agent. The response contains the token, once. |
| `PATCH /agents/{id}` | `{status: "ACTIVE" \| "DISABLED"}`. |
| `GET /environments` | Target environments. |
| `GET /operations` | Operation catalogue with parameter schemas. |
| `GET /policies` | Matrix of configured and effective policy per operation and environment kind. |
| `PUT /policies/{operation_key}/{environment_kind}` | Set an operation policy; operator session required. |
| `GET /executions` | Search. Query: `status` (repeatable), `agent_id`, `environment_id`, `operation`, `q`, `limit` (≤100), `offset`. |
| `GET /executions/{id}` | Full record including parameters, result and error. |

### Policy editing

PUT accepts `{"effect": "ALLOW"}` only. Valid effects are ALLOW, REQUIRE_APPROVAL
and DENY. HTTP 200 returns the configured and effective PolicyCell. A missing rule
is created. Each successful write records a `policy.updated` event with the
operator, correlation ID, operation, environment kind, previous effect (null
for a new rule) and new effect, in the same transaction.

Unknown operations, invalid environment kinds, invalid effects and extra fields
return 422. Non-read production cells are fixed: every write to them returns
403, including a request to set DENY. Cookie-authenticated writes use the same
Origin check as the other management endpoints. A conflicting concurrent insert
returns 409 without changing the rule or recording a successful policy write.

### Approval deadlines and unknown outcomes

Execution and tool-request responses include nullable `approval_expires_at`
and `reconciliation_required_at` timestamps. Pending requests receive a fixed
deadline at intake; changing the configured TTL does not extend old requests.
Maintenance transitions PENDING_APPROVAL and APPROVED to EXPIRED at or after
that deadline, without executing them.

An EXECUTING request past the stale threshold is marked for reconciliation.
Its status, result and error are preserved. The dashboard shows an unknown
outcome, and no automatic retry occurs. The marker is cleared only when an
adapter response is recorded.

## Health

`GET /healthz` (process is up) and `GET /readyz` (database reachable). No
authentication.
