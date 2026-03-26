"""Database engine and session factories for sync and async usage."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from osintsuite.config import Settings


def get_async_engine(settings: Settings):
    return create_async_engine(settings.database_url, echo=settings.debug, pool_pre_ping=True)


def get_async_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    engine = get_async_engine(settings)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def get_sync_engine(settings: Settings):
    return create_engine(settings.database_url_sync, echo=settings.debug, pool_pre_ping=True)


def get_sync_session_factory(settings: Settings) -> sessionmaker[Session]:
    engine = get_sync_engine(settings)
    return sessionmaker(engine, expire_on_commit=False)
