import type {
  Health, NewTransaction, PublicStatus, SortKey, Stats, SubmitResult, TimeseriesResponse, TransactionDetail, TransactionPage,
} from "../types/api";

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code: string, readonly requestId?: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers: { Accept: "application/json", ...init?.headers } });
  } catch {
    throw new ApiError("Cannot reach the API. Check that the backend is running.", 0, "NETWORK_ERROR");
  }
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const err = (body as { error?: { code?: string; message?: string; requestId?: string } } | null)?.error;
    throw new ApiError(err?.message ?? `Request failed (${response.status})`, response.status, err?.code ?? "HTTP_ERROR", err?.requestId);
  }
  return body as T;
}

export interface ListParams { status?: PublicStatus; q?: string; limit: number; offset: number; sort: SortKey; order: "asc" | "desc" }

export const api = {
  stats: () => request<Stats>("/api/stats"),
  // /api/health answers 503 when the database is down; the body is still the health report we want to show.
  health: async (): Promise<Health> => {
    const r = await fetch("/api/health").catch(() => null);
    if (!r) throw new ApiError("Cannot reach the API. Check that the backend is running.", 0, "NETWORK_ERROR");
    return (await r.json()) as Health;
  },
  timeseries: (minutes: number) => request<TimeseriesResponse>(`/api/stats/timeseries?minutes=${minutes}`),
  list: (p: ListParams) => {
    const qs = new URLSearchParams({ limit: String(p.limit), offset: String(p.offset), sort: p.sort, order: p.order });
    if (p.status) qs.set("status", p.status);
    if (p.q) qs.set("q", p.q);
    return request<TransactionPage>(`/api/transactions?${qs}`);
  },
  get: (id: string) => request<TransactionDetail>(`/api/transactions/${id}`),
  /** Returns the raw outcome (202 accepted, 200 idempotent replay, 409 conflict, 422, 503) for display. */
  submit: async (tx: NewTransaction): Promise<SubmitResult> => {
    const started = performance.now();
    const response = await fetch("/api/transactions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(tx) });
    return {
      httpStatus: response.status, elapsedMs: performance.now() - started,
      replay: response.headers.get("Idempotent-Replay") === "true", body: await response.json().catch(() => null),
    };
  },
};
