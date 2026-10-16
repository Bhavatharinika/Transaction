import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { StatusBadge } from "../components/StatusBadge";
import { EmptyState, ErrorState, LoadingBlock } from "../components/StateViews";
import { SubmitDialog } from "../components/SubmitDialog";
import { useDebounced } from "../hooks/useDebounced";
import { usePolling } from "../hooks/usePolling";
import { between, formatAmount, formatDateTime, formatDuration, shortId } from "../lib/format";
import { api } from "../services/api";
import type { PublicStatus, SortKey } from "../types/api";

const PAGE_SIZE = 25;
const FILTERS: { value: PublicStatus | ""; label: string }[] = [
  { value: "", label: "All" }, { value: "pending", label: "Pending" }, { value: "processing", label: "Processing" },
  { value: "completed", label: "Completed" }, { value: "failed", label: "Failed" },
];

export function TransactionsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<PublicStatus | "">("");
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim(), 300);
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<SortKey>("submittedAt");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [creating, setCreating] = useState(false);

  const list = usePolling(
    () => api.list({ status: status || undefined, q: q || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE, sort, order }),
    3000, [status, q, page, sort, order],
  );
  const data = list.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  const toggleSort = (key: SortKey) => {
    if (sort === key) setOrder(order === "asc" ? "desc" : "asc");
    else { setSort(key); setOrder("desc"); }
    setPage(0);
  };
  const header = (key: SortKey, label: string, align = "text-left") => (
    <th scope="col" aria-sort={sort === key ? (order === "asc" ? "ascending" : "descending") : "none"} className={`px-4 py-3 font-medium ${align}`}>
      <button className="inline-flex items-center gap-1 hover:text-ink" onClick={() => toggleSort(key)}>
        {label}<span aria-hidden className="text-xs">{sort === key ? (order === "asc" ? "▲" : "▼") : ""}</span>
      </button>
    </th>
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Transactions</h1>
          <p className="text-sm text-ink-soft">{data ? `${data.total.toLocaleString()} matching` : "Loading…"}</p>
        </div>
        <button className="btn-primary" onClick={() => setCreating(true)}>New transaction</button>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="sr-only" htmlFor="tx-search">Search transactions</label>
        <input id="tx-search" className="input max-w-xs" type="search" placeholder="Search by ID, description or currency" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} />
        <div role="group" aria-label="Filter by status" className="inline-flex overflow-hidden rounded-md border border-line bg-white text-sm">
          {FILTERS.map((f) => (
            <button key={f.label} aria-pressed={status === f.value} onClick={() => { setStatus(f.value); setPage(0); }} className={`px-3 py-2 ${status === f.value ? "bg-brand text-white" : "text-ink-soft hover:bg-canvas"}`}>{f.label}</button>
          ))}
        </div>
      </div>

      <div className="card overflow-hidden">
        {list.error && !data ? <ErrorState error={list.error} onRetry={list.refresh} />
          : !data ? <LoadingBlock rows={6} label="Loading transactions" />
          : data.items.length === 0 ? (
            <EmptyState
              title={q || status ? "No transactions match these filters" : "No transactions yet"}
              hint={q || status ? "Try a different search or clear the status filter." : "Submit one to watch it move from pending to completed."}
              action={<button className="btn-primary" onClick={() => setCreating(true)}>New transaction</button>}
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-sm">
                <caption className="sr-only">Transactions</caption>
                <thead className="border-b border-line bg-canvas/60 text-ink-soft">
                  <tr>
                    <th scope="col" className="px-4 py-3 text-left font-medium">Transaction ID</th>
                    {header("amount", "Amount", "text-right")}
                    <th scope="col" className="px-4 py-3 text-left font-medium">Currency</th>
                    {header("status", "Status")}
                    {header("submittedAt", "Submitted")}
                    <th scope="col" className="px-4 py-3 text-left font-medium">Processing time</th>
                    {header("completedAt", "Completed")}
                    {header("retryCount", "Retries", "text-right")}
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {data.items.map((t) => (
                    <tr key={t.transactionId} tabIndex={0} className="cursor-pointer hover:bg-canvas/60"
                        onClick={() => navigate(`/transactions/${t.transactionId}`)}
                        onKeyDown={(e) => e.key === "Enter" && navigate(`/transactions/${t.transactionId}`)}>
                      <td className="px-4 py-3 font-mono text-xs" title={t.transactionId}>{shortId(t.transactionId)}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{formatAmount(t.amount, t.currency)}</td>
                      <td className="px-4 py-3">{t.currency}</td>
                      <td className="px-4 py-3"><StatusBadge status={t.status} /></td>
                      <td className="px-4 py-3 text-ink-soft">{formatDateTime(t.submittedAt)}</td>
                      <td className="px-4 py-3 tabular-nums text-ink-soft">{formatDuration(between(t.processingStartedAt, t.completedAt))}</td>
                      <td className="px-4 py-3 text-ink-soft">{formatDateTime(t.completedAt)}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{t.retryCount}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        {data && data.total > 0 && (
          <div className="flex items-center justify-between border-t border-line px-4 py-3 text-sm text-ink-soft">
            <span>Page {page + 1} of {pages}</span>
            <div className="flex gap-2">
              <button className="btn-secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
              <button className="btn-secondary" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          </div>
        )}
      </div>
      {creating && <SubmitDialog onClose={() => setCreating(false)} onSubmitted={list.refresh} />}
    </div>
  );
}
