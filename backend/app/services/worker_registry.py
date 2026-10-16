"""Workers publish a TTL'd heartbeat to Redis so the health endpoint can report the live worker pool."""
import json
from typing import Any

from redis.asyncio import Redis


class WorkerRegistry:
    def __init__(self, redis: Redis, prefix: str, ttl_seconds: int) -> None:
        self._redis, self._prefix, self._ttl = redis, prefix, ttl_seconds

    async def publish(self, worker_id: str, info: dict[str, Any]) -> None:
        await self._redis.set(f"{self._prefix}{worker_id}", json.dumps(info), ex=self._ttl)

    async def remove(self, worker_id: str) -> None:
        await self._redis.delete(f"{self._prefix}{worker_id}")

    async def list_workers(self) -> list[dict[str, Any]]:
        workers: list[dict[str, Any]] = []
        async for key in self._redis.scan_iter(match=f"{self._prefix}*", count=100):
            raw = await self._redis.get(key)
            if raw:
                workers.append(json.loads(raw))
        return sorted(workers, key=lambda w: w.get("workerId", ""))
