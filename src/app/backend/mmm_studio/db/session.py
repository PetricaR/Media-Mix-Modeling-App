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


_INITIALISED: set[str] = set()
BASELINE_REVISION = "0001_baseline"


def run_migrations(sqlalchemy_url: str | None = None) -> None:
    """Bring the schema to the latest Alembic revision.

    A database created before migrations existed (tables present, no `alembic_version`) is stamped at the baseline
    first, so its existing tables are kept and only later revisions run.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    engine = get_engine(sqlalchemy_url)
    cfg = Config()
    cfg.set_main_option("script_location", "mmm_studio.db:migrations")
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        tables = set(inspect(conn).get_table_names())
        if "alembic_version" not in tables and "runs" in tables:
            command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")


def init_db(sqlalchemy_url: str | None = None, force: bool = False) -> None:
    """Migrate the application schema to the latest revision (once per database URL and process)."""
    url = sqlalchemy_url or get_settings().sqlalchemy_url
    if url in _INITIALISED and not force:
        return
    if url.startswith("sqlite"):
        Base.metadata.create_all(bind=get_engine(sqlalchemy_url))
    else:
        run_migrations(sqlalchemy_url)
    _INITIALISED.add(url)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a SQLAlchemy session."""
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
