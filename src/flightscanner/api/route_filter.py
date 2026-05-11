"""Standalone time-window/airport filter for historical price reads.

Mirrors `PriceMonitorScheduler._apply_route_filters` semantics but operates
without requiring a scheduler instance — used by API endpoints to retroactively
filter cached price history when the route's time windows change.
"""

from typing import List, Optional

from flightscanner.interfaces import FlightDirection, FlightInfo, FlightPrice
from flightscanner.models.database import Route


def _hhmm_to_minutes(hhmm: Optional[str]) -> Optional[int]:
    if not hhmm:
        return None
    try:
        h, m = map(int, hhmm.split(":"))
        return h * 60 + m
    except (ValueError, AttributeError):
        return None


def _in_window(
    time_str: Optional[str], from_min: Optional[int], to_min: Optional[int]
) -> bool:
    if from_min is None and to_min is None:
        return True
    m = _hhmm_to_minutes(time_str or "00:00")
    if m is None:
        return True  # missing time data — let it through
    if from_min is not None and m < from_min:
        return False
    if to_min is not None and m > to_min:
        return False
    return True


def filter_history_by_route(route: Route, prices: List[FlightPrice]) -> List[FlightPrice]:
    """Return only price records whose flight times fall within the route's
    configured outbound + return windows. Records with no time data pass through.

    Empty windows (None) act as "no constraint" — matching the scheduler's filter.
    """
    dep_airport = getattr(route, "dep_airport_code", None)
    arr_airport = getattr(route, "arr_airport_code", None)
    dep_from = _hhmm_to_minutes(getattr(route, "dep_time_from", None))
    dep_to = _hhmm_to_minutes(getattr(route, "dep_time_to", None))
    arr_from = _hhmm_to_minutes(getattr(route, "arr_time_from", None))
    arr_to = _hhmm_to_minutes(getattr(route, "arr_time_to", None))
    ret_dep_from = _hhmm_to_minutes(getattr(route, "ret_dep_time_from", None))
    ret_dep_to = _hhmm_to_minutes(getattr(route, "ret_dep_time_to", None))
    ret_arr_from = _hhmm_to_minutes(getattr(route, "ret_arr_time_from", None))
    ret_arr_to = _hhmm_to_minutes(getattr(route, "ret_arr_time_to", None))

    if not any([
        dep_airport, arr_airport,
        dep_from, dep_to, arr_from, arr_to,
        ret_dep_from, ret_dep_to, ret_arr_from, ret_arr_to,
    ]):
        return prices

    def _outbound_ok(fi: FlightInfo) -> bool:
        if dep_airport and fi.departure_airport_code and fi.departure_airport_code != dep_airport:
            return False
        if arr_airport and fi.arrival_airport_code and fi.arrival_airport_code != arr_airport:
            return False
        if not _in_window(fi.departure_time, dep_from, dep_to):
            return False
        if not _in_window(fi.arrival_time, arr_from, arr_to):
            return False
        return True

    def _return_ok(fi: FlightInfo) -> bool:
        # Return leg airports are reversed: dep = original arrival, arr = original departure.
        if arr_airport and fi.departure_airport_code and fi.departure_airport_code != arr_airport:
            return False
        if dep_airport and fi.arrival_airport_code and fi.arrival_airport_code != dep_airport:
            return False
        if not _in_window(fi.departure_time, ret_dep_from, ret_dep_to):
            return False
        if not _in_window(fi.arrival_time, ret_arr_from, ret_arr_to):
            return False
        return True

    out: List[FlightPrice] = []
    for fp in prices:
        fi = fp.flight_info
        if fp.return_flight_info is not None:
            if not _outbound_ok(fi):
                continue
            if not _return_ok(fp.return_flight_info):
                continue
        elif fi.direction == FlightDirection.RETURN:
            if not _return_ok(fi):
                continue
        else:
            if not _outbound_ok(fi):
                continue
        out.append(fp)
    return out
