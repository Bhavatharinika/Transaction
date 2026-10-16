"""FastAPI application factory."""
import asyncio
import contextlib
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api.routes import router
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.session import Database
from app.services.load_shedder import LoadShedder
from app.services.observability import Observability
from app.services.queue import SafeEnqueuer, TransactionQueue
from app.services.transaction_service import TransactionService
from app.services.worker_registry import WorkerRegistry

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database.from_settings(settings)
        # Short socket timeouts keep the API fast even when Redis is slow/unreachable.
        redis = Redis.from_url(
            settings.redis_url, decode_responses=True, max_connections=200,
            socket_timeout=settings.redis_socket_timeout_seconds, socket_connect_timeout=settings.redis_socket_timeout_seconds,
        )
        queue = TransactionQueue(redis, settings.stream_name, settings.consumer_group)
        shedder = LoadShedder(db, queue, settings)
        registry = WorkerRegistry(redis, settings.worker_registry_prefix, settings.worker_registry_ttl_seconds)
        app.state.transactions = TransactionService(db, SafeEnqueuer(queue, settings.redis_down_backoff_seconds), shedder)
        app.state.observability = Observability(db, redis, queue, registry, shedder, settings)
        app.state.db, app.state.redis = db, redis
        shedder_task = asyncio.create_task(shedder.run(), name="load-shedder")
        logger.info("api started")
        try:
            yield
        finally:
            shedder_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await shedder_task
            await redis.aclose()
            await db.dispose()

    app = FastAPI(
        title="High Performance Transaction Processing System", version="1.0.0", lifespan=lifespan,
        description="Reliable, idempotent intermediary in front of a slow, unreliable posting service.",
    )
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["GET", "POST"], allow_headers=["*"], expose_headers=["X-Request-ID", "Retry-After", "Idempotent-Replay"])
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(router)
    return app
