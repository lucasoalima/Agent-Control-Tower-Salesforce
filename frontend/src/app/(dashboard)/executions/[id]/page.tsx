"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { ReconciliationNotice } from "@/components/reconciliation-notice";
import { ErrorState, JsonBlock, Loading, SectionTitle, Tag, ToneLabel } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { elapsedMs, formatDateTime, formatDuration } from "@/lib/format";
import {
  EFFECT_LABEL,
  EFFECT_TONE,
  KIND_LABEL,
  PROVIDER_LABEL,
  REASON_LABEL,
  STATUS_LABEL,
  STATUS_TONE,
  TONE_BG,
} from "@/lib/labels";
import type { ExecutionDetail } from "@/lib/types";

const LIVE = new Set(["PENDING_APPROVAL", "APPROVED", "EXECUTING"]);

function Timeline({ e }: { e: ExecutionDetail }) {
  const steps = [
    { label: "Requested by the agent", at: e.requested_at, note: "" },
    {
      label: "Policy decided",
      at: e.decided_at,
      note: formatDuration(elapsedMs(e.requested_at, e.decided_at)),
    },
    { label: "Started running", at: e.started_at, note: "" },
    {
      label: e.status === "FAILED" ? "Failed" : "Finished",
      at: e.finished_at,
      note: formatDuration(elapsedMs(e.started_at, e.finished_at)),
    },
  ];
  return (
    <ol className="border-l border-line">
      {steps.map((step) => (
        <li key={step.label} className="relative py-1.5 pl-5">
          <span
            aria-hidden
            className={`absolute top-3 -left-[5px] size-[9px] rounded-full border ${
              step.at ? "border-ink bg-ink" : "border-line bg-ground"
            }`}
          />
          <p className={step.at ? "" : "text-muted"}>{step.label}</p>
          <p className="text-[13px] text-muted tabular-nums">
            {step.at ? (
              <>
                <time dateTime={step.at}>{formatDateTime(step.at)}</time>
                {step.note ? `, took ${step.note}` : ""}
              </>
            ) : (
              "did not happen"
            )}
          </p>
        </li>
      ))}
    </ol>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[13px] text-muted">{label}</dt>
      <dd className="font-mono text-[12px] break-all">{children}</dd>
    </div>
  );
}

export default function ExecutionPage() {
  const { id } = useParams<{ id: string }>();
  const { data: e, error } = useSWR<ExecutionDetail, ApiError>(`/executions/${id}`, {
    refreshInterval: (latest) => (latest && LIVE.has(latest.status) ? 3000 : 0),
  });

  const back = (
    <Link href="/executions" className="text-[13px] text-accent underline underline-offset-2">
      Back to execution history
    </Link>
  );

  if (error?.status === 404 || error?.status === 422) {
    return (
      <>
        {back}
        <h1 className="mt-3 text-[1.6rem] font-bold">Execution record not found</h1>
        <p className="mt-1 text-muted">No record has this ID. It may have been mistyped.</p>
      </>
    );
  }
  if (error) return <ErrorState error={error} />;
  if (!e) return <Loading />;

  const neverRan = e.status === "DENIED" || e.status === "REJECTED" || e.status === "EXPIRED";

  return (
    <>
      {back}
      <header className="relative mt-3 mb-6 overflow-hidden rounded-sm border border-line bg-surface py-4 pr-5 pl-6">
        <span aria-hidden className={`absolute inset-y-0 left-0 w-1.5 ${TONE_BG[EFFECT_TONE[e.policy_effect]]}`} />
        <h1 className="text-[1.6rem] leading-tight font-bold tracking-tight">
          {e.operation_title ?? "Unknown operation"}
        </h1>
        <p className="text-[13px] text-muted">{e.operation_key}</p>
        <p className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1">
          <ToneLabel tone={STATUS_TONE[e.status]}>{STATUS_LABEL[e.status]}</ToneLabel>
          <span>
            {e.agent.name} <span className="text-muted">({PROVIDER_LABEL[e.agent.provider]})</span>
          </span>
          <span className="flex items-center gap-2">
            {e.environment.name} <Tag>{KIND_LABEL[e.environment.kind]}</Tag>
          </span>
        </p>
      </header>

      <ReconciliationNotice requiredAt={e.reconciliation_required_at} />

      <div className="grid gap-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,3fr)]">
        <div className="flex min-w-0 flex-col gap-7">
          <section>
            <SectionTitle>Policy decision</SectionTitle>
            <p>
              <ToneLabel tone={EFFECT_TONE[e.policy_effect]}>{EFFECT_LABEL[e.policy_effect]}</ToneLabel>
            </p>
            <p className="text-[13px] text-muted">Decided by: {REASON_LABEL[e.decision_reason].toLowerCase()}</p>
            <p className="mt-1 max-w-[70ch] text-muted">{e.decision_detail}</p>
            {neverRan ? (
              <p className="mt-2 max-w-[70ch]">
                This request never reached {e.environment.name}. Nothing was changed.
              </p>
            ) : null}
          </section>

          <section>
            <SectionTitle
              aside={e.action_hash ? "exactly as validated and fingerprinted" : "as received, with secrets removed"}
            >
              What the agent asked for
            </SectionTitle>
            <JsonBlock value={e.parameters} />
          </section>

          {e.result ? (
            <section>
              <SectionTitle aside="returned by the environment, not reported by the agent">
                Result
              </SectionTitle>
              <JsonBlock value={e.result} />
            </section>
          ) : null}

          {e.error ? (
            <section>
              <SectionTitle>Error</SectionTitle>
              <JsonBlock value={e.error} />
            </section>
          ) : null}
        </div>

        <aside className="flex min-w-0 flex-col gap-7">
          <section>
            <SectionTitle>Timeline</SectionTitle>
            <Timeline e={e} />
          </section>

          <section>
            <SectionTitle>Identifiers</SectionTitle>
            <dl className="flex flex-col gap-2.5">
              <Field label="Execution ID">{e.id}</Field>
              <Field label="Correlation ID">{e.correlation_id}</Field>
              <Field label="Action hash (SHA-256)">
                {e.action_hash ?? "none: the request was refused before validation"}
              </Field>
            </dl>
          </section>
        </aside>
      </div>
    </>
  );
}
