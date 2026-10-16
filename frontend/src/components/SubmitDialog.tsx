import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import type { NewTransaction, SubmitResult } from "../types/api";

const CURRENCIES = ["USD", "EUR", "GBP", "JPY", "INR", "AUD", "CAD", "CHF"];
const OUTCOME: Record<number, { text: string; cls: string }> = {
  202: { text: "202 Accepted — queued for processing", cls: "text-st-completed" },
  200: { text: "200 Duplicate — returned the existing transaction (nothing new created)", cls: "text-st-processing" },
  409: { text: "409 Conflict — same ID, different payload", cls: "text-st-failed" },
  422: { text: "422 Validation error", cls: "text-st-failed" },
  503: { text: "503 Overloaded — retry later", cls: "text-st-pending" },
};

export function SubmitDialog({ onClose, onSubmitted }: { onClose: () => void; onSubmitted: () => void }) {
  const [id, setId] = useState<string>(() => crypto.randomUUID());
  const [amount, setAmount] = useState("125.00");
  const [currency, setCurrency] = useState("USD");
  const [description, setDescription] = useState("Demo payment");
  const [result, setResult] = useState<SubmitResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [networkError, setNetworkError] = useState<string | null>(null);
  // Keeps the timestamp stable across replays so "same payload" really is the same payload.
  const timestamp = useRef<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const send = async (override?: Partial<NewTransaction>) => {
    setBusy(true); setNetworkError(null);
    timestamp.current ??= new Date().toISOString();
    const payload: NewTransaction = { id, amount: Number(amount), currency, description, timestamp: timestamp.current, ...override };
    try { setResult(await api.submit(payload)); onSubmitted(); }
    catch { setNetworkError("Cannot reach the API."); }
    finally { setBusy(false); }
  };
  const outcome = result ? OUTCOME[result.httpStatus] ?? { text: `HTTP ${result.httpStatus}`, cls: "text-ink" } : null;

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-labelledby="submit-title" className="card max-h-[90vh] w-full max-w-lg overflow-y-auto p-6 shadow-xl">
        <div className="flex items-start justify-between">
          <div>
            <h2 id="submit-title" className="text-lg font-semibold">Submit a transaction</h2>
            <p className="text-sm text-ink-soft">Sends a real request to POST /api/transactions.</p>
          </div>
          <button className="btn-secondary px-2 py-1" onClick={onClose} aria-label="Close dialog">✕</button>
        </div>
        <div className="mt-5 grid gap-4">
          <label className="grid gap-1 text-sm">Transaction ID (idempotency key)
            <div className="flex gap-2">
              <input className="input font-mono text-xs" value={id} onChange={(e) => setId(e.target.value)} />
              <button className="btn-secondary shrink-0" onClick={() => { setId(crypto.randomUUID()); setResult(null); timestamp.current = null; }}>New ID</button>
            </div>
          </label>
          <div className="grid grid-cols-3 gap-3">
            <label className="col-span-2 grid gap-1 text-sm">Amount
              <input className="input" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
            </label>
            <label className="grid gap-1 text-sm">Currency
              <select className="input" value={currency} onChange={(e) => setCurrency(e.target.value)}>{CURRENCIES.map((c) => <option key={c}>{c}</option>)}</select>
            </label>
          </div>
          <label className="grid gap-1 text-sm">Description
            <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>
        </div>
        <div className="mt-5 flex flex-wrap gap-2">
          <button className="btn-primary" disabled={busy} onClick={() => void send()}>{result ? "Submit again (same ID)" : "Submit"}</button>
          {result && <button className="btn-secondary" disabled={busy} onClick={() => void send({ amount: Number(amount) + 1 })}>Resubmit with changed amount</button>}
        </div>
        {networkError && <p role="alert" className="mt-4 text-sm text-st-failed">{networkError}</p>}
        {result && outcome && (
          <div className="mt-5 rounded-md border border-line bg-canvas p-4" role="status">
            <p className={`text-sm font-medium ${outcome.cls}`}>{outcome.text}</p>
            <p className="mt-1 text-xs text-ink-soft">Responded in {result.elapsedMs.toFixed(0)} ms{result.replay ? " · Idempotent-Replay: true" : ""}</p>
            <pre className="mt-3 max-h-40 overflow-auto rounded bg-white p-3 font-mono text-xs">{JSON.stringify(result.body, null, 2)}</pre>
            {(result.httpStatus === 202 || result.httpStatus === 200) && <Link className="mt-3 inline-block text-sm font-medium text-brand hover:underline" to={`/transactions/${id}`} onClick={onClose}>Open transaction details</Link>}
          </div>
        )}
      </div>
    </div>
  );
}
