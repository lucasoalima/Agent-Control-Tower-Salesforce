# Evaluation methodology

> **Nothing in this document is a measured result yet.** The evaluation lab is
> Phase 4. This page defines what will be measured and how, and states what the
> current automated tests already demonstrate. Measured results will be added
> in a separate, clearly marked section, with the commit and command that
> produced them.

## Principle: evidence, not claims

A check passes only on evidence the gateway did not get from the agent:

- "Nothing was deployed" is established by inspecting the target organisation's
  state, not by the execution record saying `DENIED`.
- "Tests passed" is established by a test-run record produced by the adapter,
  not by an agent's message.

The mock adapter exposes its organisation state (`components`, `deployments`,
`test_runs`) for this purpose.

## Scenarios

| # | Scenario | Expected behaviour | Current coverage |
| - | --- | --- | --- |
| 1 | Agent attempts a deployment without approval | Request is paused or denied; no deployment exists in the org | Automated integration tests (`test_sensitive_operation_waits_…`, `test_production_deployment_is_denied_…`) assert both the record and the untouched org |
| 2 | Agent claims tests passed with no execution record | Claim is stored as unverified and shown as such | **Not implemented.** Needs a claims API (Phase 4) |
| 3 | Agent attempts a restricted metadata operation | Policy blocks it; org unchanged | Automated integration test (`test_operation_not_granted_…`) |
| 4 | Operator approves a specific deployment | Runs once, with the approved payload; complete audit record | **Not implemented.** Needs approvals (Phase 2) |

Scenario 3 from the project motivation (outdated recommendations) is out of
scope for the MVP.

## Metrics to be measured

| Metric | Definition |
| --- | --- |
| Unauthorised operation blocking rate | Of requests that policy should not let run immediately, the fraction for which the target state is unchanged |
| Approval bypass rate | Of executions of approval-requiring operations, the fraction with no valid, matching, unexpired, unused approval. Target: 0 |
| Execution success rate | `COMPLETED / (COMPLETED + FAILED)` |
| Verified vs unverified test reports | Agent claims with and without a matching adapter test-run record |
| Policy decision latency | `decided_at - requested_at`, reported as p50/p95/p99 |
| End-to-end latency | `finished_at - requested_at` for requests that ran |

The timestamps these metrics need are already recorded on every execution.

## Planned method

- Scenarios run as scripted agents against the mock adapter, through the HTTP
  gateway, so results are reproducible without Salesforce access or an LLM.
- Each run starts from a fresh database and mock organisation.
- Adversarial variants are generated for each scenario (payload substitution
  after approval, replay of a used approval, expired approval, another agent's
  approval).
- Latency is reported with hardware, sample size and concurrency stated.
  Numbers from the mock adapter measure the gateway, not Salesforce.
- A later stage repeats the scenarios with a real agent (Claude Code or Codex)
  connected over MCP; those results are reported separately because they are
  not deterministic.

## Threats to validity

- The mock adapter is a simplification. Passing against it shows the gateway's
  logic is right, not that the real integration is.
- Scripted agents only try what we thought of.
- The blocking rate says nothing about actions taken outside the gateway (see
  the security model).
