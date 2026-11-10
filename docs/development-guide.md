# Development guide

## Prerequisites

- Docker (for PostgreSQL, or for the whole stack)
- [uv](https://docs.astral.sh/uv/) and Python 3.12+
- Node.js 22+

No Salesforce account is needed. Everything runs against the mock adapter.

## Option A: everything in Docker

```bash
cp .env.example .env
make up
make seed        # note the operator password and agent tokens
make simulate
```

Dashboard: <http://localhost:3000>. API docs: <http://localhost:8000/api/v1/docs>.

## Option B: API and dashboard on the host

```bash
cp .env.example .env
make db                               # PostgreSQL only

cd backend
uv sync
uv run act migrate
uv run act seed
uv run act simulate
uv run uvicorn control_tower.main:app --reload --no-access-log

cd ../frontend                        # second terminal
npm ci
npm run dev
```

The backend reads `ACT_*` variables from the environment or from `backend/.env`.

## The `act` command

| Command | Does |
| --- | --- |
| `act migrate` | Applies Alembic migrations |
| `act seed` | Creates three mock environments, the default policies, three demo agents and the first operator. Idempotent. Prints credentials once |
| `act simulate` | Sends 16 scripted requests through the gateway, covering every outcome |

`act simulate` calls the gateway service in-process, so it produces real
decisions and records but does not exercise token authentication. Mock
organisation state lives in the memory of the process that ran the operation.

## Tests

```bash
make test-backend     # ruff, ruff format --check, mypy --strict, pytest
make test-frontend    # eslint, tsc, vitest, next build
```

Integration tests need PostgreSQL. By default they use
`postgresql+psycopg://act:act_dev_password@localhost:5432/act_test`
(override with `ACT_TEST_DATABASE_URL`). Create the database once:

```bash
docker compose exec db createdb -U act act_test
```

The test session drops and recreates the `public` schema in that database and
builds it with the real migrations. Each test runs in a transaction that is
rolled back. Without a reachable database the integration tests are skipped;
with `ACT_REQUIRE_DB=1` (set in CI and by `make test-backend`) they fail instead.

## Changing the schema

```bash
cd backend
# edit persistence/models.py
uv run alembic revision --autogenerate -m "describe the change"
# read and correct the generated file
uv run act migrate
```

`tests/integration/test_migrations.py` fails if models and migrations differ.

## Adding an operation

1. In `platforms/<platform>/operations.py`, define a parameter model that
   subclasses `OperationParams` (unknown fields forbidden, strict types,
   frozen). Constrain every string.
2. Add an `OperationSpec` with the right `RiskClass`. Anything that changes the
   target is `WRITE` or `DEPLOY`.
3. Implement it in the mock adapter, with state that tests can inspect.
4. Add a default policy for all three environment kinds in `bootstrap.py`. A
   test fails if an operation has none.
5. Add tests: schema rejection cases, adapter behaviour, and a gateway test for
   the outcome that must *not* happen.
6. Document it in `docs/api.md`.

## Adding a platform

Create `platforms/<name>/` with operations and an adapter implementing
`PlatformAdapter`, and register both in `platforms/registry.py`. Nothing outside
`platforms/` should need to change.

## Conventions

- English for code, comments, documentation and UI text.
- `control_tower.core` has no I/O.
- Timestamps are timezone-aware UTC.
- Log with `structlog` key-value events. Never log a token; the redactor is a
  safety net, not permission.
