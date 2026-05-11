"""Database session dependency injection for FastAPI."""

from typing import Generator

from sqlalchemy.orm import Session

from flightscanner.models.database import init_db

_engine, _SessionLocal = init_db()


def get_db() -> Generator[Session, None, None]:
    """Yield a database session, closing it after the request."""
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
