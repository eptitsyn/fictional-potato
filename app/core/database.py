import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_engine_pid: int | None = None


def _build_engine() -> AsyncEngine:
    return create_async_engine(
        settings.DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        echo=False,
    )


def _ensure_process_local_state() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    global _engine, _engine_pid, _session_factory

    current_pid = os.getpid()
    if _engine is None or _session_factory is None or _engine_pid != current_pid:
        _engine = _build_engine()
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
        _engine_pid = current_pid

    return _engine, _session_factory


def get_engine() -> AsyncEngine:
    engine, _ = _ensure_process_local_state()
    return engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    _, session_factory = _ensure_process_local_state()
    return session_factory


def reset_async_db_state() -> None:
    global _engine, _engine_pid, _session_factory
    _engine = None
    _session_factory = None
    _engine_pid = None


async def dispose_async_db_state() -> None:
    global _engine, _engine_pid, _session_factory

    if _engine is not None:
        await _engine.dispose()

    _engine = None
    _session_factory = None
    _engine_pid = None


class _AsyncSessionFactoryProxy:
    def __call__(self, *args, **kwargs):
        return get_session_factory()(*args, **kwargs)


AsyncSessionLocal = _AsyncSessionFactoryProxy()


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
