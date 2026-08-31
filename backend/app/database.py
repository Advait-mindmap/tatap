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


def init_db() -> None:
    if DATABASE_URL.startswith('postgresql') or DATABASE_URL.startswith('postgres'):
        with engine.begin() as conn:
            conn.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS vector;')
    Base.metadata.create_all(bind=engine)
