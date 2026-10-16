"""Circuit breaker (CLOSED / OPEN / HALF_OPEN) over a rolling window of call outcomes. Pure logic.

Per-process by design (see README trade-offs): each worker protects itself and the shared dependency
independently, avoiding a Redis round-trip on every call.
"""
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class BreakerState(str, Enum):
    CLOSED = "closed"
    HALF_OPEN = "half_open"
    OPEN = "open"


@dataclass(frozen=True)
class BreakerConfig:
    window_size: int = 20
    min_calls: int = 10
    failure_ratio: float = 0.5
    open_seconds: float = 15.0
    half_open_max_calls: int = 3


class CircuitBreaker:
    def __init__(
        self,
        config: BreakerConfig = BreakerConfig(),
        clock: Callable[[], float] = time.monotonic,
        on_transition: Callable[[BreakerState, BreakerState], None] | None = None,
    ) -> None:
        self._cfg = config
        self._clock = clock
        self._on_transition = on_transition
        self._state = BreakerState.CLOSED
        self._window: deque[bool] = deque(maxlen=config.window_size)  # True = failure
        self._opened_at = 0.0
        self._probes_in_flight = 0
        self._probe_successes = 0

    @property
    def state(self) -> BreakerState:
        if self._state is BreakerState.OPEN and self._clock() - self._opened_at >= self._cfg.open_seconds:
            self._set_state(BreakerState.HALF_OPEN)
            self._probes_in_flight = 0
            self._probe_successes = 0
        return self._state

    def retry_after(self) -> float:
        """Seconds until the breaker may admit probe traffic (0 unless OPEN)."""
        if self.state is not BreakerState.OPEN:
            return 0.0
        return max(0.0, self._cfg.open_seconds - (self._clock() - self._opened_at))

    def allow_request(self) -> bool:
        state = self.state
        if state is BreakerState.CLOSED:
            return True
        if state is BreakerState.OPEN:
            return False
        if self._probes_in_flight + self._probe_successes < self._cfg.half_open_max_calls:
            self._probes_in_flight += 1
            return True
        return False

    def record_success(self) -> None:
        if self._state is BreakerState.HALF_OPEN:
            self._probes_in_flight = max(0, self._probes_in_flight - 1)
            self._probe_successes += 1
            if self._probe_successes >= self._cfg.half_open_max_calls:
                self._window.clear()
                self._set_state(BreakerState.CLOSED)
        elif self._state is BreakerState.CLOSED:
            self._window.append(False)

    def record_failure(self) -> None:
        if self._state is BreakerState.HALF_OPEN:
            self._probes_in_flight = max(0, self._probes_in_flight - 1)
            self._trip()
        elif self._state is BreakerState.CLOSED:
            self._window.append(True)
            if len(self._window) >= self._cfg.min_calls and sum(self._window) / len(self._window) >= self._cfg.failure_ratio:
                self._trip()

    def release_probe(self) -> None:
        """Return an admitted half-open probe slot when the call was cancelled before an outcome."""
        if self._state is BreakerState.HALF_OPEN:
            self._probes_in_flight = max(0, self._probes_in_flight - 1)

    def _trip(self) -> None:
        self._opened_at = self._clock()
        self._set_state(BreakerState.OPEN)

    def _set_state(self, new: BreakerState) -> None:
        old, self._state = self._state, new
        if old is not new and self._on_transition:
            self._on_transition(old, new)
