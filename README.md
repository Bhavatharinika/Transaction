# High Performance Transaction Processing System

A reliable, idempotent intermediary in front of a slow, unreliable posting service. Designed to provide effectively-once downstream posting under the documented mock timing assumptions using at-least-once delivery, verification, and database-backed fencing. The API acknowledges a transaction in milliseconds (HTTP 202), stores it durably, and delivers it to the posting service asynchronously, even though the posting service has no idempotency support, takes 2–10 s per call and fails ~5% of the time, sometimes *after* it has already written the record.

> **Honesty note.** This repository contains unit and integration tests and measures actual behavior without invented performance statistics. **No benchmark numbers are fabricated**: §23 is a template to be filled from a real run on your hardware.

---

## 1. Problem statement

Clients need a fast, reliable way to post financial transactions. The only downstream is a mock posting service that:

* waits 2–10 s before writing, fails ~5% of requests, with failures **before** the write (row absent) *or* **after** the write (row present but an error returned);
* is **not idempotent**: two POSTs with the same id create two rows.

Target objectives: respond < 100 ms · ≥ 1000 tx/s accepted · zero duplicate postings · idempotent client API · status tracking · recovery analytically expected < 30 s · observability.

## 2. Solution overview

1. **Accept**: validate, `INSERT … ON CONFLICT DO NOTHING` into PostgreSQL (durable), `XADD` to a Redis Stream, return **202**.
2. **Deliver**: workers claim a transaction atomically (lease), `GET` the posting service, `POST` only if absent.
3. **Never blind-retry**: after an *uncertain* POST failure the transaction goes to an internal `verifying` state; after a settle window longer than the mock's write delay, a `GET` decides: found → completed, absent → safe retry with exponential backoff + jitter.
4. **Self-heal**: expired leases (crashed workers) → `verifying`; an outbox dispatcher re-queues anything missing from Redis; a circuit breaker parks work during outages without burning retries; backpressure returns 503 + `Retry-After` when saturated.

## 3. Architecture

```mermaid
flowchart LR
  C[Client / React dashboard] -->|HTTP| API[FastAPI x N processes]
  API -->|1. INSERT ON CONFLICT| PG[(PostgreSQL<br/>source of truth)]
  API -->|2. XADD| R[(Redis Stream<br/>consumer group)]
  R -->|XREADGROUP| W[Worker pool<br/>replicas x asyncio tasks]
  W -->|claim / CAS transitions| PG
  W -->|GET then POST| M[Mock Posting Service<br/>slow, unreliable, no idempotency]
  D[Dispatcher / outbox relay<br/>in every worker] -->|due + unqueued rows| PG
  D -->|XADD| R
  RP[Reaper<br/>expired leases] --> PG
  API -. /metrics /api/health .-> OBS[Prometheus / Dashboard]
  W -. heartbeat, /metrics :9100 .-> OBS
```

Single repo, one backend image, two entrypoints (`uvicorn` API and `python -m app.workers.runner`), a React SPA behind nginx.

## 4. Request lifecycle

```mermaid
sequenceDiagram
  participant Cl as Client
  participant A as API
  participant P as PostgreSQL
  participant Q as Redis Stream
  participant W as Worker
  participant M as Posting service
  Cl->>A: POST /api/transactions
  A->>P: INSERT ... ON CONFLICT DO NOTHING
  A->>Q: XADD (failure tolerated)
  A-->>Cl: 202 {transactionId, status: pending}
  Q->>W: XREADGROUP
  W->>P: claim (UPDATE ... WHERE due AND no live lease)
  W->>M: GET /transactions/{id}
  alt exists
    W->>P: CAS -> completed
  else absent
    W->>M: POST /transactions
    alt 2xx
      W->>P: CAS -> completed
    else uncertain failure (timeout / reset / 5xx)
      W->>P: CAS -> verifying (next_attempt_at = POST start + settle)
      Note over W,M: later, after the mock's 2-10s write window
      W->>M: GET /transactions/{id}
      alt found (post-write failure)
        W->>P: CAS -> completed
      else absent (pre-write failure)
        W->>P: CAS -> pending, retry_count+1, backoff
      end
    end
  end
  W->>Q: XACK + XDEL (only after the DB write)
```

### State machine

```mermaid
stateDiagram-v2
  [*] --> pending
  pending --> processing: claim
  processing --> completed: GET found / POST 2xx
  processing --> verifying: uncertain POST failure, or worker lease expired
  processing --> pending: retry (backoff) or circuit-open deferral
  processing --> failed: permanent 4xx / retries exhausted
  verifying --> completed: GET found
  verifying --> pending: GET absent, retry
  verifying --> verifying: GET unavailable / breaker open
  verifying --> failed: verification exhausted
  completed --> [*]
  failed --> [*]
```

`verifying` is internal; the API reports it as `processing`. Transitions are validated in one pure function (`services/state_machine.py`) **and** enforced in SQL by compare-and-swap.

## 5. Idempotency strategy (client duplicates)

The client's `id` is the primary key and idempotency key.

| Scenario | Result |
|---|---|
| First submission | `202`, row inserted, one stream message |
| Same id, same payload (numeric/zone-equivalent forms count as same) | `200` + `Idempotent-Replay: true`, existing status returned, nothing created or queued |
| Same id, different payload | `409 IDEMPOTENCY_CONFLICT` |
| 25 concurrent identical submissions | exactly one `202`, 24 × `200`, one row (tested) |

Mechanism: `INSERT … ON CONFLICT (id) DO NOTHING RETURNING` is atomic; concurrent inserts serialise on the PK index. A SHA-256 of a **canonical** payload (normalised decimal, UTC timestamp, sorted metadata) detects differing bodies.

## 6. Duplicate prevention at the posting service

Three independent guards:

1. **One owner at a time**: `claim` is a single `UPDATE … WHERE status IN (pending, verifying) AND next_attempt_at <= now() AND (lease IS NULL OR lease expired)`. Under READ COMMITTED the losing contender re-evaluates the WHERE after the winner commits and matches zero rows.
2. **GET before POST, always**.
3. **No re-POST after uncertainty** without a settled GET (next section).

## 7. Failure handling

Why the naive "POST failed → GET → retry" is unsafe: the mock sleeps 2–10 s **before** writing. An immediate GET after a timeout can say "absent" while the write is still pending; the retry then creates a duplicate. This design therefore:

* uses a POST timeout (15 s) **above** the mock's worst-case latency;
* after an uncertain failure schedules verification at `POST start + VERIFICATION_SETTLE_SECONDS` (12 s default, > 10 s);
* only a GET that is *still absent after the settle window* counts as a pre-write failure.

| Failure | Classification | Behaviour |
|---|---|---|
| Connection refused / connect timeout / pool timeout / HTTP 429 | `NOT_WRITTEN` | retry with backoff (nothing could have been written) |
| Read timeout, connection reset, HTTP 5xx, HTTP 409 | `UNCERTAIN` | → `verifying`, GET after settle; **no re-POST** |
| HTTP 4xx (other) | `REJECTED` | `failed` (`POSTING_REJECTED`), no retry |
| GET fails | n/a | retry (attempt) / reschedule verification (verifying); a failed GET never proves absence |
| Worker crash mid-flight | lease expiry | reaper → `verifying` (never straight to POST) |
| Retries exhausted | n/a | `failed`, `last_error` `{code: RETRIES_EXHAUSTED, message, at}` |
| Verification exhausted | n/a | `failed` `VERIFICATION_EXHAUSTED` with an explicit "manual reconciliation required" message (the outcome genuinely is unknown) |

## 8. Race-condition handling

* **Double processing / duplicate POST**: atomic claim (above). Test: three workers race on one transaction → one `completed`, two `skipped`, exactly one POST.
* **Lost updates / stale workers**: every transition is `UPDATE … WHERE id AND lease_owner = me AND status = s AND version = v`; a reaped-and-reclaimed worker's write affects 0 rows. Test: stale worker cannot overwrite a newer `completed`.
* **Conflicting state**: allowed transitions are explicit; invalid ones raise before SQL runs.
* **Lease renewal**: one batched `UPDATE … WHERE lease_owner = me` per worker every 3 s (not one per transaction).
* **Reaper vs. live worker**: only rows with an *expired* lease are reaped; the settle window after reaping exceeds the mock's max write latency, so a delayed original POST cannot land after verification.
* **Clock skew**: all lease/schedule comparisons use the database clock.

## 9. Queue design (Redis Streams)

* Consumer group, `XREADGROUP` → process → `XACK`+`XDEL` **after** the DB transition (at-least-once). Redelivered messages are harmless no-ops because the claim is the single point of mutual exclusion.
* `XAUTOCLAIM` takes over messages from dead consumers.
* **Redis is not the source of truth.** The *dispatcher* (in every worker; `FOR UPDATE SKIP LOCKED`, so safe concurrently) relays any due row that is un-queued or whose message is older than `REQUEUE_AFTER_SECONDS` and still unfinished (covers Redis flush/loss).
* **Delayed work** (retry backoff, verification settle) is scheduled in PostgreSQL (`next_attempt_at`) rather than a Redis ZSET: one fewer consistency problem, and delays survive a Redis loss. The trade-off is a ≤ 1 s dispatch granularity.
* **Redis down**: the API still returns 202 (row durable, `enqueued_at` cleared) and skips Redis for 2 s after a failure so requests stay fast. Processing pauses until Redis returns; nothing is lost. *(Workers do not process directly from PostgreSQL while Redis is down. Honest limitation.)*
* **Graceful shutdown**: stop intake, drain in-flight work up to `SHUTDOWN_GRACE_SECONDS`, then abandon the rest to lease recovery.

## 10. Worker design

Each worker process = asyncio tasks: consumer, autoclaim, dispatcher, reaper, lease keeper, heartbeat (TTL'd registration in Redis → health endpoint), sampler (dashboard time series). Concurrency per process = `WORKER_CONCURRENCY`; scale out with `WORKER_REPLICAS` (`docker compose up --scale worker=N`). Loops survive Redis/DB outages (logged, retried).

## 11. Retry strategy

`delay(n) = min(max_delay, initial × 2^(n−1)) + base × jitter_ratio × U(0,1)`; defaults 1 s, 60 s, 20 %, `RETRY_MAX_RETRIES=5` (1 attempt + 5 retries). Additive jitter never shortens backoff. `retry_count` is stored and shown. Exhaustion → `failed` with structured error. Circuit-open deferrals and crash recovery do not increment retries.

## 12. Circuit breaker

Per worker process, rolling window (default last 20 calls, ≥ 10 calls, ≥ 50 % failures → OPEN for 15 s → HALF_OPEN with 3 probes; any probe failure re-opens, all succeed closes). 4xx/409 responses prove the service is alive and do not count as failures. While open, work is deferred (`pending`/`verifying`, `next_attempt_at` = breaker retry-after, **no retry consumed**), so an outage never fails transactions. State is exported as a metric and in `/api/health`. *Per-process* state is a deliberate simplification (no Redis round-trip per call; each worker converges independently).

## 13. Backpressure

A background gauge (per API process, 1 s) tracks Redis stream length and open (pending/processing/verifying) rows. When `QUEUE_MAX_DEPTH` or `MAX_OPEN_TRANSACTIONS` is reached, **new** submissions get `503 OVERLOADED` + `Retry-After`; accepted work is never dropped. The check uses cached gauges plus a local accepted-since-refresh counter, so it is O(1) on the hot path (a soft limit: bursts can overshoot slightly between refreshes). Rate limiting per client is **not** implemented (see §29).

---

## Capacity reality check (read before benchmarking)

Each in-flight POST occupies a worker slot for 2–10 s (mean ≈ 6 s). Sustained *completion* throughput ≈ `slots / 6 s`: default 2 workers × 150 slots ≈ 50 tx/s. To complete 1000 tx/s you need ≈ 6000 concurrent slots **and** a posting service that tolerates that concurrency (unknown). The **acceptance** path (1000+/s, < 100 ms) is independent of this: the queue absorbs the difference up to the backpressure limit. The load script reports the two rates separately. Tune `WORKER_REPLICAS`, `WORKER_CONCURRENCY`, `MOCK_MAX_CONNECTIONS`.

## 14. Database schema

`transactions`

| Column | Why |
|---|---|
| `id uuid PK` | client id = idempotency key = posting-service id |
| `amount numeric(20,4) CHECK > 0`, `currency char(3) CHECK ^[A-Z]{3}$`, `description`, `timestamp`, `metadata jsonb` | the payload |
| `payload_hash` | detect same-id/different-payload (409) |
| `status` (CHECK pending/processing/verifying/completed/failed) | state machine |
| `retry_count` | backoff + exhaustion |
| `next_attempt_at` | durable delay scheduler |
| `lease_owner`, `lease_expires_at` | exclusive ownership + crash recovery |
| `version` | optimistic CAS, stale-worker protection |
| `enqueued_at` | outbox flag / re-queue timer |
| `submitted_at`, `processing_started_at`, `completed_at`, `failed_at`, `created_at`, `updated_at` | lifecycle timestamps |
| `last_error jsonb` | `{code, message, at}` |

Indexes: `status`; partial `next_attempt_at WHERE status IN (pending, verifying)` (dispatcher); partial `lease_expires_at` and `lease_owner WHERE lease_owner IS NOT NULL` (reaper, heartbeat); `submitted_at DESC, id` (listing); partial `completed_at`/`failed_at` (time series).

`transaction_events`: append-only audit trail written in the **same DB transaction** as each state change (powers the details timeline, retry chart and posting-error rate). `metric_samples`: 5-second gauge snapshots (queue depth chart); one writer per bucket cluster-wide via PK dedup. Schema is managed by **Alembic** (`backend/alembic`); the migration job runs before API/workers.

## 15. API documentation

OpenAPI/Swagger: `http://localhost:8000/docs`. Errors always use `{"error": {"code", "message", "requestId", "details?"}}`; every response carries `X-Request-ID` and `X-Response-Time-Ms`.

```bash
# Submit (202)
curl -i -X POST http://localhost:8000/api/transactions -H 'Content-Type: application/json' -d '{
  "id": "123e4567-e89b-12d3-a456-426614174000", "amount": 100.50, "currency": "USD",
  "description": "Test transaction", "timestamp": "2023-01-15T10:30:00Z", "metadata": {"order": "A-1"}}'
# -> {"transactionId":"123e4567-...","status":"pending","submittedAt":"2025-..."}

# Same request again -> 200 + Idempotent-Replay: true.  Different amount -> 409 IDEMPOTENCY_CONFLICT.

# Status
curl http://localhost:8000/api/transactions/123e4567-e89b-12d3-a456-426614174000
# -> {"transactionId":..,"status":"completed","submittedAt":..,"completedAt":..,"error":null,
#     "amount":"100.5000","currency":"USD","retryCount":0,"lastError":null,"events":[...], ...}

# List (filters: status, q, limit<=200, offset, sort=submittedAt|completedAt|amount|retryCount|status, order)
curl 'http://localhost:8000/api/transactions?status=failed&q=usd&limit=25&offset=0'

curl http://localhost:8000/api/health            # 200 (503 if PostgreSQL is down)
curl http://localhost:8000/api/stats
curl 'http://localhost:8000/api/stats/timeseries?minutes=30'
curl http://localhost:8000/metrics               # Prometheus text
```

| Code | Meaning |
|---|---|
| 202 | accepted for async processing |
| 200 | idempotent replay of an existing transaction |
| 404 `TRANSACTION_NOT_FOUND` | unknown id |
| 409 `IDEMPOTENCY_CONFLICT` | same id, different payload |
| 422 `VALIDATION_ERROR` | invalid body/path/query (field-level `details`) |
| 503 `OVERLOADED` / `DATABASE_UNAVAILABLE` | backpressure / storage outage, with `Retry-After` |

Validation: UUID id; amount finite, > 0, ≤ 4 decimals, ≤ 20 digits; currency must be an active ISO 4217 code (case-insensitive input, stored upper-case); ISO 8601 timestamp (naive = UTC); description ≤ 1000 chars; metadata ≤ 8 KB.

## 16. Frontend

React + TypeScript + Vite + Tailwind (+ Recharts for charts, React Router). Every number comes from the API; nothing is fabricated.

* **Overview**: totals, pending/processing/completed/failed, success & error rate, queue depth, average processing time, workers, breaker; charts (throughput, success vs failure, queue depth, processing time, retries) over 30/60/120 min.
* **Transactions**: search (id prefix / description / currency), status filter, sortable columns, pagination, **New transaction** dialog that lets you demonstrate 202 → 200 replay → 409 conflict live.
* **Details**: all fields, metadata, last error, live **lifecycle timeline** (claim, GET, POST, verification, retries, deferrals, lease recovery).
* **System**: API, PostgreSQL, Redis, queue fill, worker pool table, circuit breaker.
* Loading / empty / error states everywhere, keyboard focus styles, ARIA labels/`aria-sort`/`aria-pressed`, reduced-motion respected, responsive.

## 17. Observability

* `/api/health`: database & Redis (latency), queue depth/in-flight/limit, live workers (in-flight, capacity, processed, breaker), transaction counts, 5-minute failed-ratio and posting-service error rate. Status: `healthy` / `degraded` (Redis down, no workers, breaker open, queue saturated) / `unhealthy` (DB down → 503).
* Structured JSON logs with `request_id`, `transaction_id`, `worker_id`, retry counts, durations, error codes.
* Prometheus: `tps_http_requests_total`, `tps_http_request_duration_seconds`, `tps_transactions_accepted_total{outcome}`, `tps_transactions_completed_total{via}`, `tps_transactions_failed_total{code}`, `tps_transaction_processing_seconds`, `tps_retries_scheduled_total{reason}`, `tps_verifications_total{result}`, `tps_mock_requests_total{operation,outcome}`, `tps_mock_request_duration_seconds`, `tps_circuit_breaker_state`, `tps_queue_depth`, `tps_open_transactions`, `tps_worker_inflight`, `tps_leases_lost_total`, `tps_leases_reaped_total`, `tps_dispatched_total`, `tps_enqueue_failures_total`. API exposes them on `:8000/metrics` (multiprocess aggregated), each worker on `:9100`. No Prometheus/Grafana server is bundled (dashboard + health cover the demo).

---

## 18. Local setup (without Docker)

Requires Python 3.12, PostgreSQL 16, Redis 7, Node 20, and the mock service (`docker run -p 8080:8080 vinhopenfabric/mock-posting-service:latest`).

```bash
cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+asyncpg://tps:tps-local-dev@localhost:5432/tps
export REDIS_URL=redis://localhost:6379/0 MOCK_SERVICE_URL=http://localhost:8080
alembic upgrade head
uvicorn app.main:create_app --factory --port 8000 &        # API
python -m app.workers.runner &                             # worker (start more for scale-out)
cd ../frontend && npm install && npm run dev               # http://localhost:5173 (proxies /api)
```

## 19. Docker setup

```bash
cp .env.example .env        # optional; every variable has a default
docker compose up --build   # UI :3000 · API :8000 (/docs) · mock :8080 · Postgres :5432 · Redis :6379
docker compose up --build --scale worker=4      # more workers
bash scripts/demo.sh                             # scripted demo against the running stack
```
Prerequisites: Docker Compose v2 and network access to pull `vinhopenfabric/mock-posting-service`. If the mock image has no arm64 build on Apple Silicon, add `platform: linux/amd64` to that service. The compose file runs the mock image **unmodified**. `docker compose down -v` resets everything.

## 20. Environment variables

See `.env.example` (all documented, none secret beyond the local-dev Postgres password; do not commit `.env`). Key groups: `API_WORKERS`, `QUEUE_MAX_DEPTH`/`MAX_OPEN_TRANSACTIONS`/`RETRY_AFTER_SECONDS` (backpressure), `WORKER_REPLICAS`/`WORKER_CONCURRENCY`/`LEASE_*`/`RECONCILE_*`/`SHUTDOWN_GRACE_SECONDS` (workers), `MOCK_*_TIMEOUT_SECONDS`/`VERIFICATION_SETTLE_SECONDS` (posting-service client), `RETRY_*` (backoff), `CIRCUIT_*` (breaker).

> Keep `MOCK_POST_TIMEOUT_SECONDS` > the mock's max latency (10 s) and `VERIFICATION_SETTLE_SECONDS` > 10 s, or the safety argument in §7 no longer holds. Keep `LEASE_TTL_SECONDS` > 2× `LEASE_HEARTBEAT_SECONDS`. Crash recovery time is analytically expected to remain below 30 seconds (lease TTL 10 s + settle 12 s + dispatch tick ≈ 23–25 s); this must be validated with an actual failure test on a running deployment (**analytical, not measured**).

## 21. Running tests

```bash
make test                          # docker compose --profile test run --rm tests  (unit + integration)
make test-live                     # additionally exercises the REAL mock container (slow; uses POST /cleanup before/after)
# or locally: cd backend && pytest -m "not integration"      # unit only, no services needed
#             TEST_DATABASE_URL=... TEST_REDIS_URL=... pytest   # needs Postgres + Redis
```
The compose `tests` service uses a separate `tps_test` database and Redis DB 15, so dev data is untouched.

## 22. Running load tests

```bash
pip install httpx
python load-tests/load_test.py --total 1000  --concurrency 100
python load-tests/load_test.py --total 10000 --concurrency 200 --dup-ratio 0.05
python load-tests/load_test.py --total 1000  --concurrency 100 --wait --cleanup-mock --json load-tests/results/latest.json   # + end-to-end completion
```
Reports HTTP requests, unique transactions, duplicate replays, requests/s, avg/p50/p95/p99/max latency (client side and server side `X-Response-Time-Ms`), share under 100 ms, status mix, error rate, sampled queue depth, and (with `--wait`) exact test UUID completion/failure/unfinished counts and completion rate asserting invariant `completed + failed + unfinished == submitted`. Run the generator on a machine that is not the bottleneck; note the host specs with your results.

## 23. Benchmark results — to be filled from a real run

> No numbers are claimed here. Paste the script's JSON summary after running locally.

| Run | Env (CPU/RAM, API procs, worker replicas × concurrency) | Total / concurrency | Req/s | avg ms | p50 | p95 | p99 | % < 100 ms | Error rate | Max queue depth | Completion rate (tx/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 100 | _fill_ | | | | | | | | | | |
| 1,000 | _fill_ | | | | | | | | | | |
| 10,000 | _fill_ | | | | | | | | | | |

Also record: measured crash-recovery time (kill a worker mid-run: `docker kill <worker>`; time until all in-flight rows are terminal) and which resource saturated first.

---

## 24. Design decisions

| Decision | Why |
|---|---|
| PostgreSQL as source of truth, Redis only for delivery | Durability and constraints (PK, CHECK, CAS) live in one place; a Redis loss can't lose or duplicate work |
| Outbox flag + dispatcher | Redis outage ≠ data loss; also re-queues delayed retries |
| Lease + version CAS instead of Redis locks | The state we protect is in PostgreSQL; a lock in a different system could disagree with it |
| `verifying` + settle window | The only way to make "GET after failure" actually sound given the mock's pre-write delay |
| Crash recovery goes to `verifying` | A crash may have happened after the write |
| DB-driven delay scheduling | Delays survive Redis loss; simpler than a ZSET + reconciliation |
| Events table in the same transaction | Timeline/metrics can never disagree with state |
| Batched lease heartbeat | O(workers) not O(in-flight) writes |
| Pure-ASGI middleware, multi-process API, prom multiprocess mode | Needed to approach 1000 rps in Python without per-request overhead |
| Money as `numeric` / strings in JSON | Avoid float rounding in storage and API (see assumptions for the outbound float) |

## 25. Trade-offs

* **Per-process circuit breaker**: simple and fast; N workers each need to observe failures before opening.
* **No exactly-once claim**: guarantees are *at-least-once delivery + effectively-once posting*, bounded by the posting service's timing assumptions.
* **Dispatch latency ≤ ~1 s** for retries/verification (polling interval).
* **Workers idle while Redis is down** (no direct DB-polling fallback).
* **Soft backpressure** (cached gauges).
* **Events table grows unbounded** (no retention job yet).
* **Settle window adds ≥ ~12 s** to the (rare) uncertain-failure path: the price of never duplicating.

## 26. Scalability

API processes are stateless (scale with `API_WORKERS` / more containers behind a load balancer). Workers scale horizontally (consumer group + SKIP LOCKED dispatch + CAS claims; no worker-local state). Bottlenecks in order: posting-service concurrency → worker slots → PostgreSQL write rate (≈ 4–6 small writes per transaction: insert, claim, transition, events) → Redis. Next steps: partitioned/archived events, PgBouncer, batched event writes.

## 27. Failure scenarios tested

| Scenario | Test | Verified here? |
|---|---|---|
| Pre-write failure → verify absent → retry once | `test_processor::test_pre_write_failure_…` | needs PG+Redis |
| Post-write failure → found, **no** re-POST | `test_post_write_failure_…` | needs PG+Redis |
| Timeout + late write found after settle; no early GET | `test_timeout_then_late_write_…` | needs PG+Redis |
| Connection reset after write | `test_connection_reset_…` | needs PG+Redis |
| Service down → retries exhausted, meaningful error | `test_posting_service_unavailable_…` | needs PG+Redis |
| Permanent 4xx rejection | `test_permanent_rejection_…` | needs PG+Redis |
| Circuit open defers w/o burning retries; recovers | `test_open_circuit_defers_…`, `test_outage_trips_breaker_…` | needs PG+Redis |
| 25 concurrent duplicate submissions | `test_api::test_concurrent_identical_…` | needs PG+Redis |
| 3 workers race one transaction | `test_concurrency::test_three_workers_…` | needs PG+Redis |
| 25-way claim contention | `test_claim_is_exclusive_…` | needs PG+Redis |
| Crash after write / before write → recovery | `test_crash_after_write_…`, `…before_write…`, e2e `test_worker_crash_…` | needs PG+Redis |
| Stale worker write rejected | `test_stale_worker_cannot_overwrite…` | needs PG+Redis |
| Redis down: 202 still returned, later delivered | `test_outbox::test_redis_outage_…` | needs PG+Redis |
| Lost stream messages re-queued; no dispatcher duplicates | `test_outbox` | needs PG+Redis |
| Consumer-group redelivery (XAUTOCLAIM) | `test_consumer_group_…` | needs PG+Redis |
| Backpressure 503 + Retry-After | `test_backpressure_…` | needs PG+Redis |
| Graceful shutdown drains in-flight | `test_graceful_shutdown_…` | needs PG+Redis |
| State machine, retry math, hashing, breaker, validation, mock client | `tests/unit/*` | **yes (81 unit tests executed & passed)** |
| Load test exact UUID & invariant accounting | `tests/unit/test_load_accounting.py` | **yes (executed & passed)** |
| Integration, concurrency, failure, crash recovery, Redis outage | `tests/integration/*` | requires live PostgreSQL + Redis services |

### Testing status (what was and wasn't run)
* **Executed & Verified:** Python byte-compilation of all backend and load-test modules; all 81 unit tests (state machine, retry policy, canonical hashing, circuit breaker, input validation, mock client response parsing/classification, load-test exact UUID tracking, baseline regression test, and invariant accounting) passed.
* **Frontend Verified:** Built with Vite and TypeScript compiler (`tsc -b && vite build`) successfully.
* **Requires Live Infrastructure:** Tests in `backend/tests/integration` require running PostgreSQL and Redis instances (e.g. via `docker compose --profile test run --rm tests` or `make test`). In environments without running database/broker daemons, integration suites cleanly skip rather than reporting false positives.
* **Benchmark Results:** In accordance with the honest reporting policy, no synthetic or unverified benchmark numbers are pre-filled in §23. Real numbers must be filled from an actual run on the target hardware.

## 28. Known assumptions

1. **Mock service wire format is unverified.** Assumed: `GET /transactions/{id}` → 404 when absent, 2xx with a body when present (tolerant parser in `response_indicates_exists`); POST body = the assignment schema with `amount` as a JSON number; 2xx = written. All of this is isolated in `services/mock_client.py` (`MockPostingServiceClient`).
2. The mock's write delay is ≤ 10 s (per the assignment); POST timeout 15 s and settle 12 s are derived from it.
3. Outbound `amount` is sent as a float (as in the exercise's curl); storage and API use exact decimals (≤ 4 dp).
4. Naive timestamps are interpreted as UTC.
5. A 2xx POST response is trusted as "written" (no confirmatory GET).
6. Currency validation uses an embedded list of active ISO 4217 codes.
7. No authentication/authorization (out of scope; would sit in front of the API).
8. The transaction `id` supplied by the client is also the id used at the posting service.

## 29. Future improvements

Per-client rate limiting (token bucket in Redis); auth; shared (Redis-backed) circuit breaker; direct DB-polling fallback while Redis is down; events retention/partitioning; PgBouncer; OpenTelemetry traces; bundled Prometheus + Grafana + alert rules (queue age, failed ratio, breaker open); batched event inserts; dead-letter view and manual "reconcile/retry" action for `VERIFICATION_EXHAUSTED`; contract tests against the real mock to pin its response format.

## 30. Demo instructions

See [`docs/DEMO.md`](docs/DEMO.md) (2–3 minute script) and `bash scripts/demo.sh`.

## Repository layout

```
backend/   app/{api,core,db,models,schemas,services,workers}  alembic/  tests/{unit,integration}
frontend/  src/{components,pages,hooks,services,types,lib}  nginx.conf  Dockerfile
load-tests/load_test.py   scripts/{demo.sh,init-test-db.sql}   docs/DEMO.md
docker-compose.yml  .env.example  Makefile
```
(The worker lives in `backend/app/workers` and ships in the same image as the API; it is a separate *process/service* in Compose.)
