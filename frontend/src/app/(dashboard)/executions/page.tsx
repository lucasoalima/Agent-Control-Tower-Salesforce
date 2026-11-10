"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import useSWR from "swr";
import { StripLegend, StripList } from "@/components/strip";
import { EmptyState, ErrorState, Loading, PageHeader } from "@/components/ui";
import { plural } from "@/lib/format";
import { STATUS_LABEL } from "@/lib/labels";
import type { Agent, ExecutionPage, ExecutionStatus } from "@/lib/types";

const PAGE_SIZE = 25;
const STATUSES = Object.keys(STATUS_LABEL) as ExecutionStatus[];

function History() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const statuses = params.getAll("status").filter((s): s is ExecutionStatus => s in STATUS_LABEL);
  const agentId = params.get("agent_id") ?? "";
  const q = params.get("q") ?? "";
  const offset = Math.max(0, Number(params.get("offset")) || 0);

  // Filters live in the URL so that a filtered view can be linked to and reloaded.
  function update(changes: Record<string, string | string[] | null>) {
    const next = new URLSearchParams(params);
    next.delete("offset");
    for (const [key, value] of Object.entries(changes)) {
      next.delete(key);
      if (Array.isArray(value)) value.forEach((v) => next.append(key, v));
      else if (value) next.set(key, value);
    }
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  }

  const [search, setSearch] = useState(q);
  useEffect(() => {
    if (search === q) return;
    const timer = setTimeout(() => update({ q: search || null }), 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- debounce on the typed value only
  }, [search]);

  const query = new URLSearchParams();
  statuses.forEach((s) => query.append("status", s));
  if (agentId) query.set("agent_id", agentId);
  if (q) query.set("q", q);
  query.set("limit", String(PAGE_SIZE));
  query.set("offset", String(offset));

  const page = useSWR<ExecutionPage>(`/executions?${query.toString()}`, {
    refreshInterval: 5000,
    keepPreviousData: true,
  });
  const agents = useSWR<Agent[]>("/agents");
  const filtered = statuses.length > 0 || Boolean(agentId) || Boolean(q);

  const control = "rounded-sm border border-line bg-raised px-3 py-1.5 text-ink";

  return (
    <>
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <label className="min-w-56 flex-1">
          <span className="mb-1 block text-[13px] text-muted">
            Search by agent, operation, environment, correlation ID or action hash
          </span>
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            className={`${control} w-full`}
          />
        </label>
        <label>
          <span className="mb-1 block text-[13px] text-muted">Agent</span>
          <select
            value={agentId}
            onChange={(event) => update({ agent_id: event.target.value || null })}
            className={control}
          >
            <option value="">All agents</option>
            {agents.data?.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <fieldset className="mb-5">
        <legend className="mb-1 text-[13px] text-muted">Status</legend>
        <div className="flex flex-wrap gap-1.5">
          {STATUSES.map((status) => {
            const on = statuses.includes(status);
            return (
              <button
                key={status}
                type="button"
                aria-pressed={on}
                onClick={() =>
                  update({ status: on ? statuses.filter((s) => s !== status) : [...statuses, status] })
                }
                className={`cursor-pointer rounded-sm border px-2.5 py-1 text-[13px] ${
                  on ? "border-ink bg-ink text-ground" : "border-line bg-surface hover:bg-raised"
                }`}
              >
                {STATUS_LABEL[status]}
              </button>
            );
          })}
        </div>
      </fieldset>

      {page.error ? (
        <ErrorState error={page.error} />
      ) : !page.data ? (
        <Loading />
      ) : page.data.items.length === 0 ? (
        <EmptyState title={filtered ? "No requests match these filters" : "No requests recorded yet"}>
          {filtered ? (
            <button
              type="button"
              onClick={() => {
                setSearch("");
                router.replace(pathname);
              }}
              className="cursor-pointer text-accent underline underline-offset-2"
            >
              Clear filters
            </button>
          ) : (
            <>
              Send the demo traffic with <code className="font-mono">make simulate</code>.
            </>
          )}
        </EmptyState>
      ) : (
        <>
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-3">
            <p className="text-[13px] text-muted" role="status">
              Showing {offset + 1} to {offset + page.data.items.length} of{" "}
              {plural(page.data.total, "request")}
            </p>
            <StripLegend />
          </div>
          <StripList executions={page.data.items} />
          <nav aria-label="Pages" className="mt-4 flex gap-2">
            <button
              type="button"
              disabled={offset === 0}
              onClick={() => update({ offset: String(Math.max(0, offset - PAGE_SIZE)) })}
              className={`${control} cursor-pointer disabled:cursor-default disabled:opacity-50`}
            >
              Newer
            </button>
            <button
              type="button"
              disabled={offset + PAGE_SIZE >= page.data.total}
              onClick={() => update({ offset: String(offset + PAGE_SIZE) })}
              className={`${control} cursor-pointer disabled:cursor-default disabled:opacity-50`}
            >
              Older
            </button>
          </nav>
        </>
      )}
    </>
  );
}

export default function ExecutionsPage() {
  return (
    <>
      <PageHeader title="Execution history">
        One record per tool request, whether it ran, failed, is waiting or was blocked.
      </PageHeader>
      <Suspense fallback={<Loading />}>
        <History />
      </Suspense>
    </>
  );
}
