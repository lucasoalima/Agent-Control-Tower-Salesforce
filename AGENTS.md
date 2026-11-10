# Instructions for AI agents working on this repository

This project exists because written instructions to agents are not controls.
These rules are still worth following; CI enforces what it can.

## Never

- Deploy to, or run commands against, any real Salesforce org. Development uses
  the mock adapter only.
- Add an operation that runs arbitrary shell commands, arbitrary Salesforce CLI
  commands or free-form SOQL.
- Put a credential, token or org auth URL in code, tests, fixtures, logs or docs.
- Weaken a check in `core/policy.py`, `gateway/service.py` or `api/deps.py` to
  make a test pass. If a security test fails, the code is wrong.
- Report a test, build or deployment as successful unless you ran it in this
  session and saw it succeed. If you could not run something, say so and say why.

## Always

- Run `make test-backend` and `make test-frontend` before saying work is done,
  and report the actual output.
- Add tests with every behaviour change. For anything security-relevant, add
  the negative test too (the request that must be refused).
- Keep `control_tower/core` free of I/O and framework imports.
- Add a migration for every model change. `test_migrations.py` fails on drift.
- Update `docs/` when behaviour, API or the security model changes.

## Layout

- `backend/src/control_tower/core`: pure domain (policy, lifecycle, fingerprint, redaction)
- `backend/src/control_tower/gateway`: the request pipeline
- `backend/src/control_tower/platforms`: operation schemas and adapters per platform
- `backend/src/control_tower/api`: HTTP layer
- `frontend/src`: Next.js dashboard

## Adding an operation

See "Adding an operation" in `docs/development-guide.md`. A new operation needs
a strict parameter schema, a risk class, a default policy for all three
environment kinds, a mock implementation and tests.
