from __future__ import annotations

import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import Base

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    DATABASE_URL = 'sqlite:///./task2.db'

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def is_postgres() -> bool:
    return DATABASE_URL.startswith('postgresql') or DATABASE_URL.startswith('postgres')


def init_db() -> None:
    """Historical entry point. Kept so existing callers and docs still work."""
    ensure_tables()


_TABLES_READY = False


def ensure_tables() -> None:
    """Create any missing tables, once per process.

    `init_db()` was exported but never called by the app, so a fresh deployment had a database
    with no tables in it — which nothing noticed while every feature was in-memory. Durable runs
    and the usage cap both read and write, so they call this lazily before their first query
    rather than depending on someone having run a migration by hand.

    **The pgvector extension has to exist before `create_all`, not alongside it.** Two corpus
    tables declare `Vector(1536)` columns, and Postgres rejects that type outright when the
    extension is absent — which fails the whole `create_all`, not just those two tables. So a
    deployment without the extension would end up with no `run_states` and no `usage_counters`
    either, and because metering fails closed, every request would then be refused with a 503.
    A missing extension would have taken the API down rather than degrading one feature.

    `init_db()` already did this and `ensure_tables()` did not, which is exactly the kind of
    divergence that survives because only the unused path was correct. They are one function now.

    Idempotent and cheap: CREATE EXTENSION IF NOT EXISTS, CREATE TABLE per missing table, and
    the flag keeps it to one round trip per process.
    """
    global _TABLES_READY
    if _TABLES_READY:
        return
    if is_postgres():
        with engine.begin() as conn:
            conn.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS vector;')
    Base.metadata.create_all(bind=engine)
    _TABLES_READY = True


def reset_tables_ready() -> None:
    """Test hook: forget that tables were checked (used when the engine is swapped out)."""
    global _TABLES_READY
    _TABLES_READY = False
