import { Notice } from "@/components/ui";
import { formatDateTime } from "@/lib/format";

export function ReconciliationNotice({ requiredAt }: { requiredAt: string | null }) {
  if (!requiredAt) return null;
  return (
    <div role="alert">
      <Notice>
        Outcome unknown. This execution needs reconciliation and will not be retried automatically.
        Detected at <time dateTime={requiredAt}>{formatDateTime(requiredAt)}</time>.
      </Notice>
    </div>
  );
}
