"""Async engine / session management."""
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings


class Database:
    def __init__(self, url: str, *, pool_size: int = 20, max_overflow: int = 20) -> None:
        self.engine = create_async_engine(
            url, pool_size=pool_size, max_overflow=max_overflow, pool_timeout=5, pool_recycle=1800, pool_pre_ping=True
        )
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> "Database":
        return cls(settings.database_url, pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """One short DB transaction: commits on success, rolls back on error."""
        async with self.sessionmaker() as session:
            async with session.begin():
                yield session

    async def ping(self) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()
