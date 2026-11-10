"use client";

import Link from "next/link";
import useSWR from "swr";
import { StripLegend, StripList } from "@/components/strip";
import { EmptyState, ErrorState, Loading, PageHeader, SectionTitle } from "@/components/ui";
import type { ExecutionPage, Overview } from "@/lib/types";

const REFRESH = { refreshInterval: 5000 };

function Tally({ overview }: { overview: Overview }) {
  const figures = [
    {
      href: "/agents",
      value: overview.agents_active,
      label: overview.agents_active === 1 ? "active agent" : "active agents",
      note: `of ${overview.agents_total} registered`,
    },
    { href: "/executions", value: overview.executions_total, label: "requests recorded", note: "all time" },
    {
      href: "/approvals",
      value: overview.pending_approvals,
      label: "waiting for approval",
      note: "nothing runs until decided",
      tone: overview.pending_approvals ? "text-approval" : "",
    },
    {
      href: "/executions?status=FAILED",
      value: overview.failed_actions,
      label: "failed actions",
      note: "ran and did not succeed",
      tone: overview.failed_actions ? "text-deny" : "",
    },
    {
      href: "/executions?status=DENIED",
      value: overview.policy_violations,
      label: "blocked by policy",
      note: "never ran",
      tone: overview.policy_violations ? "text-deny" : "",
    },
  ];
  return (
    <dl className="grid grid-cols-2 overflow-hidden rounded-sm border border-line bg-surface sm:grid-cols-3 lg:grid-cols-5">
      {figures.map((f) => (
        <Link
          key={f.label}
          href={f.href}
          className="-mt-px -ml-px flex flex-col-reverse border-t border-l border-line px-4 py-3 hover:bg-raised"
        >
          <dt>
            {f.label}
            <span className="block text-[13px] text-muted">{f.note}</span>
          </dt>
          <dd className={`text-[1.75rem] leading-tight tabular-nums ${f.tone ?? ""}`}>{f.value}</dd>
        </Link>
      ))}
    </dl>
  );
}

export default function OverviewPage() {
  const overview = useSWR<Overview>("/overview", REFRESH);
  const waiting = useSWR<ExecutionPage>("/executions?status=PENDING_APPROVAL&limit=5", REFRESH);
  const latest = useSWR<ExecutionPage>("/executions?limit=8", REFRESH);

  const error = overview.error ?? waiting.error ?? latest.error;
  if (error) return <ErrorState error={error} />;
  if (!overview.data || !waiting.data || !latest.data) return <Loading />;

  return (
    <>
      <PageHeader title="Overview">
        Every tool request your agents send through the gateway, and what policy decided.
      </PageHeader>

      <Tally overview={overview.data} />

      {overview.data.executions_total === 0 ? (
        <div className="mt-8">
          <EmptyState title="No requests recorded yet">
            Send the demo traffic with <code className="font-mono">make simulate</code>, or point an
            agent at the gateway using one of the tokens printed by{" "}
            <code className="font-mono">make seed</code>.
          </EmptyState>
        </div>
      ) : (
        <div className="mt-8 flex flex-col gap-8">
          <section>
            <SectionTitle
              aside={
                waiting.data.total > waiting.data.items.length ? (
                  <Link href="/approvals" className="text-accent underline underline-offset-2">
                    See all {waiting.data.total}
                  </Link>
                ) : null
              }
            >
              Waiting for approval
            </SectionTitle>
            {waiting.data.items.length ? (
              <StripList executions={waiting.data.items} />
            ) : (
              <EmptyState title="Nothing is waiting" />
            )}
          </section>

          <section>
            <SectionTitle
              aside={
                <Link href="/executions" className="text-accent underline underline-offset-2">
                  Full history
                </Link>
              }
            >
              Latest requests
            </SectionTitle>
            <StripList executions={latest.data.items} />
          </section>
        </div>
      )}

      <div className="mt-6">
        <StripLegend />
      </div>
    </>
  );
}
