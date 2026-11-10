"use client";

import useSWR from "swr";
import { StripList } from "@/components/strip";
import { EmptyState, ErrorState, Loading, Notice, PageHeader, SectionTitle } from "@/components/ui";
import { plural } from "@/lib/format";
import type { ExecutionPage } from "@/lib/types";

export default function ApprovalsPage() {
  const waiting = useSWR<ExecutionPage>("/executions?status=PENDING_APPROVAL&limit=100", {
    refreshInterval: 5000,
  });
  const decided = useSWR<ExecutionPage>(
    "/executions?status=APPROVED&status=REJECTED&status=EXPIRED&limit=25",
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

      <Notice>
        Approving and rejecting are not available in this version. A request listed here has not
        run and cannot run; it stays recorded with the exact payload the agent sent.
      </Notice>

      <section>
        <SectionTitle aside={plural(waiting.data.total, "request")}>Waiting</SectionTitle>
        {waiting.data.items.length ? (
          <StripList executions={waiting.data.items} />
        ) : (
          <EmptyState title="Nothing is waiting">
            Requests appear here when an agent asks for an operation whose policy is “needs
            approval”.
          </EmptyState>
        )}
      </section>

      <section className="mt-8">
        <SectionTitle>Decision history</SectionTitle>
        {decided.data.items.length ? (
          <StripList executions={decided.data.items} />
        ) : (
          <EmptyState title="No decisions yet" />
        )}
      </section>
    </>
  );
}
