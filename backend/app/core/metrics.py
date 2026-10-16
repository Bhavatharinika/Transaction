"""Prometheus metrics. The API runs multi-process, so gauges declare a multiprocess mode."""
import os

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest, multiprocess

HTTP_REQUESTS = Counter("tps_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "tps_http_request_duration_seconds", "API latency", ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
TX_ACCEPTED = Counter("tps_transactions_accepted_total", "Submissions by outcome", ["outcome"])
ENQUEUE_FAILURES = Counter("tps_enqueue_failures_total", "API-side Redis enqueue failures (rows stay durable)")
TX_COMPLETED = Counter("tps_transactions_completed_total", "Transactions completed", ["via"])
TX_FAILED = Counter("tps_transactions_failed_total", "Transactions permanently failed", ["code"])
TX_PROCESSING_SECONDS = Histogram(
    "tps_transaction_processing_seconds", "First claim to completion",
    buckets=(1, 2, 5, 10, 15, 20, 30, 60, 120, 300),
)
RETRIES_SCHEDULED = Counter("tps_retries_scheduled_total", "Retries scheduled", ["reason"])
VERIFICATIONS = Counter("tps_verifications_total", "GET verifications after uncertain POST", ["result"])
MOCK_REQUESTS = Counter("tps_mock_requests_total", "Calls to the posting service", ["operation", "outcome"])
MOCK_LATENCY = Histogram(
    "tps_mock_request_duration_seconds", "Posting service latency", ["operation"],
    buckets=(0.05, 0.25, 1, 2, 4, 6, 8, 10, 12, 15),
)
BREAKER_STATE = Gauge("tps_circuit_breaker_state", "0=closed 1=half_open 2=open", multiprocess_mode="max")
BREAKER_TRIPS = Counter("tps_circuit_breaker_trips_total", "Times the breaker opened")
QUEUE_DEPTH = Gauge("tps_queue_depth", "Redis stream length (backlog + in-flight)", multiprocess_mode="max")
OPEN_TRANSACTIONS = Gauge("tps_open_transactions", "pending+processing+verifying", multiprocess_mode="max")
WORKER_INFLIGHT = Gauge("tps_worker_inflight", "Transactions being processed", multiprocess_mode="livesum")
LEASES_LOST = Counter("tps_leases_lost_total", "Stale-worker writes rejected by compare-and-swap")
LEASES_REAPED = Counter("tps_leases_reaped_total", "Expired leases recovered", ["from_status"])
DISPATCHED = Counter("tps_dispatched_total", "Rows pushed to the stream by the dispatcher")
CLAIMS = Counter("tps_claims_total", "Claim attempts", ["result"])


def render_metrics() -> tuple[bytes, str]:
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry), CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST
