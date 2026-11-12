"use client";

import useSWR from "swr";
import { ApprovalRequest } from "@/components/governance";
import { StripList } from "@/components/strip";
import { EmptyState, ErrorState, Loading, PageHeader, SectionTitle } from "@/components/ui";
import { plural } from "@/lib/format";
import type { ExecutionPage } from "@/lib/types";

export default function ApprovalsPage() {
  const waiting = useSWR<ExecutionPage>("/executions?status=PENDING_APPROVAL&limit=100", {
    refreshInterval: 5000,
  });
  const decided = useSWR<ExecutionPage>(
    "/executions?policy_effect=REQUIRE_APPROVAL&status=APPROVED&status=REJECTED&status=EXPIRED&status=EXECUTING&status=COMPLETED&status=FAILED&limit=25",
    { refreshInterval: 10_000 },
  );

  const error = waiting.error ?? decided.error;
  if (error) return <ErrorState error={error} />;
  if (!waiting.data || !decided.data) return <Loading />;

  return (
    <>
      <PageHeader title="Approval queue">
        Requests that policy will not run without a person saying yes. Open one to see exactly what
        the agent asked to change.
      </PageHeader>

      <section>
        <SectionTitle aside={plural(waiting.data.total, "request")}>Waiting</SectionTitle>
        {waiting.data.items.length ? (
          <ul className="flex flex-col gap-2">
            {waiting.data.items.map((execution) => (
              <ApprovalRequest key={execution.id} execution={execution} />
            ))}
          </ul>
        ) : (
          <EmptyState title="Nothing is waiting">
            Requests appear here when an agent asks for an operation whose policy is “needs
            approval”.
          </EmptyState>
        )}
      </section>

      <section className="mt-8">
        <SectionTitle>Decision history</SectionTitle>
        <p className="mb-3 text-[13px] text-muted">Includes approved actions that are running, completed or failed.</p>
        {decided.data.items.length ? (
          <StripList executions={decided.data.items} />
        ) : (
          <EmptyState title="No decisions yet" />
        )}
      </section>
    </>
  );
}
