"""SQLAlchemy 2.0 database engine, session management, and initialization."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.config import PROJECT_ROOT, get_env_settings


class Base(DeclarativeBase):
    """Base declarative class for all SlotWise AI ORM entities."""


def _normalize_sqlite_url(database_url: str) -> str:
    """Ensure relative SQLite file paths exist and resolve consistently."""
    if not database_url.startswith("sqlite:///"):
        return database_url
    if database_url in {"sqlite:///:memory:", "sqlite:///"}:
        return database_url

    raw_path = database_url.removeprefix("sqlite:///")
    path_obj = Path(raw_path)
    if not path_obj.is_absolute():
        path_obj = (PROJECT_ROOT / path_obj).resolve()
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path_obj.as_posix()}"


def _configure_sqlite_connection(dbapi_connection: Any, _connection_record: Any) -> None:
    """Enable strict foreign-key enforcement and WAL mode on SQLite connections."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass
    cursor.close()


def create_db_engine(database_url: str | None = None, echo: bool = False) -> Engine:
    """Create a SQLAlchemy 2.x Engine supporting SQLite (default) and PostgreSQL."""
    raw_url = database_url or get_env_settings().database_url
    resolved_url = _normalize_sqlite_url(raw_url)

    is_sqlite = resolved_url.startswith("sqlite")
    connect_args: dict[str, Any] = {"check_same_thread": False} if is_sqlite else {}
    engine_kwargs: dict[str, Any] = {
        "echo": echo,
        "future": True,
        "connect_args": connect_args,
    }
    if not is_sqlite:
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["pool_size"] = 10
        engine_kwargs["max_overflow"] = 20

    engine = create_engine(resolved_url, **engine_kwargs)
    if is_sqlite:
        event.listen(engine, "connect", _configure_sqlite_connection)
    return engine


_DEFAULT_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker[Session] | None = None


def configure_default_engine(database_url: str | None = None, echo: bool = False) -> Engine:
    """Reset and configure the global engine and session factory."""
    global _DEFAULT_ENGINE, _SESSION_FACTORY
    if _DEFAULT_ENGINE is not None:
        _DEFAULT_ENGINE.dispose()
    _DEFAULT_ENGINE = create_db_engine(database_url=database_url, echo=echo)
    _SESSION_FACTORY = sessionmaker(
        bind=_DEFAULT_ENGINE,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    return _DEFAULT_ENGINE


def get_engine() -> Engine:
    """Retrieve or lazily initialize the default database engine."""
    if _DEFAULT_ENGINE is None:
        return configure_default_engine()
    return _DEFAULT_ENGINE


def get_session_factory() -> sessionmaker[Session]:
    """Retrieve or lazily initialize the default sessionmaker."""
    if _SESSION_FACTORY is None:
        configure_default_engine()
    assert _SESSION_FACTORY is not None
    return _SESSION_FACTORY


@contextmanager
def session_scope(session_factory: sessionmaker[Session] | None = None) -> Generator[Session, None, None]:
    """Transactional context manager for service and script operations."""
    factory = session_factory or get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a database session per request."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def init_db(engine: Engine | None = None, drop_existing: bool = False) -> None:
    """Create all ORM tables in the target database."""
    import backend.app.models  # noqa: F401 — ensure all models are registered on Base.metadata

    target_engine = engine or get_engine()
    if drop_existing:
        Base.metadata.drop_all(bind=target_engine)
    Base.metadata.create_all(bind=target_engine)
