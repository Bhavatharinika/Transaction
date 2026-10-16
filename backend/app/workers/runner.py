"""Worker process entrypoint:  python -m app.workers.runner"""
import asyncio
import logging
import signal

from prometheus_client import start_http_server
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import Database
from app.services.mock_client import MockPostingServiceClient
from app.workers.runtime import WorkerRuntime

logger = logging.getLogger("app.worker")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    start_http_server(settings.worker_metrics_port)  # Prometheus scrape endpoint for this worker
    db = Database.from_settings(settings)
    redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=5, socket_connect_timeout=2, health_check_interval=30)
    client = MockPostingServiceClient.from_settings(settings)
    runtime = WorkerRuntime(settings, db, redis, client)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await runtime.start()
    await stop.wait()
    logger.info("shutdown signal received")
    await runtime.stop()
    await client.aclose()
    await redis.aclose()
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
