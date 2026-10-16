import { NavLink, Outlet } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { api } from "../services/api";

const NAV = [
  { to: "/", label: "Overview", end: true, d: "M3 12l9-9 9 9M5 10v10h14V10" },
  { to: "/transactions", label: "Transactions", end: false, d: "M4 6h16M4 12h16M4 18h10" },
  { to: "/system", label: "System", end: false, d: "M12 3v3m0 12v3M3 12h3m12 0h3M6 6l2 2m8 8l2 2M6 18l2-2m8-8l2-2" },
];

const HEALTH_STYLE = {
  healthy: ["bg-st-completed", "All systems operational"],
  degraded: ["bg-st-pending", "Degraded"],
  unhealthy: ["bg-st-failed", "Unhealthy"],
} as const;

function HealthPill() {
  const { data, error } = usePolling(api.health, 5000);
  const [dot, label] = error ? ["bg-st-failed", "API unreachable"] : data ? HEALTH_STYLE[data.status] : ["bg-ink-faint", "Checking…"];
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-line bg-white px-3 py-1 text-sm text-ink-soft" role="status">
      <span aria-hidden className={`h-2 w-2 rounded-full ${dot}`} />
      {label}
    </span>
  );
}

export function Layout() {
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside className="bg-navy text-white md:w-60 md:shrink-0">
        <div className="flex items-center gap-3 px-5 py-4 md:py-6">
          <span aria-hidden className="grid h-8 w-8 place-items-center rounded-md bg-brand text-sm font-semibold">TP</span>
          <div>
            <p className="text-sm font-semibold leading-tight">Transaction Processing</p>
            <p className="text-xs text-white/60">Operations console</p>
          </div>
        </div>
        <nav aria-label="Primary" className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:pb-0">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex items-center gap-3 whitespace-nowrap rounded-md px-3 py-2 text-sm transition-colors ${isActive ? "bg-navy-hover text-white" : "text-white/70 hover:bg-navy-hover hover:text-white"}`
              }
            >
              <svg aria-hidden width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={item.d} /></svg>
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="min-w-0 flex-1">
        <header className="flex items-center justify-end border-b border-line bg-white px-6 py-3">
          <HealthPill />
        </header>
        <main className="mx-auto max-w-7xl p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
