from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, settings


def build_engine(config: Settings | None = None):
    cfg = config or settings
    return create_engine(cfg.resolved_database_url, echo=cfg.sqlalchemy_echo, future=True)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def create_session_factory(config: Settings | None = None) -> sessionmaker:
    engine_override = build_engine(config)
    return sessionmaker(bind=engine_override, autoflush=False, autocommit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
