"""Datetime serialization helpers for the API layer.

Per CLAUDE.md, timestamps are stored as UTC in the DB but may be naive
(SQLite strips tzinfo). When sending to the frontend:

- For math-consumable fields (parsed by JS Date): return ISO UTC with a
  trailing 'Z' so JS interprets them correctly, e.g. "2026-05-09T01:41:00Z".
- For display-only fields (shown verbatim): convert UTC → CST (UTC+8) and
  format as "YYYY-MM-DD HH:MM" so Chinese users see Beijing time directly.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

CST = timezone(timedelta(hours=8))


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    """Serialize a datetime as ISO-8601 UTC with trailing 'Z'."""
    if dt is None:
        return None
    aware = _ensure_utc(dt).astimezone(timezone.utc)
    return aware.isoformat().replace("+00:00", "Z")


def fmt_cst(dt: Optional[datetime], fmt: str = "%Y-%m-%d %H:%M") -> Optional[str]:
    """Format a datetime in Beijing time (CST, UTC+8)."""
    if dt is None:
        return None
    return _ensure_utc(dt).astimezone(CST).strftime(fmt)
