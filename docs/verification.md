# Verification log

What was actually run, and what was not. Updated at each milestone.

## Phase 1 (10 November 2026)

Environment: Linux sandbox, Python 3.12.3, Node.js 22, PostgreSQL 16.15
(installed natively), headless Chromium 153 driven by Playwright.
Resolved versions are pinned in `backend/uv.lock` and `frontend/package-lock.json`
(FastAPI 0.162, SQLAlchemy 2.1, Pydantic 2.14, Next.js 16.4, React 19.3, Tailwind 4.3).

### Executed, with results

| Check | Command | Result |
| --- | --- | --- |
| Backend lint | `ruff check .` | no findings |
| Backend format | `ruff format --check .` | 63 files formatted |
| Backend types | `mypy` (strict) | no issues in 43 source files |
| Backend tests | `ACT_REQUIRE_DB=1 pytest --cov` | **260 passed**, 0 skipped, 94% line coverage |
| Migrations | part of the suite | upgrade, drift check against models, downgrade and re-upgrade on PostgreSQL 16 |
| Frontend lint | `eslint .` | no findings |
| Frontend types | `tsc --noEmit` | no errors |
| Frontend tests | `vitest run` | **18 passed** |
| Frontend build | `next build` | succeeded, 8 routes |

Of the 260 backend tests, 159 are unit tests with no I/O and 101 are
integration tests against PostgreSQL through the real migrations.

**Mutation check.** Eleven security checks were removed from the code one at a
time and the suite re-run. Ten were caught immediately. One (disabled-agent
check at gateway intake) survived because the policy function repeats it; a test
was added for the behaviour unique to the intake check, after which all eleven
mutants fail the suite.

**Live run** of `uvicorn` and `next start` against PostgreSQL, checked with curl:

| Check | Observed |
| --- | --- |
| Management API through the dashboard without a session | 401 |
| Gateway API through the dashboard origin, with a valid agent token | 404 (not exposed there) |
| Operator sign-in through the dashboard | 200, HttpOnly cookie set |
| Agent requests a sandbox deployment directly at the backend | 202 `PENDING_APPROVAL` |
| Overview counts before and after that request | executions 16 to 17, waiting 2 to 3 |
| Agent token in the API log | 0 occurrences |
| `Content-Security-Policy: frame-ancestors 'none'`, `X-Frame-Options: DENY` | present |

**Browser walkthrough** in headless Chromium at 1440×900 (light and dark) and
390×844: sign in through the form, then overview, agents, approval queue,
filtered execution history, policies and an execution detail page. All rendered
with data from the API; the only console message was the expected 401 before
sign-in. This walkthrough found one bug (sign-in redirect loop from a stale
client cache), which was fixed and re-verified. The screenshots in
`docs/images/` are from this run.

### Not executed

| Item | Why | Risk |
| --- | --- | --- |
| `docker compose up`, both Dockerfiles | Docker is not available in the sandbox. The Compose and workflow files were only checked for YAML validity | Medium. Most likely failure points: the `uv` image tag, the standalone Next.js copy step |
| GitHub Actions workflow | Cannot run outside GitHub | Low to medium. It runs the same commands as above |
| `make` targets | They wrap Docker and the commands above; not run as targets | Low |
| Frontend component or end-to-end tests in CI | Not written. The browser walkthrough was a one-off script, not part of the repository | Regressions in the UI would not be caught automatically |
| Anything against real Salesforce | No adapter exists yet, by design | n/a |
| Load or latency measurement | Phase 4 | n/a |
| Windows and macOS | Only Linux was used | Low |

### Not built yet

Approval decisions, policy editing, audit event log, MCP server, real Salesforce
adapter, evaluation lab, OpenTelemetry. See the status table in the README.

## Governance API milestone

Executed on the Windows host with Python 3.12.10 and PostgreSQL 16 in Docker:

| Check | Result |
| --- | --- |
| Ruff lint and format | Passed; 71 Python files formatted |
| Strict mypy | Passed; 45 source files |
| Backend suite | 383 passed, 97% coverage, database required |
| Frontend lint, TypeScript and production build | Passed |
| Frontend tests | 20 passed |
| Approval security mutations | 13 detected, isolated processes, no source edits |
| Normal approval and authentication tests after mutations | 107 passed |
| Docker build and startup | Both images built; backend and database healthy |
| Live requests through dashboard proxy | Anonymous 401; approved COMPLETED; replay 409; rejected without execution; three audit events |

All platform actions used the mock Salesforce adapter. No push or real Salesforce
operation was performed. The earlier Phase 1 "not executed" table records that
milestone only; Docker and Windows have since been verified as listed above.

For dashboard changes, run the Makefile's underlying backend and frontend
checks. Governance tests cover request bindings, no automatic retries, review
markup, policy controls, history filtering and catalogue schema changes.
Interactive browser verification remains a separate check from these tests.

## Phase 2 dashboard verification (11 November 2026)

- Backend: 388 tests passed, 97% coverage; Ruff and strict mypy passed.
- Frontend: 51 tests passed; ESLint, TypeScript and the production build passed.
- Docker: both images built and the local stack started successfully.
- Browser: reviewed parameters and binding before approval; approved from the
  queue, rejected from detail, resumed an unused approval and checked the
  immutable history after completion.
- Policy editing was saved and restored; four non-read production cells exposed
  fixed guardrails without editing controls. No browser console errors occurred.
- Nine new mutations were detected: six frontend and three backend checks.
  Transformations were isolated in memory; no source mutations were retained.
- The temporary browser operator and session were removed after verification.
- The browser walkthrough is manual. CI covers markup and request tests, not
  an automated browser end-to-end suite. All platform actions used the mock.
