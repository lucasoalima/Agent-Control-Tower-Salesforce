import Link from "next/link";
import { formatDate, formatTime } from "@/lib/format";
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
import type { ExecutionSummary } from "@/lib/types";
import { ToneLabel } from "./ui";

/**
 * One tool request, drawn like a flight progress strip: fixed boxes, always in
 * the same order, so a column of strips can be scanned vertically. The edge
 * colour is the policy decision; the last box is what happened next.
 */
export function ExecutionStrip({ execution }: { execution: ExecutionSummary }) {
  const e = execution;
  const columns =
    "sm:grid-cols-[5.5rem_minmax(0,1fr)_minmax(0,1.3fr)] lg:grid-cols-[5.5rem_minmax(0,1fr)_minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1.1fr)]";
  const box = "min-w-0 px-3 py-2 sm:border-l sm:border-line";

  return (
    <li className="list-none">
      <Link
        href={`/executions/${e.id}`}
        className={`relative grid grid-cols-2 overflow-hidden rounded-sm border border-line bg-surface pl-1.5 hover:bg-raised ${columns}`}
      >
        <span aria-hidden className={`absolute inset-y-0 left-0 w-1.5 ${TONE_BG[EFFECT_TONE[e.policy_effect]]}`} />

        <div className="px-3 py-2 text-[13px] tabular-nums">
          <time dateTime={e.requested_at} className="block text-ink">
            {formatTime(e.requested_at)}
          </time>
          <span className="text-muted">{formatDate(e.requested_at)}</span>
        </div>

        <div className={box}>
          <p className="truncate font-bold">{e.agent.name}</p>
          <p className="truncate text-[13px] text-muted">{PROVIDER_LABEL[e.agent.provider]}</p>
        </div>

        <div className={`${box} max-sm:col-span-2`}>
          <p className="truncate">{e.operation_title ?? "Unknown operation"}</p>
          <p className="truncate text-[13px] text-muted">{e.operation_key}</p>
        </div>

        <div className={`${box} sm:col-start-2 lg:col-start-auto`}>
          <p className="truncate">{e.environment.name}</p>
          <p className="truncate text-[13px] text-muted">{KIND_LABEL[e.environment.kind]}</p>
        </div>

        <div className={box}>
          <p className="truncate">
            <ToneLabel tone={STATUS_TONE[e.status]}>{STATUS_LABEL[e.status]}</ToneLabel>
          </p>
          <p className="truncate text-[13px] text-muted">
            {e.status === "DENIED" ? REASON_LABEL[e.decision_reason] : EFFECT_LABEL[e.policy_effect]}
          </p>
        </div>
      </Link>
    </li>
  );
}

export function StripList({ executions }: { executions: ExecutionSummary[] }) {
  return (
    <ul className="flex flex-col gap-1.5">
      {executions.map((e) => (
        <ExecutionStrip key={e.id} execution={e} />
      ))}
    </ul>
  );
}

export function StripLegend() {
  return (
    <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-muted">
      <span>Edge colour is the policy decision:</span>
      {(["ALLOW", "REQUIRE_APPROVAL", "DENY"] as const).map((effect) => (
        <span key={effect} className="inline-flex items-center gap-1.5">
          <span aria-hidden className={`h-3.5 w-1.5 ${TONE_BG[EFFECT_TONE[effect]]}`} />
          {EFFECT_LABEL[effect].toLowerCase()}
        </span>
      ))}
    </p>
  );
}
