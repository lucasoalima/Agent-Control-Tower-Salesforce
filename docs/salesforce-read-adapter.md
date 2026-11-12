# Salesforce REST reads

| 🧭 Navegação rápida | Detail | Files |
| --- | --- | --- |
| OAuth and reads | [Contract](#contract) | backend/src/control_tower/platforms/salesforce/read_adapter.py |
| Registration | [Activation](#activation) | backend/src/control_tower/cli.py, backend/src/control_tower/platforms/salesforce/provisioning.py |
| Startup | [Configuration](#configuration) | backend/src/control_tower/platforms/registry.py |
| Verification | [Tests](#tests) | backend/tests/unit/test_salesforce_read_adapter.py, backend/tests/integration/test_salesforce_read_adapter.py |
| Decision | [Evidence and change](#evidence-and-change) | README.md, docs/architecture.md, docs/security-model.md |

Related notes: [Architecture](architecture.md), [Security model](security-model.md),
[MCP server](mcp-server.md), [Development guide](development-guide.md).

## Contract

The opt-in salesforce-readonly adapter implements salesforce.org.describe and
salesforce.data.query. It uses OAuth 2.0 client credentials on the configured
My Domain origin. The backend holds the client credentials and the access token; agents do not
receive them.

Each action uses one OAuth grant, one fixed Organization query and, for
data.query, one structured data query. The returned instance origin and org ID
must match the configured target. Access tokens are not cached or refreshed.

The adapter revalidates the existing operation model before constructing SOQL
from its identifiers and bounded limit. No free-form query is accepted. The
S608 suppression marks this validated construction; injection tests exercise
constructed models that bypass ordinary Pydantic initialization.

Results contain the existing org identity or sobject/total_size/records keys.
Record attributes are removed recursively; relationship objects are retained.
The shared redactor and exact credential-value replacement sanitize output.
Static errors never echo Salesforce response bodies or exception messages.

## Configuration

Provide ACT_SALESFORCE_CONNECTIONS as a JSON array through the backend process's
secure runtime environment. Do not save credentials in source, dotenv files,
CLI arguments, fixtures, docs or client configuration. This adapter's settings
do not load dotenv files.

| Connection field | Purpose |
| --- | --- |
| environment_id | Stable UUID for the environment row |
| name | Administrative environment name |
| kind | DEVELOPMENT, TESTING or PRODUCTION |
| org_id | Expected Salesforce org ID, 15 or 18 characters |
| origin | HTTPS My Domain origin, including sandbox domains where applicable |
| api_version | Explicit supported version, with no automatic latest-version choice |
| client_id, client_secret | Existing OAuth client-credentials integration credentials |

ACT_SALESFORCE_TIMEOUT_SECONDS bounds requests from 1 to 60 seconds, default 15.
Empty configuration leaves only the mock adapter registered. Duplicate UUIDs,
names or org IDs are refused. Each backend process must receive this configuration;
the existing demo Compose configuration continues to provision mock environments.

Use an already configured OAuth integration and dedicated least-privilege user.
This delivery creates no Salesforce metadata or users and makes no live org
request during registration or startup. Future live activation needs separate
authorization under this project's no-real-org development rule.

## Activation

With migrations applied and the runtime configuration present, a backend
administrator runs:

```bash
uv run act register-salesforce ENVIRONMENT_UUID
```

Replace ENVIRONMENT_UUID with the public UUID from the selected configuration.
The command creates a new environment row and registry.environment_registered
audit event atomically. Repeating the same binding is harmless. A conflicting
UUID or name is refused; existing environments are never retargeted.

Register an agent for that environment through the existing operator workflow.
Grants, policy and adapter support all remain necessary. Production data.query
is denied by the existing default policy; this delivery does not change it.

## Tests

Unit tests use generated in-memory credentials and httpx MockTransport.
Integration tests exercise provisioning, immutable audit history, gateway
discovery, execution records and existing denials with PostgreSQL and HTTP mocks.
No real Salesforce records are queried or exported.

Redirects, proxies and automatic retries are disabled. Responses are capped at
one MiB. Incomplete query pages are rejected; query locators are never followed.
Metadata reads, Apex tests, permission changes and deploy are unsupported.
Live org authentication and permissions remain unverified.

## Evidence and change

Lucas: "ok pode continuar". This follows the requested Salesforce read adapter
after the MCP delivery. A new adapter key keeps mock and REST environments
separate; explicit runtime bindings prevent agent-selected endpoints.

Original, backend/src/control_tower/platforms/registry.py, build_adapters, lines 39–41:

```python
registry = AdapterRegistry()  # Startup could register only the mock.
registry.register(MockSalesforceAdapter())
return registry
```

Updated, build_adapters, lines 43–50:

```python
registry = AdapterRegistry()
registry.register(MockSalesforceAdapter())  # Preserve the local default.
settings = SalesforceSettings()
if settings.connections:  # Require explicit backend-only configuration.
    registry.register(
        SalesforceReadAdapter(settings.connections, timeout_seconds=settings.timeout_seconds)
    )
return registry
```

Original, backend/src/control_tower/cli.py, main, line 89:

```python
args = parser.parse_args(argv)  # Only migrate, seed and simulate had been registered.
```

Updated, main, lines 123–129:

```python
register = sub.add_parser(  # Add a local administrative command, without remote calls.
    "register-salesforce",
    help="register a configured read-only environment without remote calls",
)
register.add_argument("environment_id", type=UUID)  # Select an existing runtime binding.
register.set_defaults(func=_cmd_register_salesforce)
args = parser.parse_args(argv)
```

The adapter, provisioning helper, registration command and their tests did not
exist before this delivery. Database models, schemas and policy rules are unchanged.

The OAuth design follows Salesforce's [integration-user guidance](https://developer.salesforce.com/blogs/2024/02/invoke-rest-apis-with-the-salesforce-integration-user-and-oauth-client-credentials)
and [REST query contract](https://developer.salesforce.com/docs/platform/api-rest/guide/resources-query.html).
