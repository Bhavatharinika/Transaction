from app.services.circuit_breaker import BreakerConfig, BreakerState, CircuitBreaker


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make(**overrides):
    clock = Clock()
    cfg = BreakerConfig(window_size=10, min_calls=4, failure_ratio=0.5, open_seconds=10, half_open_max_calls=2, **overrides)
    transitions = []
    return CircuitBreaker(cfg, clock, lambda a, b: transitions.append((a, b))), clock, transitions


def fail(breaker, n):
    for _ in range(n):
        assert breaker.allow_request()
        breaker.record_failure()


def test_starts_closed_and_allows_requests():
    breaker, _, _ = make()
    assert breaker.state is BreakerState.CLOSED and breaker.allow_request()


def test_does_not_open_below_min_calls():
    breaker, _, _ = make()
    fail(breaker, 3)
    assert breaker.state is BreakerState.CLOSED


def test_opens_when_failure_ratio_reached_and_rejects():
    breaker, _, transitions = make()
    fail(breaker, 4)
    assert breaker.state is BreakerState.OPEN
    assert not breaker.allow_request()
    assert transitions == [(BreakerState.CLOSED, BreakerState.OPEN)]


def test_successes_dilute_failures():
    breaker, _, _ = make()
    for _ in range(8):
        breaker.record_success()
    fail(breaker, 3)
    assert breaker.state is BreakerState.CLOSED  # 3/10 < 50%


def test_half_open_after_timeout_limits_probes():
    breaker, clock, _ = make()
    fail(breaker, 4)
    clock.now = 9.9
    assert not breaker.allow_request()
    assert 0 < breaker.retry_after() < 0.2
    clock.now = 10.0
    assert breaker.state is BreakerState.HALF_OPEN
    assert breaker.allow_request() and breaker.allow_request()
    assert not breaker.allow_request()  # probe budget exhausted


def test_half_open_success_closes():
    breaker, clock, _ = make()
    fail(breaker, 4)
    clock.now = 10
    for _ in range(2):
        assert breaker.allow_request()
        breaker.record_success()
    assert breaker.state is BreakerState.CLOSED
    assert breaker.allow_request()


def test_half_open_failure_reopens_immediately():
    breaker, clock, _ = make()
    fail(breaker, 4)
    clock.now = 10
    assert breaker.allow_request()
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    assert breaker.retry_after() == 10


def test_released_probe_frees_slot():
    breaker, clock, _ = make()
    fail(breaker, 4)
    clock.now = 10
    assert breaker.allow_request() and breaker.allow_request()
    breaker.release_probe()
    assert breaker.allow_request()
