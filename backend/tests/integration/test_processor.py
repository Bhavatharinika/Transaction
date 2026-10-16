"""Worker algorithm against a deterministic fake posting service: every failure mode from the assignment."""
from tests.integration.conftest import drive, events_of, fetch_row, insert_row
from app.workers.processor import Outcome
from app.services.circuit_breaker import BreakerConfig, CircuitBreaker


async def test_success_path_gets_before_posting(db, fake, make_processor):
    row = await insert_row(db)
    done = await drive(make_processor(), row.id)
    assert done.status == "completed" and done.completed_at and done.last_error is None
    assert fake.calls == [("GET", str(row.id)), ("POST", str(row.id))]  # GET always precedes POST
    assert fake.writes[str(row.id)] == 1


async def test_existing_transaction_is_never_reposted(db, fake, make_processor):
    row = await insert_row(db)
    fake.preload(row.id)
    done = await drive(make_processor(), row.id)
    assert done.status == "completed" and fake.post_calls[str(row.id)] == 0


async def test_pre_write_failure_is_verified_then_retried_exactly_once(db, fake, make_processor):
    row = await insert_row(db)
    fake.plan(row.id, "fail_before_write", "ok")
    done = await drive(make_processor(), row.id)
    assert done.status == "completed" and done.retry_count == 1
    assert fake.post_calls[str(row.id)] == 2 and fake.writes[str(row.id)] == 1
    events = await events_of(db, row.id)
    assert {"post_failed", "verification_scheduled", "verify_missing", "retry_scheduled", "post_ok", "completed"} <= set(events)
    assert events.index("verify_missing") < events.index("retry_scheduled") < events.index("post_ok")


async def test_post_write_failure_is_detected_and_never_reposted(db, fake, make_processor):
    row = await insert_row(db)
    fake.plan(row.id, "fail_after_write")
    done = await drive(make_processor(), row.id)
    assert done.status == "completed" and done.retry_count == 0
    assert fake.post_calls[str(row.id)] == 1 and fake.writes[str(row.id)] == 1  # the duplicate-POST bug does not occur
    assert "verify_found" in await events_of(db, row.id)


async def test_timeout_then_late_write_is_found_after_settle_window(db, fake, make_processor):
    """The mock sleeps before writing: a GET right after a timeout would say 'missing' and a retry would duplicate."""
    row = await insert_row(db)
    fake.plan(row.id, "timeout_after_write")
    fake.late_write_delay = 0.25  # lands AFTER the timeout but BEFORE the settle window (0.4s) ends
    processor = make_processor()
    assert await processor.process(row.id) is Outcome.VERIFICATION_SCHEDULED
    parked = await fetch_row(db, row.id)
    assert parked.status == "verifying" and parked.lease_owner is None
    assert await processor.process(row.id) is Outcome.SKIPPED  # not due yet: an early GET is impossible
    done = await drive(processor, row.id)
    assert done.status == "completed" and fake.writes[str(row.id)] == 1 and fake.post_calls[str(row.id)] == 1


async def test_connection_reset_after_write_is_not_reposted(db, fake, make_processor):
    row = await insert_row(db)
    fake.plan(row.id, "reset_after_write")
    done = await drive(make_processor(), row.id)
    assert done.status == "completed" and fake.writes[str(row.id)] == 1 and fake.post_calls[str(row.id)] == 1


async def test_timeout_before_write_is_retried_after_verification(db, fake, make_processor):
    row = await insert_row(db)
    fake.plan(row.id, "timeout_before_write", "ok")
    done = await drive(make_processor(), row.id)
    assert done.status == "completed" and fake.post_calls[str(row.id)] == 2 and fake.writes[str(row.id)] == 1


async def test_posting_service_unavailable_exhausts_retries_with_meaningful_error(db, fake, make_processor):
    fake.down = True
    row = await insert_row(db)
    done = await drive(make_processor(), row.id)
    assert done.status == "failed" and done.failed_at and done.retry_count == 4  # 1 attempt + 3 retries
    assert done.last_error["code"] == "RETRIES_EXHAUSTED" and "CONNECTION_ERROR" in done.last_error["message"]
    assert fake.post_calls[str(row.id)] == 0  # never POSTed while GET was failing


async def test_repeated_pre_write_failures_exhaust_retries(db, fake, make_processor):
    row = await insert_row(db)
    fake.plan(row.id, *["fail_before_write"] * 10)
    done = await drive(make_processor(), row.id)
    assert done.status == "failed" and done.last_error["code"] == "RETRIES_EXHAUSTED"
    assert fake.post_calls[str(row.id)] == 4 and fake.writes[str(row.id)] == 0


async def test_permanent_rejection_fails_without_retry(db, fake, make_processor):
    row = await insert_row(db)
    fake.plan(row.id, "http_400")
    done = await drive(make_processor(), row.id)
    assert done.status == "failed" and done.last_error["code"] == "POSTING_REJECTED" and fake.post_calls[str(row.id)] == 1


async def test_open_circuit_defers_without_consuming_retries_then_recovers(db, fake, make_processor):
    now = [0.0]
    breaker = CircuitBreaker(BreakerConfig(window_size=4, min_calls=2, failure_ratio=0.5, open_seconds=30), clock=lambda: now[0])
    breaker.record_failure(); breaker.record_failure()
    assert breaker.retry_after() > 0
    row = await insert_row(db)
    processor = make_processor(breaker=breaker)
    assert await processor.process(row.id) is Outcome.DEFERRED
    parked = await fetch_row(db, row.id)
    assert parked.status == "pending" and parked.retry_count == 0 and not fake.calls  # no budget burnt, service untouched
    assert "deferred" in await events_of(db, row.id)
    now[0] = 31.0  # breaker -> half-open, probes allowed
    done = await drive(processor, row.id)
    assert done.status == "completed" and done.retry_count == 0
