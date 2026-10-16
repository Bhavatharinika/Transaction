import type { ReactNode } from "react";
import { ApiError } from "../services/api";

export function LoadingBlock({ rows = 3, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-live="polite" className="space-y-3 p-5">
      <span className="sr-only">{label}…</span>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-4 animate-pulse rounded bg-canvas" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const api = error instanceof ApiError ? error : null;
  return (
    <div role="alert" className="flex flex-col items-start gap-2 p-5">
      <p className="text-sm font-medium text-st-failed">{api?.status === 0 ? "API unreachable" : "Something went wrong"}</p>
      <p className="text-sm text-ink-soft">{error.message}</p>
      {api?.requestId && <p className="font-mono text-xs text-ink-faint">Request ID: {api.requestId}</p>}
      {onRetry && <button className="btn-secondary mt-1" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-1 px-5 py-12 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {hint && <p className="max-w-sm text-sm text-ink-soft">{hint}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}
