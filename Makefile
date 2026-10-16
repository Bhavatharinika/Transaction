.PHONY: up down test test-live load load-wait demo logs
up:        ; docker compose up --build -d
down:      ; docker compose down -v
logs:      ; docker compose logs -f api worker
test:      ; docker compose --profile test run --rm tests
test-live: ; docker compose --profile test run --rm tests pytest -v -m mock_service
load:      ; python3 load-tests/load_test.py --total 1000 --concurrency 100
load-wait: ; python3 load-tests/load_test.py --total 1000 --concurrency 100 --wait --json load-tests/results/latest.json
demo:      ; bash scripts/demo.sh
