"""Central, environment-driven configuration. No secrets live in source code."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # Infrastructure
    database_url: str = "postgresql+asyncpg://tps:tps@localhost:5432/tps"
    redis_url: str = "redis://localhost:6379/0"
    mock_service_url: str = "http://localhost:8080"

    # API
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    log_level: str = "INFO"
    db_pool_size: int = 20
    db_max_overflow: int = 20
    redis_socket_timeout_seconds: float = 0.5
    redis_down_backoff_seconds: float = 2.0

    # Queue (Redis Streams)
    stream_name: str = "tps:transactions"
    consumer_group: str = "tps-workers"
    worker_registry_prefix: str = "tps:workers:"
    worker_registry_ttl_seconds: int = 15

    # Backpressure
    queue_max_depth: int = 50_000
    max_open_transactions: int = 200_000
    load_shed_refresh_seconds: float = 1.0
    retry_after_seconds: int = 5

    # Worker
    worker_concurrency: int = 150
    worker_metrics_port: int = 9100
    lease_ttl_seconds: float = 10.0
    lease_heartbeat_seconds: float = 3.0
    reconcile_interval_seconds: float = 1.0
    reconcile_batch_size: int = 500
    requeue_after_seconds: float = 20.0
    reaper_interval_seconds: float = 2.0
    autoclaim_interval_seconds: float = 5.0
    autoclaim_min_idle_ms: int = 30_000
    shutdown_grace_seconds: float = 30.0
    sample_interval_seconds: int = 5
    sample_retention_hours: int = 24

    # Posting-service client
    mock_connect_timeout_seconds: float = 3.0
    mock_get_timeout_seconds: float = 5.0
    mock_post_timeout_seconds: float = 15.0
    mock_max_connections: int = 200
    verification_settle_seconds: float = 12.0

    # Retry policy
    retry_max_retries: int = 5
    retry_initial_delay_seconds: float = 1.0
    retry_max_delay_seconds: float = 60.0
    retry_jitter_ratio: float = 0.2

    # Circuit breaker
    circuit_window_size: int = 20
    circuit_min_calls: int = 10
    circuit_failure_ratio: float = 0.5
    circuit_open_seconds: float = 15.0
    circuit_half_open_max_calls: int = 3

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
