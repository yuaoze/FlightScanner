"""Database session dependency injection for FastAPI."""

from threading import Lock
from typing import Generator

from sqlalchemy.orm import Session

from flightscanner.models.database import init_db

_engine = None
_SessionLocal = None
_db_init_lock = Lock()


def _get_session_factory():
    """Initialize the default database on first real request.

    Importing an API router should be side-effect free.  The previous eager
    initialization migrated/opened ``flightscanner.db`` during test discovery,
    schema generation, and CLI imports even when ``get_db`` was overridden.
    """
    global _engine, _SessionLocal
    if _SessionLocal is None:
        with _db_init_lock:
            if _SessionLocal is None:
                _engine, _SessionLocal = init_db()
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """Yield a database session, closing it after the request."""
    db = _get_session_factory()()
    try:
        yield db
    finally:
        db.close()
