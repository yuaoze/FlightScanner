"""Compatibility facade for the shared route filter implementation."""

from typing import Any

from flightscanner.core.route_filter import (
    filter_prices_by_route,
    hhmm_to_minutes,
    in_time_window,
)
from flightscanner.interfaces import FlightPrice


def _hhmm_to_minutes(hhmm: str | None) -> int | None:
    return hhmm_to_minutes(hhmm)


def _in_window(
    time_str: str | None, from_min: int | None, to_min: int | None
) -> bool:
    return in_time_window(time_str, from_min, to_min)


def filter_history_by_route(
    route: Any, prices: list[FlightPrice]
) -> list[FlightPrice]:
    """Filter cached prices using the same rules as the live scheduler."""
    return filter_prices_by_route(route, prices)
