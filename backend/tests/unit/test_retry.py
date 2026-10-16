from app.services.retry import RetryPolicy

NO_JITTER = lambda: 0.0  # noqa: E731


def test_exponential_growth_without_jitter():
    policy = RetryPolicy(max_retries=5, initial_delay=1, max_delay=60, jitter_ratio=0.2)
    assert [policy.backoff_delay(n, NO_JITTER) for n in range(1, 7)] == [1, 2, 4, 8, 16, 32]


def test_delay_is_capped_at_max():
    policy = RetryPolicy(initial_delay=1, max_delay=10, jitter_ratio=0)
    assert policy.backoff_delay(20, NO_JITTER) == 10
    assert policy.backoff_delay(10_000, NO_JITTER) == 10  # no overflow


def test_jitter_is_additive_and_bounded():
    policy = RetryPolicy(initial_delay=10, max_delay=100, jitter_ratio=0.2)
    assert policy.backoff_delay(1, lambda: 1.0) == 12.0
    assert policy.backoff_delay(1, lambda: 0.0) == 10.0
    for _ in range(200):
        assert 10.0 <= policy.backoff_delay(1) <= 12.0


def test_exhaustion_boundary():
    policy = RetryPolicy(max_retries=3)
    assert [policy.exhausted(n) for n in range(1, 6)] == [False, False, False, True, True]  # 1 attempt + 3 retries


def test_zero_retries_means_single_attempt():
    assert RetryPolicy(max_retries=0).exhausted(1)
