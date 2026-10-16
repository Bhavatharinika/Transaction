import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { StatusBadge } from "../components/StatusBadge";
import { ErrorState, LoadingBlock } from "../components/StateViews";
import { Timeline } from "../components/Timeline";
import { usePolling } from "../hooks/usePolling";
import { ApiError, api } from "../services/api";
import { between, formatAmount, formatDateTime, formatDuration } from "../lib/format";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-ink-faint">{label}</dt>
      <dd className="mt-0.5 break-words text-sm">{children}</dd>
    </div>
  );
}

export function TransactionDetailPage() {
  const { id = "" } = useParams();
  const [terminal, setTerminal] = useState(false);
  const { data: tx, error, refresh } = usePolling(() => api.get(id), 2000, [id], !terminal);
  useEffect(() => setTerminal(tx?.status === "completed" || tx?.status === "failed"), [tx?.status]);

  const back = <Link to="/transactions" className="text-sm font-medium text-brand hover:underline">← All transactions</Link>;
  if (error && !tx) {
    const notFound = error instanceof ApiError && error.status === 404;
    return <div className="space-y-4">{back}<div className="card">{notFound
      ? <div className="p-6"><p className="font-medium">Transaction not found</p><p className="mt-1 text-sm text-ink-soft">No transaction exists with ID <span className="font-mono">{id}</span>.</p></div>
      : <ErrorState error={error} onRetry={refresh} />}</div></div>;
  }
  if (!tx) return <div className="space-y-4">{back}<div className="card"><LoadingBlock rows={6} label="Loading transaction" /></div></div>;

  return (
    <div className="space-y-5">
      {back}
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="break-all font-mono text-lg font-semibold">{tx.transactionId}</h1>
        <StatusBadge status={tx.status} />
        {!terminal && <span className="text-xs text-ink-faint">Updating live</span>}
      </div>

      {tx.status === "failed" && tx.lastError && (
        <div role="alert" className="rounded-lg border border-st-failed/30 bg-st-failed-bg p-4">
          <p className="text-sm font-semibold text-st-failed">Failed: {tx.lastError.code}</p>
          <p className="mt-1 text-sm text-ink">{tx.lastError.message}</p>
        </div>
      )}
      {tx.status !== "failed" && tx.lastError && (
        <div className="rounded-lg border border-st-pending/30 bg-st-pending-bg p-4">
          <p className="text-sm font-semibold text-st-pending">Last recoverable error: {tx.lastError.code}</p>
          <p className="mt-1 text-sm text-ink">{tx.lastError.message}</p>
          {tx.nextAttemptAt && <p className="mt-1 text-xs text-ink-soft">Next attempt at {formatDateTime(tx.nextAttemptAt)}</p>}
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-5">
        <section className="card p-5 lg:col-span-3" aria-label="Details">
          <h2 className="mb-4 text-sm font-semibold">Details</h2>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 md:grid-cols-3">
            <Field label="Amount"><span className="text-base font-semibold tabular-nums">{formatAmount(tx.amount, tx.currency)}</span></Field>
            <Field label="Currency">{tx.currency}</Field>
            <Field label="Retry count">{tx.retryCount}</Field>
            <Field label="Description">{tx.description || "—"}</Field>
            <Field label="Transaction timestamp">{formatDateTime(tx.timestamp)}</Field>
            <Field label="Processing time">{formatDuration(between(tx.processingStartedAt, tx.completedAt ?? tx.failedAt))}</Field>
            <Field label="Submitted">{formatDateTime(tx.submittedAt)}</Field>
            <Field label="Processing started">{formatDateTime(tx.processingStartedAt)}</Field>
            <Field label={tx.status === "failed" ? "Failed" : "Completed"}>{formatDateTime(tx.completedAt ?? tx.failedAt)}</Field>
          </dl>
          <h3 className="mb-2 mt-6 text-sm font-semibold">Metadata</h3>
          {Object.keys(tx.metadata).length ? <pre className="overflow-auto rounded-md bg-canvas p-3 font-mono text-xs">{JSON.stringify(tx.metadata, null, 2)}</pre> : <p className="text-sm text-ink-faint">None supplied</p>}
        </section>
        <section className="card p-5 lg:col-span-2" aria-label="Lifecycle">
          <h2 className="mb-4 text-sm font-semibold">Lifecycle</h2>
          <Timeline submittedAt={tx.submittedAt} events={tx.events} />
        </section>
      </div>
    </div>
  );
}
