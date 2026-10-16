import type { ReactNode } from "react";
import { ErrorState, LoadingBlock } from "../components/StateViews";
import { usePolling } from "../hooks/usePolling";
import { formatDateTime, formatInt, formatPercent, formatUptime } from "../lib/format";
import { api } from "../services/api";

function Tile({ title, status, ok, children }: { title: string; status: string; ok: boolean | "warn"; children?: ReactNode }) {
  const dot = ok === true ? "bg-st-completed" : ok === "warn" ? "bg-st-pending" : "bg-st-failed";
  return (
    <section className="card p-4" aria-label={title}>
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">{title}</h2>
        <span className="inline-flex items-center gap-2 text-sm text-ink-soft"><span aria-hidden className={`h-2 w-2 rounded-full ${dot}`} />{status}</span>
      </div>
      <div className="mt-3 space-y-1 text-sm text-ink-soft">{children}</div>
    </section>
  );
}

export function SystemPage() {
  const { data: h, error, refresh, updatedAt } = usePolling(api.health, 3000);
  if (error && !h) return <div className="card"><ErrorState error={error} onRetry={refresh} /></div>;
  if (!h) return <div className="card"><LoadingBlock rows={5} label="Loading health" /></div>;
  const { database: db, redis } = h.components;
  const breaker = h.circuitBreaker.state;
  const fill = h.queue.depth != null ? Math.min(100, (h.queue.depth / h.queue.maxDepth) * 100) : 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">System health</h1>
        <p className="text-sm text-ink-soft">Overall: <strong className="text-ink">{h.status}</strong> · API uptime {formatUptime(h.uptimeSeconds)}{updatedAt ? ` · checked ${updatedAt.toLocaleTimeString()}` : ""}</p>
      </div>
      {error && <p role="alert" className="text-sm text-st-failed">Latest refresh failed ({error.message}); showing the last known state.</p>}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Tile title="API" status="Up" ok>
          <p>Accepting requests; submissions never wait on the posting service.</p>
        </Tile>
        <Tile title="PostgreSQL" status={db.status === "up" ? "Connected" : "Unreachable"} ok={db.status === "up"}>
          {db.latencyMs != null && <p>Round-trip {db.latencyMs} ms</p>}
          {db.error && <p>Error: {db.error}</p>}
          <p>Source of truth — every accepted transaction is stored here first.</p>
        </Tile>
        <Tile title="Redis" status={redis.status === "up" ? "Connected" : "Unreachable"} ok={redis.status === "up"}>
          {redis.latencyMs != null && <p>Round-trip {redis.latencyMs} ms</p>}
          {redis.status === "down" && <p>Accepted transactions stay safe in PostgreSQL and are delivered when Redis returns.</p>}
        </Tile>
        <Tile title="Queue" status={h.queue.status === "ok" ? "Healthy" : h.queue.status === "saturated" ? "Saturated" : "Unknown"} ok={h.queue.status === "ok" ? true : h.queue.status === "saturated" ? false : "warn"}>
          <p>Depth <strong className="text-ink">{formatInt(h.queue.depth)}</strong> of {formatInt(h.queue.maxDepth)} · in flight {formatInt(h.queue.inFlight)}</p>
          <div className="h-2 overflow-hidden rounded-full bg-canvas" role="progressbar" aria-label="Queue fill" aria-valuenow={Math.round(fill)} aria-valuemin={0} aria-valuemax={100}>
            <div className="h-full rounded-full bg-st-processing" style={{ width: `${fill}%` }} />
          </div>
          <p className="text-xs">New submissions are rejected with 503 once the limit is reached.</p>
        </Tile>
        <Tile title="Worker pool" status={`${h.workers.alive} online`} ok={h.workers.alive > 0 ? true : "warn"}>
          <p>{formatInt(h.workers.inFlight)} of {formatInt(h.workers.capacity)} processing slots busy</p>
          {h.workers.alive === 0 && <p>No worker heartbeat — transactions will wait in PostgreSQL until one starts.</p>}
        </Tile>
        <Tile title="Circuit breaker" status={breaker ? breaker.replace("_", "-") : "Unknown"} ok={breaker === "closed" ? true : breaker === "half_open" ? "warn" : breaker === "open" ? false : "warn"}>
          <p>Worst state across workers. While open, work is deferred without consuming retries.</p>
          <p>Posting-service error rate (5 min): <strong className="text-ink">{formatPercent(h.errorRates.postingErrorRate5m)}</strong></p>
          <p>Failed share of finished (5 min): <strong className="text-ink">{formatPercent(h.errorRates.failedRatio5m)}</strong></p>
        </Tile>
      </div>

      <section className="card overflow-hidden" aria-label="Workers">
        <h2 className="border-b border-line px-4 py-3 text-sm font-semibold">Workers</h2>
        {h.workers.instances.length === 0 ? <p className="p-5 text-sm text-ink-soft">No workers are reporting.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <caption className="sr-only">Worker instances</caption>
              <thead className="text-left text-ink-soft"><tr>{["Worker", "In flight", "Processed", "Breaker", "Started", "State"].map((c) => <th key={c} scope="col" className="px-4 py-2.5 font-medium">{c}</th>)}</tr></thead>
              <tbody className="divide-y divide-line">
                {h.workers.instances.map((w) => (
                  <tr key={w.workerId}>
                    <td className="px-4 py-2.5 font-mono text-xs">{w.workerId}</td>
                    <td className="px-4 py-2.5 tabular-nums">{w.inflight} / {w.concurrency}</td>
                    <td className="px-4 py-2.5 tabular-nums">{formatInt(w.processed)}</td>
                    <td className="px-4 py-2.5">{w.circuitBreaker.replace("_", "-")}</td>
                    <td className="px-4 py-2.5 text-ink-soft">{formatDateTime(w.startedAt)}</td>
                    <td className="px-4 py-2.5">{w.draining ? "Draining" : "Active"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <p className="text-xs text-ink-faint">Prometheus metrics are exposed at <span className="font-mono">/metrics</span> on the API and on each worker (port 9100).</p>
    </div>
  );
}
