from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from .config import Settings, settings
from .db import SessionLocal, build_engine, engine as default_engine


@dataclass
class AppState:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker


def default_state() -> AppState:
    return AppState(settings=settings, engine=default_engine, session_factory=SessionLocal)


def build_state(config: Settings | None = None) -> AppState:
    cfg = config or settings
    if cfg is settings:
        return default_state()

    engine = build_engine(cfg)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return AppState(settings=cfg, engine=engine, session_factory=session_factory)
