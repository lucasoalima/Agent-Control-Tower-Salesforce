# Decisions and trade-offs

Short records of choices that were not obvious. Newest last.

## 1. Policy evaluation was pulled forward from Phase 2 into Phase 1

The plan puts the policy engine in Phase 2. But the Phase 1 dashboard must show
real data, real data comes from the gateway, and a gateway without policy would
either execute everything (unsafe) or nothing (useless). So Phase 1 contains the
decision function and the three outcomes. Phase 2 keeps what is hard about
approvals: binding a human decision to an action hash, expiry, single use and
concurrency.

**Cost:** Phase 1 is larger. **Benefit:** there has never been a version of the
gateway that executes without a policy decision.

## 2. Operations are code, policies are data

An operation needs a parameter schema and an adapter implementation; both are
security-relevant and should be code reviewed. Which effect applies to an
operation in which kind of environment is configuration.

## 3. An agent is bound to one environment

The request has no "target" field. This removes a class of mistakes and attacks
(an agent talked into naming production). **Cost:** an agent working across two
orgs needs two identities.

## 4. Refuse payloads containing secrets; do not rewrite them

See the security model. What is stored, hashed, approved and executed must be
one object.

## 5. Structured queries instead of SOQL

Validating free-form SOQL safely needs a real parser. A structured query
(`sobject`, `fields`, `limit`) is trivially validated. **Cost:** no `WHERE`
clause yet, which limits usefulness. A filtered form can be added as structured
conditions.

## 6. Denied requests return HTTP 200

The HTTP status describes the exchange (authenticated, processed, recorded); the
body describes the outcome. This also maps cleanly onto an MCP tool result
later. **Cost:** clients must read the body.

## 7. The ORM is used directly by the gateway service

A full repository layer with separate domain entities would double the model
code for no present benefit. Instead the parts that benefit from purity
(policy, lifecycle, fingerprint, redaction) are pure functions over plain
values, and the service that orchestrates them uses SQLAlchemy directly.

## 8. Synchronous SQLAlchemy

Requests are short transactions and FastAPI runs sync endpoints in a thread
pool. Async would add complexity without a measured need. Revisit if adapter
calls become long-running (deployments can take minutes; Phase 3 will likely
move execution to a background worker rather than make the request path async).

## 9. Enumerations as VARCHAR + CHECK, not native PostgreSQL enums

Adding a value is a simple constraint change instead of `ALTER TYPE`.

## 10. Integration tests use PostgreSQL, not SQLite

The schema uses JSONB and the tests should exercise the real migrations.
**Cost:** tests need a database; they skip locally without one and fail in CI.

## 11. Same-origin proxy instead of CORS

The browser talks only to the dashboard origin, which forwards to the backend.
The session cookie can be `SameSite=Strict` and the backend needs no CORS
configuration. The proxy refuses `/gateway/*` and never forwards an
`Authorization` header.

## 12. Hand-written UI primitives instead of shadcn/ui

The brief suggested shadcn/ui. The dashboard currently needs a handful of
primitives, and its main element (the execution strip) is custom anyway.
Revisit when Phase 2 needs dialogs and forms with real accessibility
requirements; Radix-based components would earn their place there.

## 13. Lessons from building Phase 1

- **A bug only the browser found.** After a signed-out visit, the client cache
  held a "401" for the current operator; signing in then bounced straight back
  to the sign-in page. API tests could not see it. The cache is now cleared on
  sign-in and sign-out.
- **Mutation testing found a gap.** Eleven security checks were removed one at
  a time to see whether the suite noticed. One removal survived, because the
  disabled-agent check exists in two layers. A test now pins the behaviour that
  only the outer check provides.
- **A tautological assertion slipped into a test** and was removed during
  review. Green tests are not evidence until you have seen them fail.


## 14. Enforce append-only audit events in PostgreSQL

Lucas requested on 10 November 2026: "No UPDATE or DELETE path in application
code; enforce it in the database if practical (trigger or revoked privileges)
and test it."

The application appends events in the transaction that changes state. Database
triggers also reject UPDATE, DELETE and TRUNCATE, so direct SQL cannot silently
rewrite events. Revoking privileges from the local owner would not provide
the same protection; the owner could grant them back.

Actor and execution identifiers have no cascading foreign keys, preserving
history if operational records are removed. Credentials are omitted from
authentication events, and event details use the existing redactor. A database
administrator remains trusted.

Original, backend/src/control_tower/api/routes/auth.py, login, line 69:

```python
session.commit()  # Only the session was persisted; the login existed in structured logs.
```

Updated, the same method, lines 91-99:

```python
record_event(  # Store the login and its session in the same database transaction.
    session,
    "auth.login",
    actor_type=AuditActorType.OPERATOR,
    actor_id=operator.id,
    correlation_id=correlation_id,
    occurred_at=now,
)
session.commit()  # Confirm both records together.
```

## 15. Expire approvals and reconcile without guessing outcomes

Lucas requested on 10 November 2026: "Background expiry of stale approvals,
and reconciliation of executions stuck in EXECUTING (mark them clearly; do not
guess the outcome)."

Requests store their deadline at intake, with a default lifetime of five
minutes. Legacy waiting records use their original request time plus that
default. Separate timestamps describe expiry and required reconciliation.

The worker uses bounded, row-locked batches and skips locked rows. It never
calls adapters. An EXECUTING record is marked as unknown rather than failed,
and an actual adapter response clears the marker. This preserves uncertainty
during the crash window without risking a duplicate side effect.

Original, backend/src/control_tower/core/lifecycle.py, _TRANSITIONS, line 19:

```python
S.APPROVED: frozenset({S.EXECUTING}),  # Approval had no expiry path before dispatch.
```

Updated, the same transition entry, line 21:

```python
S.APPROVED: frozenset({S.EXECUTING, S.EXPIRED}),  # An unused approval can expire.
```

The core remains free of I/O. The lifespan owns the worker, its database pool
and shutdown; maintenance events are committed with the state change.

## 16. Bind approval to immutable evidence and consume it under a row lock

Lucas requested on 10 November 2026: "The approval is bound to the exact action:
execution id, action_hash, agent, target environment. Approving never creates a
different payload."

The operator supplies the displayed hash, agent and environment IDs; the URL
selects the execution. An approval is an immutable audit event rather than a
second mutable grant table. The original request event, canonical fingerprint
and fixed deadline must still match. A second event records consumption in the
same transaction as EXECUTING, committed before any adapter side effect.

Approval is committed before dispatch. An interrupted dispatch can be resumed
explicitly while APPROVED and unexpired. A competing caller may consume it first;
the loser receives a conflict. Neither path retries a running or terminal action.

Current agent status, grants, environment binding, policy and parameter schema
are checked again at approval and dispatch. Human approval cannot override the
production guardrail. Rejection does not require continued execution eligibility.

Original, backend/src/control_tower/gateway/service.py, submit, line 154:

```python
return execution  # Pending requests were persisted without a decision path.
```

Updated, the same file, approve, line 182:

```python
execution.status = ExecutionStatus.APPROVED  # Bind the human decision before dispatch.
self._approval_event("approval.approved", execution, operator_id, correlation_id)  # Retain who approved.
self._session.commit()  # Persist the grant so interrupted dispatch can be resumed explicitly.
```
