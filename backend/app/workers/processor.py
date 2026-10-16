"""Transaction processing algorithm (one claimed transaction at a time).

Safety rules enforced here:
  R1. A transaction is only ever worked on by the worker that atomically claimed it (lease + CAS).
  R2. GET always precedes POST.
  R3. After an UNCERTAIN POST failure (timeout / reset / 5xx) we NEVER POST again directly. We move to
      `verifying`, wait out the settle window (the mock sleeps up to 10s before writing), then GET:
        found   -> completed          (post-write failure)
        missing -> retry with backoff (pre-write failure confirmed, re-POST is now safe)
  R4. A crashed worker's `processing` row is recovered into `verifying` (never straight to POST).
  R5. Circuit-open deferrals do not consume retry budget.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Sequence
from uuid import UUID

from sqlalchemy import func, null

from app.core.config import Settings
from app.core.logging import bind_context
from app.core.metrics import CLAIMS, LEASES_LOST, RETRIES_SCHEDULED, TX_COMPLETED, TX_FAILED, TX_PROCESSING_SECONDS, VERIFICATIONS
from app.db.session import Database
from app.models.transaction import Transaction
from app.services import repository
from app.services.circuit_breaker import CircuitBreaker
from app.services.mock_client import FailureKind, MockPostingServiceClient, PostingPayload, PostingServiceError
from app.services.repository import db_in, utc_iso
from app.services.retry import RetryPolicy
from app.services.state_machine import TxStatus

logger = logging.getLogger(__name__)


class Outcome(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    VERIFICATION_SCHEDULED = "verification_scheduled"
    DEFERRED = "deferred"
    SKIPPED = "skipped"      # not claimable (already owned / not due / terminal): safe to acknowledge
    LEASE_LOST = "lease_lost"
    ERROR = "error"          # unexpected: do NOT acknowledge; recovery paths will pick it up


class CircuitOpenError(Exception):
    pass


def _err(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "at": utc_iso()}


class TransactionProcessor:
    def __init__(self, db: Database, client: MockPostingServiceClient, breaker: CircuitBreaker, settings: Settings,
                 worker_id: str, policy: RetryPolicy | None = None) -> None:
        self.db, self.client, self.breaker, self.s, self.worker_id = db, client, breaker, settings, worker_id
        self.policy = policy or RetryPolicy(settings.retry_max_retries, settings.retry_initial_delay_seconds,
                                            settings.retry_max_delay_seconds, settings.retry_jitter_ratio)

    # ------------------------------------------------------------------ entry point
    async def process(self, tx_id: UUID) -> Outcome:
        bind_context(transaction_id=str(tx_id), worker_id=self.worker_id)
        try:
            async with self.db.transaction() as session:
                tx = await repository.claim(session, tx_id, self.worker_id, self.s.lease_ttl_seconds)
                if tx is not None:
                    await repository.add_event(session, tx_id, "claimed", {"worker": self.worker_id, "phase": "verify" if tx.status == "verifying" else "attempt", "retryCount": tx.retry_count})
        except Exception as exc:  # noqa: BLE001  (DB unavailable etc.: leave the message un-acked for redelivery)
            logger.warning("claim failed", extra={"error": type(exc).__name__})
            return Outcome.ERROR
        if tx is None:
            CLAIMS.labels("skipped").inc()
            return Outcome.SKIPPED
        CLAIMS.labels("won").inc()
        started = time.monotonic()
        try:
            outcome = await (self._verify(tx) if tx.status == TxStatus.VERIFYING.value else self._attempt(tx))
        except asyncio.CancelledError:
            raise  # shutdown: the lease expires and the reaper moves the row to `verifying`
        except Exception:  # noqa: BLE001
            logger.exception("unexpected processing error; lease will expire and be recovered")
            return Outcome.ERROR
        logger.info("processed", extra={"outcome": outcome.value, "duration_ms": round((time.monotonic() - started) * 1000), "retry_count": tx.retry_count})
        return outcome

    # ------------------------------------------------------------------ phases
    async def _attempt(self, tx: Transaction) -> Outcome:
        try:
            found = await self._call(lambda: self.client.exists(tx.id))
        except CircuitOpenError:
            return await self._defer(tx)
        except PostingServiceError as exc:
            return await self._retry_or_fail(tx, exc.as_error(), reason="get_failed")
        if found:
            return await self._complete(tx, via="pre_check")  # R2: already at the posting service (earlier attempt / other path)

        post_started = time.monotonic()
        try:
            await self._call(lambda: self.client.post(self._payload(tx)))
        except CircuitOpenError:
            return await self._defer(tx)
        except PostingServiceError as exc:
            return await self._handle_post_failure(tx, exc, post_started)
        return await self._complete(tx, via="post", extra_events=[("post_ok", {})])

    async def _handle_post_failure(self, tx: Transaction, exc: PostingServiceError, post_started: float) -> Outcome:
        failure = ("post_failed", {"code": exc.code, "kind": exc.kind.value, "status": exc.status_code})
        if exc.kind is FailureKind.REJECTED:
            return await self._fail(tx, _err("POSTING_REJECTED", f"Posting service permanently rejected the transaction: {exc}"), extra_events=[failure])
        if exc.kind is FailureKind.NOT_WRITTEN:
            return await self._retry_or_fail(tx, exc.as_error(), reason="not_written", extra_events=[failure])
        # UNCERTAIN (R3): do not re-POST. Wait out the settle window measured from POST start, then verify with GET.
        wait = max(self.s.verification_settle_seconds - (time.monotonic() - post_started), 1.0)
        ok = await self._commit(
            tx, TxStatus.VERIFYING, "verification_scheduled", {"settleSeconds": round(wait, 1), "cause": exc.code}, extra_events=[failure],
            next_attempt_at=db_in(wait), enqueued_at=None, last_error=exc.as_error(),
        )
        return Outcome.VERIFICATION_SCHEDULED if ok else Outcome.LEASE_LOST

    async def _verify(self, tx: Transaction) -> Outcome:
        try:
            found = await self._call(lambda: self.client.exists(tx.id))
        except CircuitOpenError:
            return await self._defer(tx)
        except PostingServiceError as exc:
            VERIFICATIONS.labels("error").inc()
            return await self._verification_unavailable(tx, exc)
        if found:
            VERIFICATIONS.labels("found").inc()
            return await self._complete(tx, via="verification", extra_events=[("verify_found", {})])  # post-write failure
        VERIFICATIONS.labels("missing").inc()
        error = _err("PRE_WRITE_FAILURE", "POST failed and the posting service holds no such transaction; safe to retry")
        return await self._retry_or_fail(tx, error, reason="pre_write_confirmed", extra_events=[("verify_missing", {})])

    # ------------------------------------------------------------------ transitions
    async def _complete(self, tx: Transaction, *, via: str, extra_events: Sequence[tuple[str, dict]] = ()) -> Outcome:
        if not await self._commit(tx, TxStatus.COMPLETED, "completed", {"via": via}, extra_events=extra_events, completed_at=func.now(), last_error=null()):
            return Outcome.LEASE_LOST
        TX_COMPLETED.labels(via).inc()
        if tx.processing_started_at:
            TX_PROCESSING_SECONDS.observe((datetime.now(timezone.utc) - tx.processing_started_at).total_seconds())
        return Outcome.COMPLETED

    async def _fail(self, tx: Transaction, error: dict[str, Any], *, extra_events: Sequence[tuple[str, dict]] = (), **values: Any) -> Outcome:
        if not await self._commit(tx, TxStatus.FAILED, "failed", error, extra_events=extra_events, failed_at=func.now(), last_error=error, **values):
            return Outcome.LEASE_LOST
        TX_FAILED.labels(error["code"]).inc()
        logger.error("transaction failed permanently", extra={"error_code": error["code"]})
        return Outcome.FAILED

    async def _retry_or_fail(self, tx: Transaction, error: dict[str, Any], *, reason: str, extra_events: Sequence[tuple[str, dict]] = ()) -> Outcome:
        retry_count = tx.retry_count + 1
        if self.policy.exhausted(retry_count):
            exhausted = _err("RETRIES_EXHAUSTED", f"Gave up after {self.policy.max_retries} retries; last error {error['code']}: {error['message']}")
            return await self._fail(tx, exhausted, extra_events=extra_events, retry_count=retry_count)
        delay = self.policy.backoff_delay(retry_count)
        ok = await self._commit(
            tx, TxStatus.PENDING, "retry_scheduled", {"reason": reason, "retryCount": retry_count, "delaySeconds": round(delay, 2), "cause": error["code"]},
            extra_events=extra_events, retry_count=retry_count, next_attempt_at=db_in(delay), enqueued_at=None, last_error=error,
        )
        if ok:
            RETRIES_SCHEDULED.labels(reason).inc()
        return Outcome.RETRY_SCHEDULED if ok else Outcome.LEASE_LOST

    async def _verification_unavailable(self, tx: Transaction, exc: PostingServiceError) -> Outcome:
        retry_count = tx.retry_count + 1
        if self.policy.exhausted(retry_count):
            return await self._fail(tx, _err("VERIFICATION_EXHAUSTED", "Could not confirm whether the posting service stored this transaction; manual reconciliation required"), retry_count=retry_count)
        delay = self.policy.backoff_delay(retry_count)
        ok = await self._commit(
            tx, TxStatus.VERIFYING, "verification_rescheduled", {"retryCount": retry_count, "delaySeconds": round(delay, 2), "cause": exc.code},
            retry_count=retry_count, next_attempt_at=db_in(delay), enqueued_at=None, last_error=exc.as_error(),
        )
        return Outcome.VERIFICATION_SCHEDULED if ok else Outcome.LEASE_LOST

    async def _defer(self, tx: Transaction) -> Outcome:
        """Circuit open: park the transaction without consuming retry budget (R5)."""
        delay = max(self.breaker.retry_after(), 1.0)
        target = TxStatus.VERIFYING if tx.status == TxStatus.VERIFYING.value else TxStatus.PENDING
        ok = await self._commit(tx, target, "deferred", {"reason": "circuit_open", "retryInSeconds": round(delay, 1)}, next_attempt_at=db_in(delay), enqueued_at=None)
        return Outcome.DEFERRED if ok else Outcome.LEASE_LOST

    # ------------------------------------------------------------------ plumbing
    async def _commit(self, tx: Transaction, target: TxStatus, event: str, details: dict[str, Any] | None = None, **values: Any) -> bool:
        async with self.db.transaction() as session:
            ok = await repository.transition(session, tx, self.worker_id, target, event=event, event_details=details, **values)
        if not ok:
            LEASES_LOST.inc()
            logger.warning("stale worker write rejected (lease lost); newer state preserved", extra={"attempted": target.value})
        return ok

    async def _call(self, fn: Callable[[], Awaitable[Any]]) -> Any:
        if not self.breaker.allow_request():
            raise CircuitOpenError
        try:
            result = await fn()
        except PostingServiceError as exc:
            # A 4xx/409 proves the service is alive, so it must not push the breaker toward OPEN.
            (self.breaker.record_failure if exc.counts_for_breaker else self.breaker.record_success)()
            raise
        except BaseException:
            self.breaker.release_probe()
            raise
        self.breaker.record_success()
        return result

    @staticmethod
    def _payload(tx: Transaction) -> PostingPayload:
        return PostingPayload(id=tx.id, amount=tx.amount, currency=tx.currency, description=tx.description, timestamp=tx.timestamp, metadata=tx.meta)
