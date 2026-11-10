"use client";

import useSWR from "swr";
import { ErrorState, Loading, Notice, PageHeader, ToneLabel } from "@/components/ui";
import { EFFECT_LABEL, EFFECT_TONE, KIND_LABEL, RISK_LABEL } from "@/lib/labels";
import type { PolicyCell, PolicyMatrix } from "@/lib/types";

function Cell({ cell }: { cell: PolicyCell }) {
  const overridden = cell.configured !== null && cell.configured !== cell.effective;
  return (
    <td className="border-t border-l border-line px-3 py-2 align-top">
      <ToneLabel tone={EFFECT_TONE[cell.effective]}>{EFFECT_LABEL[cell.effective]}</ToneLabel>
      {cell.reason === "NO_MATCHING_RULE" ? (
        <p className="text-[13px] text-muted">No rule, so blocked by default</p>
      ) : null}
      {cell.reason === "PRODUCTION_GUARDRAIL" ? (
        <p className="text-[13px] text-muted">
          Fixed guardrail
          {overridden && cell.configured
            ? `, overrides the “${EFFECT_LABEL[cell.configured].toLowerCase()}” rule`
            : ""}
        </p>
      ) : null}
    </td>
  );
}

export default function PoliciesPage() {
  const { data, error } = useSWR<PolicyMatrix>("/policies");
  if (error) return <ErrorState error={error} />;
  if (!data) return <Loading />;

  return (
    <>
      <PageHeader title="Policies">
        What the gateway does when an agent that holds the grant asks for each operation, by kind of
        environment. This table is computed by the same function that enforces it.
      </PageHeader>

      <Notice>
        Policies are read-only in this version. Anything that is not a read is always blocked on
        production environments, whatever the rules say.
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
                  <Cell key={cell.environment_kind} cell={cell} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
