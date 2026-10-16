export type PublicStatus = "pending" | "processing" | "completed" | "failed";

export interface Stats {
  total: number; pending: number; processing: number; completed: number; failed: number;
  successRate: number | null; errorRate: number | null;
  avgProcessingSeconds: number | null; avgEndToEndSeconds: number | null;
  postingErrorRate: number | null; completedLast5m: number; queueDepth: number | null;
}

export interface WorkerInfo {
  workerId: string; startedAt: string; concurrency: number; inflight: number; processed: number;
  circuitBreaker: "closed" | "half_open" | "open"; draining: boolean; lastSeen: string;
}

export interface Health {
  status: "healthy" | "degraded" | "unhealthy";
  uptimeSeconds: number;
  components: {
    api: { status: string };
    database: { status: "up" | "down"; latencyMs?: number; error?: string };
    redis: { status: "up" | "down"; latencyMs?: number; error?: string };
  };
  queue: { depth: number | null; inFlight: number | null; maxDepth: number; status: string };
  workers: { alive: number; inFlight: number; capacity: number; instances: WorkerInfo[] };
  circuitBreaker: { state: "closed" | "half_open" | "open" | null };
  transactions: Partial<Record<"total" | "pending" | "processing" | "completed" | "failed", number>>;
  errorRates: { failedRatio5m: number | null; postingErrorRate5m: number | null };
}

export interface TransactionListItem {
  transactionId: string; amount: string; currency: string; description: string; status: PublicStatus;
  submittedAt: string; processingStartedAt: string | null; completedAt: string | null; retryCount: number;
}
export interface TransactionPage { items: TransactionListItem[]; total: number; limit: number; offset: number }

export interface TimelineEvent { type: string; at: string; details: Record<string, unknown> }
export interface ErrorInfo { code: string; message: string; at?: string | null }
export interface TransactionDetail {
  transactionId: string; status: PublicStatus; submittedAt: string; completedAt: string | null; error: string | null;
  amount: string; currency: string; description: string; timestamp: string; metadata: Record<string, unknown>;
  retryCount: number; processingStartedAt: string | null; failedAt: string | null; lastError: ErrorInfo | null;
  nextAttemptAt: string | null; createdAt: string; updatedAt: string; events: TimelineEvent[];
}

export interface SeriesPoint {
  time: string; submitted: number; completed: number; failed: number; retries: number;
  avgProcessingSeconds: number | null; queueDepth: number | null;
}
export interface TimeseriesResponse { minutes: number; points: SeriesPoint[] }

export interface NewTransaction {
  id: string; amount: number; currency: string; description: string; timestamp: string; metadata?: Record<string, unknown>;
}
export interface SubmitResult { httpStatus: number; elapsedMs: number; replay: boolean; body: unknown }

export type SortKey = "submittedAt" | "completedAt" | "amount" | "retryCount" | "status";
