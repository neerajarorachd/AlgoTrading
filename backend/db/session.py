from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from db_config import load_database_config


def build_engine(environ: Optional[dict] = None, engine: Optional[Engine] = None) -> Engine:
    """Build the SQL Server engine from DB_CONNECTION_STRING, or return an injected one (tests)."""
    if engine is not None:
        return engine
    config = load_database_config(environ)
    return create_engine(config.url, pool_pre_ping=True, future=True)


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
