import { formatTime } from "../lib/format";
import type { TimelineEvent } from "../types/api";

const LABELS: Record<string, { text: string; tone: "ok" | "bad" | "warn" | "info" | "neutral" }> = {
  submitted: { text: "Accepted and stored (HTTP 202)", tone: "neutral" },
  claimed: { text: "Worker claimed the transaction", tone: "info" },
  post_ok: { text: "Posting service accepted the POST", tone: "ok" },
  post_failed: { text: "POST failed — outcome uncertain, will verify before any retry", tone: "warn" },
  verification_scheduled: { text: "Verification scheduled after settle window", tone: "warn" },
  verification_rescheduled: { text: "Posting service unavailable — verification rescheduled", tone: "warn" },
  verify_found: { text: "Verification: record exists at posting service (post-write failure)", tone: "ok" },
  verify_missing: { text: "Verification: record absent (pre-write failure confirmed)", tone: "warn" },
  retry_scheduled: { text: "Retry scheduled with backoff", tone: "warn" },
  deferred: { text: "Deferred — circuit breaker open (no retry consumed)", tone: "warn" },
  lease_expired: { text: "Worker lease expired — recovering via verification", tone: "bad" },
  completed: { text: "Completed", tone: "ok" },
  failed: { text: "Failed permanently", tone: "bad" },
};
const DOT = { ok: "bg-st-completed", bad: "bg-st-failed", warn: "bg-st-pending", info: "bg-st-processing", neutral: "bg-ink-faint" };

export function Timeline({ submittedAt, events }: { submittedAt: string; events: TimelineEvent[] }) {
  const all: TimelineEvent[] = [{ type: "submitted", at: submittedAt, details: {} }, ...events];
  return (
    <ol className="relative ml-2 space-y-4 border-l border-line pl-5">
      {all.map((e, i) => {
        const meta = LABELS[e.type] ?? { text: e.type, tone: "neutral" as const };
        const details = Object.entries(e.details).filter(([, v]) => v !== null && v !== undefined);
        return (
          <li key={i} className="relative">
            <span aria-hidden className={`absolute -left-[26px] top-1.5 h-2.5 w-2.5 rounded-full ring-4 ring-white ${DOT[meta.tone]}`} />
            <p className="text-sm font-medium">{meta.text}</p>
            <p className="font-mono text-xs text-ink-faint">{formatTime(e.at)}</p>
            {details.length > 0 && (
              <p className="mt-0.5 text-xs text-ink-soft">{details.map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join("  ·  ")}</p>
            )}
          </li>
        );
      })}
    </ol>
  );
}
