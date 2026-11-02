"""Database engine, session factory, and schema initialization."""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from mmm_studio.db.models import Base
from mmm_studio.settings import get_settings


@lru_cache(maxsize=4)
def get_engine(sqlalchemy_url: str | None = None) -> Engine:
    url = sqlalchemy_url or get_settings().sqlalchemy_url
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(
        url,
        pool_pre_ping=True,
        future=True,
        connect_args=connect_args,
    )


@lru_cache(maxsize=4)
def get_session_factory(sqlalchemy_url: str | None = None) -> sessionmaker[Session]:
    engine = get_engine(sqlalchemy_url)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db(sqlalchemy_url: str | None = None) -> None:
    """Create all application ORM tables if they do not exist."""
    engine = get_engine(sqlalchemy_url)
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a SQLAlchemy session."""
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
