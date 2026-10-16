"""Exponential backoff with jitter. Pure logic."""
import random
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 5
    initial_delay: float = 1.0
    max_delay: float = 60.0
    jitter_ratio: float = 0.2

    def exhausted(self, retry_count: int) -> bool:
        """`retry_count` = number of failed attempts recorded so far (including the one just failed).

        With max_retries=5 a transaction gets 1 initial attempt + 5 retries = 6 attempts total.
        """
        return retry_count > self.max_retries

    def backoff_delay(self, retry_number: int, rng: Callable[[], float] = random.random) -> float:
        """Delay before retry #`retry_number` (1-based): min(max_delay, initial * 2^(n-1)) + jitter.

        Jitter is additive and bounded by `jitter_ratio` of the base delay, which de-synchronises retry
        storms without ever shortening the minimum backoff.
        """
        exponent = min(max(retry_number - 1, 0), 30)
        base = min(self.max_delay, self.initial_delay * (2 ** exponent))
        return base + base * self.jitter_ratio * rng()
