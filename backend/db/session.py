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
    background threads sharing one engine.

    fast_executemany=True (mssql+pyodbc only — not a valid pyodbc/SQLite
    argument) makes pyodbc batch a multi-row INSERT into actual bulk
    ODBC parameter arrays instead of issuing one round trip per row, which
    is pyodbc's default. Found the hard way (2026-09-14): a bulk flush of
    ~23,000 buffered CandleIndicators rows (one per replayed candle) over
    the SSH tunnel appeared to hang for many minutes without this — it
    wasn't stuck, just paying one network round trip per row.
    """
    if engine is not None:
        return engine
    config = load_database_config(environ)
    connect_args = {"check_same_thread": False} if config.backend == "sqlite" else {}
    extra = {"fast_executemany": True} if config.backend == "mssql" else {}
    return create_engine(config.url, pool_pre_ping=True, future=True, connect_args=connect_args, **extra)


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
