"""Database session management for Streamlit UI.

This module provides utilities for managing database sessions in the
Streamlit web application context.
"""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy.orm import Session

from flightscanner.models.database import init_db


# Global engine and session factory (initialized once)
_engine, _SessionLocal = init_db()


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Get a database session with automatic cleanup.

    This context manager ensures proper session handling in Streamlit.

    Yields:
        SQLAlchemy session object.

    Example:
        with get_session() as session:
            service = RouteService(session)
            routes = service.get_all_routes()
    """
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_session_local():
    """Get the session factory for manual session creation.

    Returns:
        SessionLocal factory class.
    """
    return _SessionLocal
