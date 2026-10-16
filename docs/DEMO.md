# 2–3 minute demo script

Prereq: `docker compose up --build` (first build pulls the mock image). Open http://localhost:3000.

| Time | Do | Say |
|---|---|---|
| 0:00 | Overview page | "Real numbers from PostgreSQL/Redis; nothing is mocked in the UI. Workers, queue depth and breaker state come from worker heartbeats." |
| 0:20 | Transactions → **New transaction** → Submit | "202 in a few milliseconds — the API only does one INSERT and one XADD. The mock takes 2–10 s, we never wait for it." |
| 0:40 | Click **Submit again (same ID)** | "200 + `Idempotent-Replay`. Primary-key `ON CONFLICT DO NOTHING`: no second row, no second queue message." |
| 0:55 | **Resubmit with changed amount** | "409 — payload hash differs for the same idempotency key." |
| 1:10 | Open transaction details | "Timeline: worker claim → GET (absent) → POST → completed. GET always precedes POST." |
| 1:30 | Submit ~20 transactions with `scripts/demo.sh` or the load script (`--total 50`) | "The mock fails ~5% of calls. Open a transaction with retries ≥ 1 or a `verify_found` event." |
| 1:50 | Show one with `post_failed → verification_scheduled → verify_found` | "POST errored after the mock had written. We did NOT re-POST; we waited out the settle window, GET found it, marked completed." |
| 2:10 | System page | "PostgreSQL, Redis, queue fill, worker pool, circuit breaker." |
| 2:25 | `docker compose stop redis`, POST from curl → still 202; `docker compose start redis` | "Accepted work is durable in PostgreSQL; the dispatcher relays it when Redis is back." |
| 2:45 | `make test` output | "Unit, integration, concurrency, crash-recovery tests." |
