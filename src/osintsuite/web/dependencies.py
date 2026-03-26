"""FastAPI dependency injection — database sessions and engine."""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from osintsuite.config import Settings, get_settings
from osintsuite.db.repository import Repository
from osintsuite.db.session import get_async_session_factory
from osintsuite.engine.investigation import InvestigationEngine

_session_factory = None


def _get_session_factory(settings: Settings = Depends(get_settings)):
    global _session_factory
    if _session_factory is None:
        _session_factory = get_async_session_factory(settings)
    return _session_factory


async def get_db(
    session_factory=Depends(_get_session_factory),
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_repo(session: AsyncSession = Depends(get_db)) -> Repository:
    return Repository(session)


async def get_engine(
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_settings),
) -> InvestigationEngine:
    return InvestigationEngine(repo, settings)
