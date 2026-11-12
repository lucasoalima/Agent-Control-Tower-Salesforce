"use client";

import { useRef, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { ApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { decideExecution, governanceKey, savePolicy, type ApprovalAction } from "@/lib/governance";
import { EFFECT_LABEL, EFFECT_TONE, KIND_LABEL, STATUS_LABEL } from "@/lib/labels";
import type { AuditEvent, ExecutionDetail, ExecutionSummary, PolicyCell, PolicyEffect } from "@/lib/types";
import { ErrorState, JsonBlock, Loading, SectionTitle, ToneLabel } from "./ui";

const button = "cursor-pointer rounded-sm border border-line bg-raised px-3 py-2 font-bold disabled:cursor-default disabled:opacity-60";

export function ApprovalReview({ execution: e }: { execution: ExecutionDetail }) {
  const { mutate } = useSWRConfig();
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const inFlight = useRef(false);
  if (e.status !== "PENDING_APPROVAL" && e.status !== "APPROVED") return null;

  async function decide(action: ApprovalAction) {
    if (!confirmed || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setMessage("");
    try {
      const result = await decideExecution(e, action);
      setMessage("Recorded: " + STATUS_LABEL[result.status] + ".");
      setConfirmed(false);
    } catch (error) {
      setMessage((error instanceof ApiError ? error.message : "Decision not confirmed.") +
        " Refresh this record before trying again; do not submit a new tool request.");
      setConfirmed(false);
    } finally {
      await mutate(governanceKey).catch(() => undefined);
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <section className="mb-6 rounded-sm border border-line bg-surface p-4" aria-busy={busy}>
      <SectionTitle>Review this exact action</SectionTitle>
      <p>{e.operation_title ?? e.operation_key} — {e.agent.name}</p>
      <p>{e.environment.name} ({KIND_LABEL[e.environment.kind]})</p>
      <p className="my-2 text-[13px] text-muted">
        Expires: {e.approval_expires_at ? formatDateTime(e.approval_expires_at) : "Unavailable"}
      </p>
      <dl className="mb-3 break-all font-mono text-[12px]">
        <dt>Execution ID</dt><dd>{e.id}</dd>
        <dt>Agent ID</dt><dd>{e.agent.id}</dd>
        <dt>Environment ID</dt><dd>{e.environment.id}</dd>
        <dt>Action hash (SHA-256)</dt><dd>{e.action_hash ?? "Unavailable"}</dd>
      </dl>
      <JsonBlock value={e.parameters} />
      <label className="mt-3 flex items-start gap-2">
        <input type="checkbox" checked={confirmed} disabled={busy || !e.action_hash}
          onChange={(event) => setConfirmed(event.target.checked)} />
        I reviewed the parameters, target environment and action hash above.
      </label>
      <div className="mt-3 flex flex-wrap gap-2">
        {e.status === "PENDING_APPROVAL" ? (
          <>
            <button type="button" className={button} disabled={!confirmed || busy || !e.action_hash}
              onClick={() => void decide("approve")}>Approve and run</button>
            <button type="button" className={button} disabled={!confirmed || busy || !e.action_hash}
              onClick={() => void decide("reject")}>Reject request</button>
          </>
        ) : (
          <button type="button" className={button} disabled={!confirmed || busy || !e.action_hash}
            onClick={() => void decide("execute")}>Run approved action</button>
        )}
      </div>
      <p role="status" className="mt-2 text-[13px]">{busy ? "Recording decision…" : message}</p>
    </section>
  );
}

export function ApprovalRequest({ execution: e }: { execution: ExecutionSummary }) {
  const [open, setOpen] = useState(false);
  const { data, error } = useSWR<ExecutionDetail>(open ? "/executions/" + e.id : null);
  return (
    <li className="rounded-sm border border-line bg-surface p-3">
      <button type="button" className="w-full cursor-pointer text-left" aria-expanded={open}
        onClick={() => setOpen(!open)}>
        <span className="font-bold">{e.operation_title ?? e.operation_key}</span>
        {" — "}{e.agent.name} → {e.environment.name}
        <span className="ml-3 text-accent underline">{open ? "Close review" : "Review request"}</span>
      </button>
      {open ? (
        error ? <ErrorState error={error} /> : data ? (
          <div className="mt-3">
            <ApprovalReview key={data.id + data.action_hash + data.status} execution={data} />
          </div>
        ) : <Loading label="Loading stored action" />
      ) : null}
    </li>
  );
}

export function AuditHistory({ events }: { events: AuditEvent[] }) {
  return (
    <section>
      <SectionTitle>Approval history</SectionTitle>
      {events.length ? (
        <ol className="flex flex-col gap-3">
          {events.map((event) => (
            <li key={event.id} className="rounded-sm border border-line p-3">
              <p className="font-bold">{event.action}</p>
              <p className="text-[13px] text-muted">
                <time dateTime={event.occurred_at}>{formatDateTime(event.occurred_at)}</time>
                {" · "}{event.actor_type}{event.actor_id ? " " + event.actor_id : ""}
              </p>
              <p className="break-all text-[12px] text-muted">Correlation: {event.correlation_id}</p>
              <JsonBlock value={event.details} />
            </li>
          ))}
        </ol>
      ) : <p className="text-muted">No approval events recorded.</p>}
    </section>
  );
}

export function ExecutionAudit({ id }: { id: string }) {
  const { data, error } = useSWR<AuditEvent[]>("/executions/" + id + "/audit-events", {
    refreshInterval: 5000,
  });
  if (error) return <ErrorState error={error} />;
  if (!data) return <Loading label="Loading approval history" />;
  return <AuditHistory events={data} />;
}

export function PolicyEditor({ operation, title, cell }: {
  operation: string; title: string; cell: PolicyCell;
}) {
  const { mutate } = useSWRConfig();
  const [effect, setEffect] = useState<PolicyEffect>(cell.configured ?? cell.effective);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const inFlight = useRef(false);
  const fixed = cell.reason === "PRODUCTION_GUARDRAIL";

  async function save() {
    if (inFlight.current || fixed) return;
    inFlight.current = true;
    setBusy(true);
    setMessage("");
    try {
      await savePolicy(operation, cell.environment_kind, effect);
      setMessage("Policy saved.");
    } catch (error) {
      setMessage((error instanceof ApiError ? error.message : "Change not confirmed.") +
        " Reload the matrix before saving again.");
    } finally {
      await mutate(governanceKey).catch(() => undefined);
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <td className="border-t border-l border-line px-3 py-2 align-top" aria-busy={busy}>
      <ToneLabel tone={EFFECT_TONE[cell.effective]}>{EFFECT_LABEL[cell.effective]}</ToneLabel>
      {fixed ? <p className="text-[13px] text-muted">Fixed guardrail — cannot be edited</p> : (
        <div className="mt-2 flex flex-col gap-2">
          {cell.configured === null ? <p className="text-[13px] text-muted">No rule, blocked by default</p> : null}
          <label className="text-[13px]">
            {title} on {KIND_LABEL[cell.environment_kind]}
            <select value={effect} disabled={busy} className="mt-1 w-full rounded-sm border border-line bg-raised p-2"
              onChange={(event) => setEffect(event.target.value as PolicyEffect)}>
              {(["ALLOW", "REQUIRE_APPROVAL", "DENY"] as const).map((value) => (
                <option key={value} value={value}>{EFFECT_LABEL[value]}</option>
              ))}
            </select>
          </label>
          <button type="button" className={button} disabled={busy || effect === cell.configured}
            onClick={() => void save()}>Save policy</button>
        </div>
      )}
      <p role="status" className="mt-2 text-[13px]">{busy ? "Saving policy…" : message}</p>
    </td>
  );
}
