"use client";

import useSWR from "swr";
import { EmptyState, ErrorState, Loading, PageHeader, Tag, ToneLabel } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { KIND_LABEL, PROVIDER_LABEL, RISK_LABEL } from "@/lib/labels";
import type { Agent, Operation } from "@/lib/types";

function AgentCard({ agent, operations }: { agent: Agent; operations: Map<string, Operation> }) {
  const granted = agent.granted_operations.map(
    (key) => operations.get(key) ?? { key, title: key, risk: null },
  );
  return (
    <li className="rounded-sm border border-line bg-surface">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-b border-line px-4 py-3">
        <h2 className="text-base font-bold">{agent.name}</h2>
        {agent.status === "ACTIVE" ? (
          <ToneLabel tone="allow">Active</ToneLabel>
        ) : (
          <ToneLabel tone="muted">Disabled</ToneLabel>
        )}
      </div>

      <dl className="grid gap-x-8 gap-y-3 px-4 py-3 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <dt className="text-[13px] text-muted">Agent type</dt>
          <dd>{PROVIDER_LABEL[agent.provider]}</dd>
        </div>
        <div>
          <dt className="text-[13px] text-muted">Connected environment</dt>
          <dd className="flex flex-wrap items-center gap-x-2">
            {agent.environment.name}
            <Tag>{KIND_LABEL[agent.environment.kind]}</Tag>
            {agent.environment.is_mock ? <Tag>Simulated</Tag> : null}
          </dd>
        </div>
        <div>
          <dt className="text-[13px] text-muted">Last request</dt>
          <dd>{relativeTime(agent.last_seen_at)}</dd>
        </div>
        <div>
          <dt className="text-[13px] text-muted">Token starts with</dt>
          <dd className="font-mono text-[13px]">{agent.token_hint}</dd>
        </div>
      </dl>

      <div className="border-t border-line px-4 py-3">
        <p className="mb-1.5 text-[13px] text-muted">
          May request {granted.length} of {operations.size} operations. Policy then decides each
          request.
        </p>
        <ul className="flex flex-wrap gap-1.5">
          {granted.map((op) => (
            <li
              key={op.key}
              title={op.key}
              className="rounded-sm border border-line bg-raised px-2 py-0.5 text-[13px]"
            >
              {op.title}
              {op.risk ? <span className="text-muted"> ({RISK_LABEL[op.risk].toLowerCase()})</span> : null}
            </li>
          ))}
        </ul>
      </div>
    </li>
  );
}

export default function AgentsPage() {
  const agents = useSWR<Agent[]>("/agents", { refreshInterval: 10_000 });
  const operations = useSWR<Operation[]>("/operations");

  const error = agents.error ?? operations.error;
  if (error) return <ErrorState error={error} />;
  if (!agents.data || !operations.data) return <Loading />;

  const byKey = new Map(operations.data.map((op) => [op.key, op]));

  return (
    <>
      <PageHeader title="Agents">
        Each agent has its own token and is tied to one environment. It can only ask for the
        operations listed here, and it never holds credentials for the target system.
      </PageHeader>
      {agents.data.length ? (
        <ul className="flex flex-col gap-3">
          {agents.data.map((agent) => (
            <AgentCard key={agent.id} agent={agent} operations={byKey} />
          ))}
        </ul>
      ) : (
        <EmptyState title="No agents registered">
          Run <code className="font-mono">make seed</code> to create the demo agents, or register one
          with <code className="font-mono">POST /api/v1/agents</code>.
        </EmptyState>
      )}
    </>
  );
}
