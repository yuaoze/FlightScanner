"""Database session dependency injection for FastAPI."""

from threading import Lock
from typing import Generator

from sqlalchemy.orm import Session

from flightscanner.models.database import init_db
from flightscanner.utils.config import settings

_engine = None
_SessionLocal = None
_db_init_lock = Lock()


def _get_session_factory():
    """Defer database initialization so router imports cannot open or migrate it."""
    global _engine, _SessionLocal
    if _SessionLocal is None:
        with _db_init_lock:
            if _SessionLocal is None:
                _engine, _SessionLocal = init_db(settings.database_url)
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """Yield a database session, closing it after the request."""
    db = _get_session_factory()()
    try:
        yield db
    finally:
        db.close()
