#!/usr/bin/env bash
# 2-3 minute demo flow against a running stack (docker compose up --build).
set -euo pipefail
API=${API:-http://localhost:8000}
ID=$(python3 -c 'import uuid;print(uuid.uuid4())')
BODY=$(printf '{"id":"%s","amount":100.50,"currency":"USD","description":"Demo payment","timestamp":"2025-01-15T10:30:00Z","metadata":{"demo":true}}' "$ID")
say() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

say "1. Submit -> immediate 202 (note X-Response-Time-Ms)"
curl -s -i -X POST "$API/api/transactions" -H 'Content-Type: application/json' -d "$BODY" | grep -iE "^HTTP|x-response-time|transactionId|^\{"
say "2. Same id, same payload -> 200 + Idempotent-Replay (no second transaction)"
curl -s -i -X POST "$API/api/transactions" -H 'Content-Type: application/json' -d "$BODY" | grep -iE "^HTTP|idempotent-replay"
say "3. Same id, different payload -> 409 IDEMPOTENCY_CONFLICT"
curl -s -X POST "$API/api/transactions" -H 'Content-Type: application/json' -d "${BODY/100.50/999.99}"; echo
say "4. Watch it progress (pending -> processing -> completed; the mock takes 2-10s and fails ~5%)"
for _ in $(seq 1 40); do
  S=$(curl -s "$API/api/transactions/$ID" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["status"],"retries="+str(d["retryCount"]),d["error"] or "")')
  echo "$S"; case "$S" in completed*|failed*) break;; esac; sleep 2
done
say "5. Full timeline (GET attempts, POST outcomes, verification, retries)"
curl -s "$API/api/transactions/$ID" | python3 -c 'import sys,json;[print(e["at"][11:23],e["type"],e["details"]) for e in json.load(sys.stdin)["events"]]'
say "6. Health + Prometheus metrics"
curl -s "$API/api/health" | python3 -m json.tool | head -40
curl -s "$API/metrics" | grep -E "^tps_(queue_depth|transactions_accepted_total|circuit_breaker_state)" || true
echo; echo "Dashboard: http://localhost:3000"
