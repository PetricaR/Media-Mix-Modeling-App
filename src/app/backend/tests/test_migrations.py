"""The Alembic migrations build exactly the schema the SQLAlchemy models describe (no drift)."""

from __future__ import annotations

import os

from sqlalchemy import create_engine, text

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql:///mmm_studio_test?host=/tmp&port=5433")


def test_migrations_match_the_models(monkeypatch) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from mmm_studio.db.models import Base
    from mmm_studio.db.session import get_engine, get_session_factory, run_migrations
    from mmm_studio.settings import get_settings

    monkeypatch.setenv("MMM_DATABASE_URL", TEST_DB_URL)
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    url = get_settings().sqlalchemy_url
    engine = create_engine(url)
    with engine.begin() as conn:
        Base.metadata.drop_all(bind=conn)
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    run_migrations()
    def own_tables(name, type_, parent_names):  # the env's filter: Procrastinate's tables are not ours
        return type_ != "table" or name in Base.metadata.tables

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"include_name": own_tables})
        assert compare_metadata(ctx, Base.metadata) == []
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    engine.dispose()
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
