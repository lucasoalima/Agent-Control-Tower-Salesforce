"use client";

import useSWR from "swr";
import { PolicyEditor } from "@/components/governance";
import { ErrorState, Loading, Notice, PageHeader } from "@/components/ui";
import { KIND_LABEL, RISK_LABEL } from "@/lib/labels";
import type { PolicyMatrix } from "@/lib/types";

export default function PoliciesPage() {
  const { data, error } = useSWR<PolicyMatrix>("/policies", { refreshInterval: 10_000 });
  if (error) return <ErrorState error={error} />;
  if (!data) return <Loading />;

  return (
    <>
      <PageHeader title="Policies">
        What the gateway does when an agent that holds the grant asks for each operation, by kind of
        environment. This table is computed by the same function that enforces it.
      </PageHeader>

      <Notice>
        Select an effect and save each change. Production guardrails are fixed and cannot be edited.
      </Notice>

      <div className="overflow-x-auto rounded-sm border border-line bg-surface">
        <table className="w-full min-w-[42rem] border-collapse text-left">
          <thead>
            <tr>
              <th scope="col" className="px-3 py-2 font-bold">
                Operation
              </th>
              {data.environment_kinds.map((kind) => (
                <th key={kind} scope="col" className="w-[21%] border-l border-line px-3 py-2 font-bold">
                  {KIND_LABEL[kind]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map(({ operation, cells }) => (
              <tr key={operation.key}>
                <th scope="row" className="border-t border-line px-3 py-2 align-top font-normal">
                  <p>
                    {operation.title}
                    <span className="text-muted"> ({RISK_LABEL[operation.risk].toLowerCase()})</span>
                  </p>
                  <p className="text-[13px] text-muted">{operation.key}</p>
                </th>
                {cells.map((cell) => (
                  <PolicyEditor
                    key={operation.key + cell.environment_kind + cell.configured + cell.effective}
                    operation={operation.key} title={operation.title} cell={cell}
                  />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
