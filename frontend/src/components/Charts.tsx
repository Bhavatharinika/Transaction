import type { ReactNode } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { SeriesPoint } from "../types/api";

const C = { submitted: "#5B6B82", completed: "#0F7A66", failed: "#A4282B", queue: "#0B5CAD", retries: "#9A6700" };
const AXIS = { fontSize: 12, fill: "#7A8699" };

interface Row extends SeriesPoint { label: string }
const toRows = (points: SeriesPoint[]): Row[] =>
  points.map((p) => ({ ...p, label: new Date(p.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) }));

function ChartCard({ title, subtitle, empty, children }: { title: string; subtitle: string; empty: boolean; children: ReactNode }) {
  return (
    <section className="card p-4" aria-label={title}>
      <h3 className="text-sm font-semibold">{title}</h3>
      <p className="mb-3 text-xs text-ink-faint">{subtitle}</p>
      <div className="h-56">
        {empty ? <div className="grid h-full place-items-center text-sm text-ink-faint">No activity in this window</div> : <ResponsiveContainer width="100%" height="100%">{children as never}</ResponsiveContainer>}
      </div>
    </section>
  );
}

const grid = <CartesianGrid stroke="#EEF0F4" vertical={false} />;
const tooltip = <Tooltip contentStyle={{ fontSize: 12, borderRadius: 6, borderColor: "#E2E6EC" }} />;
const xAxis = <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={false} minTickGap={24} />;
const yAxis = (unit?: string) => <YAxis tick={AXIS} tickLine={false} axisLine={false} width={36} allowDecimals={false} unit={unit} />;

export function ThroughputChart({ points }: { points: SeriesPoint[] }) {
  const rows = toRows(points);
  return (
    <ChartCard title="Transactions per minute" subtitle="Submitted vs completed" empty={rows.every((r) => !r.submitted && !r.completed)}>
      <AreaChart data={rows}>{grid}{xAxis}{yAxis()}{tooltip}<Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
        <Area type="monotone" dataKey="submitted" name="Submitted" stroke={C.submitted} fill={C.submitted} fillOpacity={0.12} strokeWidth={2} />
        <Area type="monotone" dataKey="completed" name="Completed" stroke={C.completed} fill={C.completed} fillOpacity={0.18} strokeWidth={2} />
      </AreaChart>
    </ChartCard>
  );
}

export function OutcomeChart({ points }: { points: SeriesPoint[] }) {
  const rows = toRows(points);
  return (
    <ChartCard title="Success vs failure" subtitle="Terminal outcomes per minute" empty={rows.every((r) => !r.completed && !r.failed)}>
      <BarChart data={rows}>{grid}{xAxis}{yAxis()}{tooltip}<Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="completed" name="Completed" stackId="o" fill={C.completed} />
        <Bar dataKey="failed" name="Failed" stackId="o" fill={C.failed} />
      </BarChart>
    </ChartCard>
  );
}

export function QueueChart({ points }: { points: SeriesPoint[] }) {
  const rows = toRows(points);
  return (
    <ChartCard title="Queue depth" subtitle="Peak Redis stream length per minute (sampled by workers)" empty={rows.every((r) => r.queueDepth == null)}>
      <LineChart data={rows}>{grid}{xAxis}{yAxis()}{tooltip}
        <Line type="monotone" dataKey="queueDepth" name="Queue depth" stroke={C.queue} strokeWidth={2} dot={false} connectNulls />
      </LineChart>
    </ChartCard>
  );
}

export function ProcessingTimeChart({ points }: { points: SeriesPoint[] }) {
  const rows = toRows(points).map((r) => ({ ...r, avg: r.avgProcessingSeconds == null ? null : Number(r.avgProcessingSeconds.toFixed(2)) }));
  return (
    <ChartCard title="Processing time" subtitle="Average seconds from first claim to completion" empty={rows.every((r) => r.avg == null)}>
      <LineChart data={rows}>{grid}{xAxis}{yAxis("s")}{tooltip}
        <Line type="monotone" dataKey="avg" name="Avg processing (s)" stroke={C.completed} strokeWidth={2} dot={false} connectNulls />
      </LineChart>
    </ChartCard>
  );
}

export function RetryChart({ points }: { points: SeriesPoint[] }) {
  const rows = toRows(points);
  return (
    <ChartCard title="Retry activity" subtitle="Retries scheduled per minute (confirmed pre-write failures and other retriable errors)" empty={rows.every((r) => !r.retries)}>
      <BarChart data={rows}>{grid}{xAxis}{yAxis()}{tooltip}
        <Bar dataKey="retries" name="Retries" fill={C.retries} />
      </BarChart>
    </ChartCard>
  );
}
