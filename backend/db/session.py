from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from db_config import load_database_config


def build_engine(environ: Optional[dict] = None, engine: Optional[Engine] = None) -> Engine:
    """Build the configured engine (SQL Server or local SQLite, see
    db_config.DatabaseConfig) from DB_CONNECTION_STRING, or return an
    injected one (tests). SQLite's default same-thread restriction is
    relaxed since this app's feed/timer/EOD-flush work all runs on
    background threads sharing one engine."""
    if engine is not None:
        return engine
    config = load_database_config(environ)
    connect_args = {"check_same_thread": False} if config.backend == "sqlite" else {}
    return create_engine(config.url, pool_pre_ping=True, future=True, connect_args=connect_args)


def build_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def session_scope(session_factory: sessionmaker) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
