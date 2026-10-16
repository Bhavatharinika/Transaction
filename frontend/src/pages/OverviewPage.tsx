import { useState } from "react";
import { Link } from "react-router-dom";
import { OutcomeChart, ProcessingTimeChart, QueueChart, RetryChart, ThroughputChart } from "../components/Charts";
import { ErrorState, LoadingBlock } from "../components/StateViews";
import { StatCard } from "../components/StatCard";
import { usePolling } from "../hooks/usePolling";
import { formatDuration, formatInt, formatPercent } from "../lib/format";
import { api } from "../services/api";

const WINDOWS = [30, 60, 120];

export function OverviewPage() {
  const stats = usePolling(api.stats, 3000);
  const health = usePolling(api.health, 5000);
  const [minutes, setMinutes] = useState(30);
  const series = usePolling(() => api.timeseries(minutes), 10000, [minutes]);
  const s = stats.data;
  const h = health.data;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Overview</h1>
          <p className="text-sm text-ink-soft">Live state of the transaction pipeline{stats.updatedAt ? ` · updated ${stats.updatedAt.toLocaleTimeString()}` : ""}</p>
        </div>
        <Link to="/system" className="text-sm font-medium text-brand hover:underline">View system health</Link>
      </div>

      {stats.error && !s ? (
        <div className="card"><ErrorState error={stats.error} onRetry={stats.refresh} /></div>
      ) : !s ? (
        <div className="card"><LoadingBlock rows={4} label="Loading statistics" /></div>
      ) : (
        <section aria-label="Key metrics" className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatCard label="Total transactions" value={formatInt(s.total)} />
          <StatCard label="Pending" value={formatInt(s.pending)} tone="warn" hint="Accepted, waiting for a worker or retry" />
          <StatCard label="Processing" value={formatInt(s.processing)} tone="info" hint="Includes verification after uncertain failures" />
          <StatCard label="Completed" value={formatInt(s.completed)} tone="good" />
          <StatCard label="Failed" value={formatInt(s.failed)} tone={s.failed ? "bad" : "neutral"} hint="Retries exhausted or rejected" />
          <StatCard label="Success rate" value={formatPercent(s.successRate)} tone="good" hint="Completed ÷ finished" />
          <StatCard label="Queue depth" value={formatInt(s.queueDepth ?? h?.queue.depth)} hint={h ? `Limit ${formatInt(h.queue.maxDepth)}` : undefined} />
          <StatCard label="Error rate" value={formatPercent(s.errorRate)} tone={s.errorRate ? "bad" : "neutral"} hint={`Posting-service errors: ${formatPercent(s.postingErrorRate)} (5 min)`} />
          <StatCard label="Avg processing time" value={formatDuration(s.avgProcessingSeconds)} hint="Last 5 min, claim → completion" />
          <StatCard label="Workers online" value={h ? `${h.workers.alive}` : "—"} hint={h ? `${h.workers.inFlight} of ${h.workers.capacity} slots busy` : undefined} />
          <StatCard label="Circuit breaker" value={h?.circuitBreaker.state ? h.circuitBreaker.state.replace("_", "-") : "—"} tone={h?.circuitBreaker.state === "open" ? "bad" : "neutral"} />
          <StatCard label="Completed (5 min)" value={formatInt(s.completedLast5m)} />
        </section>
      )}

      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold">Activity</h2>
        <div role="group" aria-label="Time window" className="inline-flex overflow-hidden rounded-md border border-line bg-white text-sm">
          {WINDOWS.map((w) => (
            <button key={w} aria-pressed={minutes === w} onClick={() => setMinutes(w)} className={`px-3 py-1.5 ${minutes === w ? "bg-brand text-white" : "text-ink-soft hover:bg-canvas"}`}>{w} min</button>
          ))}
        </div>
      </div>
      {series.error && !series.data ? (
        <div className="card"><ErrorState error={series.error} onRetry={series.refresh} /></div>
      ) : !series.data ? (
        <div className="card"><LoadingBlock rows={5} label="Loading charts" /></div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <ThroughputChart points={series.data.points} />
          <OutcomeChart points={series.data.points} />
          <QueueChart points={series.data.points} />
          <ProcessingTimeChart points={series.data.points} />
          <div className="lg:col-span-2"><RetryChart points={series.data.points} /></div>
        </div>
      )}
    </div>
  );
}
