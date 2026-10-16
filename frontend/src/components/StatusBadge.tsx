import type { PublicStatus } from "../types/api";

const STYLES: Record<PublicStatus, { box: string; dot: string; label: string }> = {
  pending: { box: "bg-st-pending-bg text-st-pending", dot: "bg-st-pending", label: "Pending" },
  processing: { box: "bg-st-processing-bg text-st-processing", dot: "bg-st-processing", label: "Processing" },
  completed: { box: "bg-st-completed-bg text-st-completed", dot: "bg-st-completed", label: "Completed" },
  failed: { box: "bg-st-failed-bg text-st-failed", dot: "bg-st-failed", label: "Failed" },
};

export function StatusBadge({ status }: { status: PublicStatus }) {
  const s = STYLES[status];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${s.box}`}>
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}
