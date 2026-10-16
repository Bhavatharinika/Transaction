import type { ReactNode } from "react";

const TONES = { neutral: "text-ink", good: "text-st-completed", warn: "text-st-pending", bad: "text-st-failed", info: "text-st-processing" } as const;

export function StatCard({ label, value, hint, tone = "neutral" }: { label: string; value: ReactNode; hint?: string; tone?: keyof typeof TONES }) {
  return (
    <div className="card p-4">
      <p className="text-sm text-ink-soft">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums ${TONES[tone]}`}>{value}</p>
      {hint && <p className="mt-1 text-xs text-ink-faint">{hint}</p>}
    </div>
  );
}
