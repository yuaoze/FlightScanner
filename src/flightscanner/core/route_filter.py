"""Shared route constraints for live scrapes and historical reads.

Arrival-day limits are cumulative upper bounds: ``0`` keeps same-day arrivals,
``1`` keeps D+0 and D+1, ``2`` keeps D+0 through D+2, and ``None`` disables the
constraint.  Old flight rows without ``arrival_date`` fall back to their clock
times, which can reliably distinguish same-day from next-day only.
"""

from __future__ import annotations

from typing import Any

from flightscanner.interfaces import FlightDirection, FlightInfo, FlightPrice


def hhmm_to_minutes(hhmm: str | None) -> int | None:
    """Convert ``HH:MM`` to minutes after midnight, returning None if invalid."""
    if not hhmm:
        return None
    try:
        hour, minute = map(int, hhmm.split(":"))
    except (ValueError, AttributeError):
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour * 60 + minute


def in_time_window(
    time_str: str | None,
    from_minute: int | None,
    to_minute: int | None,
) -> bool:
    """Return whether a time is inside an inclusive optional window."""
    if from_minute is None and to_minute is None:
        return True
    minute = hhmm_to_minutes(time_str)
    if minute is None:
        # Legacy scraper rows may not contain a usable time.  Keep the existing
        # permissive behaviour rather than silently deleting their history.
        return True
    if from_minute is not None and minute < from_minute:
        return False
    if to_minute is not None and minute > to_minute:
        return False
    return True


def arrival_day_offset(flight: Any) -> int | None:
    """Return calendar days from departure to arrival for a flight-like object.

    Explicit dates take precedence.  An invalid negative date delta falls back
    to clock-time inference instead of being treated as a permissive negative
    offset.  ``VIRTUAL_RETURN`` is a Ctrip placeholder and intentionally stays
    unknown rather than being mislabelled as a same-day 00:00 arrival.
    """
    if getattr(flight, "flight_no", None) == "VIRTUAL_RETURN":
        return None

    departure_date = getattr(flight, "departure_date", None)
    actual_arrival_date = getattr(flight, "arrival_date", None)
    if departure_date is not None and actual_arrival_date is not None:
        try:
            offset = (actual_arrival_date - departure_date).days
        except (AttributeError, TypeError):
            offset = -1
        if offset >= 0:
            return int(offset)

    departure_minute = hhmm_to_minutes(getattr(flight, "departure_time", None))
    arrival_minute = hhmm_to_minutes(getattr(flight, "arrival_time", None))
    if departure_minute is None or arrival_minute is None:
        return None
    return 1 if arrival_minute < departure_minute else 0


def arrival_within_limit(flight: Any, maximum_offset: int | None) -> bool:
    """Check a cumulative arrival-day upper bound.

    When a user explicitly configures a limit, an itinerary whose offset cannot
    be established is excluded: allowing an unknown multi-day arrival would
    make the constraint look precise while silently violating it.
    """
    if maximum_offset is None:
        return True
    offset = arrival_day_offset(flight)
    return offset is not None and offset <= maximum_offset


def leg_matches_route(route: Any, flight: Any, *, is_return: bool = False) -> bool:
    """Apply airport, clock-time and arrival-day constraints to one leg."""
    departure_airport = getattr(route, "dep_airport_code", None)
    arrival_airport = getattr(route, "arr_airport_code", None)

    if is_return:
        expected_departure_airport = arrival_airport
        expected_arrival_airport = departure_airport
        departure_from = hhmm_to_minutes(getattr(route, "ret_dep_time_from", None))
        departure_to = hhmm_to_minutes(getattr(route, "ret_dep_time_to", None))
        arrival_from = hhmm_to_minutes(getattr(route, "ret_arr_time_from", None))
        arrival_to = hhmm_to_minutes(getattr(route, "ret_arr_time_to", None))
        maximum_offset = getattr(route, "ret_max_arrival_day_offset", None)
    else:
        expected_departure_airport = departure_airport
        expected_arrival_airport = arrival_airport
        departure_from = hhmm_to_minutes(getattr(route, "dep_time_from", None))
        departure_to = hhmm_to_minutes(getattr(route, "dep_time_to", None))
        arrival_from = hhmm_to_minutes(getattr(route, "arr_time_from", None))
        arrival_to = hhmm_to_minutes(getattr(route, "arr_time_to", None))
        maximum_offset = getattr(route, "max_arrival_day_offset", None)

    actual_departure_airport = getattr(flight, "departure_airport_code", None)
    actual_arrival_airport = getattr(flight, "arrival_airport_code", None)
    if (
        expected_departure_airport
        and actual_departure_airport
        and actual_departure_airport != expected_departure_airport
    ):
        return False
    if (
        expected_arrival_airport
        and actual_arrival_airport
        and actual_arrival_airport != expected_arrival_airport
    ):
        return False
    if not in_time_window(
        getattr(flight, "departure_time", None), departure_from, departure_to
    ):
        return False
    if not in_time_window(
        getattr(flight, "arrival_time", None), arrival_from, arrival_to
    ):
        return False
    return arrival_within_limit(flight, maximum_offset)


def itinerary_matches_route(
    route: Any,
    outbound: Any,
    return_flight: Any | None = None,
) -> bool:
    """Return whether an outbound/optional-return itinerary meets a route."""
    # A round-trip quote is only usable when both legs are known.  Legacy
    # orphan rows (``return_flight_id IS NULL``) must not bypass return-leg
    # arrival/time constraints or be presented as a round-trip total price.
    if (
        getattr(route, "trip_type", "oneway") == "roundtrip"
        and return_flight is None
    ):
        return False
    if not leg_matches_route(route, outbound):
        return False
    if return_flight is not None and not leg_matches_route(
        route, return_flight, is_return=True
    ):
        return False
    return True


def filter_prices_by_route(
    route: Any,
    prices: list[FlightPrice],
    *,
    require_complete_roundtrip: bool = True,
) -> list[FlightPrice]:
    """Filter live or reconstructed price records with the shared semantics.

    ``require_complete_roundtrip`` stays enabled for persisted/current data so
    an orphan outbound row can never masquerade as a round-trip total.  The
    scheduler disables it only while filtering separate outbound/return legs
    immediately before those legs are paired into a complete itinerary.
    """
    filtered: list[FlightPrice] = []
    for price in prices:
        flight: FlightInfo = price.flight_info
        if price.return_flight_info is not None:
            matches = itinerary_matches_route(route, flight, price.return_flight_info)
        elif flight.direction == FlightDirection.RETURN:
            matches = leg_matches_route(route, flight, is_return=True)
        elif not require_complete_roundtrip:
            matches = leg_matches_route(route, flight)
        else:
            matches = itinerary_matches_route(route, flight)
        if matches:
            filtered.append(price)
    return filtered
