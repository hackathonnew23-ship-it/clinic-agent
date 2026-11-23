"""Engine / session management.

SQLite for dev (WAL mode + busy timeout so concurrent writers queue instead of
failing), Postgres-ready: set DATABASE_URL to a postgresql+psycopg:// URL.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def make_engine(url: str) -> Engine:
    kwargs: dict = {}
    is_sqlite = url.startswith("sqlite")
    if is_sqlite:
        # timeout = SQLite busy timeout in seconds: concurrent writers wait their turn.
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
    engine = create_engine(url, **kwargs)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=15000")
            cur.close()

    return engine


def init_engine(url: str | None = None) -> Engine:
    """Create the global engine + session factory (tests pass their own URL)."""
    global _engine, _factory
    _engine = make_engine(url or get_settings().database_url)
    _factory = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def is_initialized() -> bool:
    return _engine is not None


def reset_engine() -> None:
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _factory = None


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _factory is None:
        init_engine()
    assert _factory is not None
    return _factory


def SessionLocal() -> Session:  # noqa: N802 - conventional name
    return get_session_factory()()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
