"""Regression tests for cumulative arrival-day route constraints."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flightscanner.core.route_filter import (
    arrival_day_offset,
    arrival_within_limit,
    filter_prices_by_route,
)
from flightscanner.interfaces import FlightDirection, FlightInfo, FlightPrice
from flightscanner.models.database import PriceHistory, Route, init_db
from flightscanner.scheduler.price_monitor import PriceMonitorScheduler

DEPARTURE_DATE = date(2026, 9, 10)
SCRAPED_AT = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)


def _route(
    outbound_limit: int | None,
    return_limit: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        max_arrival_day_offset=outbound_limit,
        ret_max_arrival_day_offset=return_limit,
    )


def _flight(
    flight_no: str,
    day_offset: int | None,
    *,
    direction: FlightDirection = FlightDirection.DEPARTURE,
    departure_time: str = "08:00",
    arrival_time: str = "10:00",
    arrival_date: object = Ellipsis,
) -> FlightInfo:
    if arrival_date is Ellipsis:
        actual_arrival_date = (
            DEPARTURE_DATE + timedelta(days=day_offset)
            if day_offset is not None
            else None
        )
    else:
        actual_arrival_date = arrival_date

    return FlightInfo(
        flight_no=flight_no,
        airline="测试航空",
        departure_city="上海" if direction == FlightDirection.DEPARTURE else "北京",
        arrival_city="北京" if direction == FlightDirection.DEPARTURE else "上海",
        departure_time=departure_time,
        arrival_time=arrival_time,
        departure_date=DEPARTURE_DATE,
        direction=direction,
        arrival_date=actual_arrival_date,  # type: ignore[arg-type]
    )


def _price(
    flight: FlightInfo,
    *,
    return_flight: FlightInfo | None = None,
    price: str = "500",
) -> FlightPrice:
    return FlightPrice(
        flight_info=flight,
        return_flight_info=return_flight,
        price=Decimal(price),
        currency="CNY",
        seat_class="经济舱",
        available_seats=5,
        scraped_at=SCRAPED_AT,
        source="test",
    )


@pytest.mark.parametrize("day_offset", [0, 1, 2, 3])
def test_arrival_day_offset_uses_explicit_calendar_dates(day_offset: int) -> None:
    assert arrival_day_offset(_flight(f"D{day_offset}", day_offset)) == day_offset


def test_arrival_day_offset_explicit_date_takes_precedence_over_clock_order() -> None:
    same_day_with_overnight_clocks = _flight(
        "DATE0",
        0,
        departure_time="23:30",
        arrival_time="01:00",
    )
    two_days_with_same_day_clocks = _flight(
        "DATE2",
        2,
        departure_time="08:00",
        arrival_time="10:00",
    )

    assert arrival_day_offset(same_day_with_overnight_clocks) == 0
    assert arrival_day_offset(two_days_with_same_day_clocks) == 2


@pytest.mark.parametrize(
    ("departure_time", "arrival_time", "expected"),
    [
        ("23:30", "01:00", 1),
        ("08:00", "10:00", 0),
    ],
)
def test_negative_explicit_date_falls_back_to_clock_inference(
    departure_time: str,
    arrival_time: str,
    expected: int,
) -> None:
    flight = _flight(
        "NEGATIVE",
        None,
        departure_time=departure_time,
        arrival_time=arrival_time,
        arrival_date=DEPARTURE_DATE - timedelta(days=1),
    )

    assert arrival_day_offset(flight) == expected


@pytest.mark.parametrize(
    ("departure_time", "arrival_time", "expected"),
    [
        ("08:00", "10:00", 0),
        ("23:30", "01:00", 1),
        ("", "01:00", None),
        ("08:00", "", None),
        ("invalid", "10:00", None),
        ("08:00", "25:00", None),
    ],
)
def test_missing_arrival_date_uses_hhmm_fallback_or_stays_unknown(
    departure_time: str,
    arrival_time: str,
    expected: int | None,
) -> None:
    flight = _flight(
        "LEGACY",
        None,
        departure_time=departure_time,
        arrival_time=arrival_time,
    )

    assert arrival_day_offset(flight) == expected


@pytest.mark.parametrize(
    ("maximum_offset", "expected_offsets"),
    [
        (0, [0]),
        (1, [0, 1]),
        (2, [0, 1, 2]),
        (None, [0, 1, 2, 3]),
    ],
)
def test_arrival_limit_is_a_cumulative_upper_bound(
    maximum_offset: int | None,
    expected_offsets: list[int],
) -> None:
    accepted = [
        offset
        for offset in range(4)
        if arrival_within_limit(_flight(f"D{offset}", offset), maximum_offset)
    ]

    assert accepted == expected_offsets


def test_zero_is_an_active_same_day_only_constraint() -> None:
    prices = [
        _price(_flight("D0", 0)),
        _price(_flight("D1", 1)),
    ]

    filtered = filter_prices_by_route(_route(0), prices)

    assert [item.flight_info.flight_no for item in filtered] == ["D0"]


def test_unknown_and_virtual_return_are_rejected_only_when_limit_is_configured() -> None:
    unknown = _flight(
        "UNKNOWN",
        None,
        departure_time="",
        arrival_time="",
    )
    virtual_return = _flight(
        "VIRTUAL_RETURN",
        0,
        direction=FlightDirection.RETURN,
        departure_time="00:00",
        arrival_time="00:00",
    )

    assert arrival_day_offset(virtual_return) is None
    assert arrival_within_limit(unknown, 2) is False
    assert arrival_within_limit(virtual_return, 2) is False
    assert arrival_within_limit(unknown, None) is True
    assert arrival_within_limit(virtual_return, None) is True


@pytest.mark.parametrize(
    ("outbound_offset", "return_offset", "expected"),
    [
        (0, 0, True),
        (1, 2, True),
        (2, 0, False),
        (0, 3, False),
    ],
)
def test_roundtrip_rejects_itinerary_when_either_leg_exceeds_its_own_limit(
    outbound_offset: int,
    return_offset: int,
    expected: bool,
) -> None:
    outbound = _flight("OUT", outbound_offset)
    return_flight = _flight(
        "RET",
        return_offset,
        direction=FlightDirection.RETURN,
    )
    combined = _price(outbound, return_flight=return_flight)

    assert bool(filter_prices_by_route(_route(1, 2), [combined])) is expected


@pytest.mark.parametrize("return_limit", [None, 0, 2])
def test_roundtrip_without_return_leg_fails_closed(
    return_limit: int | None,
) -> None:
    route = _route(1, return_limit)
    route.trip_type = "roundtrip"
    orphan = _price(_flight("ORPHAN-OUT", 0))

    assert filter_prices_by_route(route, [orphan]) == []


def test_oneway_without_return_leg_is_unchanged() -> None:
    route = _route(1)
    route.trip_type = "oneway"
    outbound = _price(_flight("ONEWAY-OUT", 0))

    assert filter_prices_by_route(route, [outbound]) == [outbound]


def test_roundtrip_split_legs_survive_only_the_pre_pairing_filter() -> None:
    route = _route(1, 2)
    route.trip_type = "roundtrip"
    outbound = _price(_flight("SPLIT-OUT", 1))
    return_leg = _price(
        _flight("SPLIT-RET", 2, direction=FlightDirection.RETURN)
    )

    assert filter_prices_by_route(route, [outbound, return_leg]) == [return_leg]
    assert filter_prices_by_route(
        route,
        [outbound, return_leg],
        require_complete_roundtrip=False,
    ) == [outbound, return_leg]


def test_standalone_return_uses_return_limit_not_outbound_limit() -> None:
    route = _route(0, 2)
    return_d2 = _price(
        _flight("RET-D2", 2, direction=FlightDirection.RETURN)
    )
    return_d3 = _price(
        _flight("RET-D3", 3, direction=FlightDirection.RETURN)
    )

    filtered = filter_prices_by_route(route, [return_d2, return_d3])

    assert [item.flight_info.flight_no for item in filtered] == ["RET-D2"]


def test_scheduler_filter_is_identical_to_shared_filter() -> None:
    route = _route(1, 2)
    prices = [
        _price(_flight("OUT-D0", 0)),
        _price(_flight("OUT-D1", 1)),
        _price(_flight("OUT-D2", 2)),
        _price(_flight("RET-D2", 2, direction=FlightDirection.RETURN)),
        _price(_flight("RET-D3", 3, direction=FlightDirection.RETURN)),
        _price(
            _flight("RT-OUT-D1", 1),
            return_flight=_flight(
                "RT-RET-D2",
                2,
                direction=FlightDirection.RETURN,
            ),
        ),
        _price(
            _flight("RT-OUT-D0", 0),
            return_flight=_flight(
                "RT-RET-D3",
                3,
                direction=FlightDirection.RETURN,
            ),
        ),
    ]
    scheduler = PriceMonitorScheduler.__new__(PriceMonitorScheduler)

    shared = filter_prices_by_route(route, prices)
    scheduled = scheduler._apply_route_filters(route, prices)

    assert scheduled == shared
    assert [item.flight_info.flight_no for item in scheduled] == [
        "OUT-D0",
        "OUT-D1",
        "RET-D2",
        "RT-OUT-D1",
    ]


async def test_normal_scrape_marks_filtered_out_without_persisting_quotes() -> None:
    engine, session_factory = init_db("sqlite:///:memory:")
    with session_factory() as session:
        route = Route(
            origin="上海",
            destination="北京",
            target_date=DEPARTURE_DATE,
            target_price=Decimal("800"),
            scrape_interval=6,
            is_active=1,
            max_arrival_day_offset=0,
        )
        session.add(route)
        session.commit()
        session.refresh(route)
        route_id = route.id

        scheduler = PriceMonitorScheduler.__new__(PriceMonitorScheduler)
        scheduler.scrapers = []
        scheduler._SessionLocal = session_factory
        scheduler._scrape_all_platforms = AsyncMock(
            return_value=[_price(_flight("TOO-LATE", 1))]
        )

        await scheduler.scrape_route(route)

    with session_factory() as verification_session:
        stored_route = verification_session.get(Route, route_id)
        assert stored_route is not None
        assert stored_route.last_flight_status == "filtered_out"
        assert (
            verification_session.query(PriceHistory)
            .filter(PriceHistory.route_id == route_id)
            .count()
            == 0
        )
    engine.dispose()


async def test_successful_normal_scrape_clears_filtered_out_status() -> None:
    engine, session_factory = init_db("sqlite:///:memory:")
    with session_factory() as session:
        route = Route(
            origin="上海",
            destination="北京",
            target_date=DEPARTURE_DATE,
            target_price=Decimal("800"),
            scrape_interval=6,
            is_active=1,
            max_arrival_day_offset=1,
            last_flight_status="filtered_out",
        )
        session.add(route)
        session.commit()
        session.refresh(route)
        route_id = route.id

        scheduler = PriceMonitorScheduler.__new__(PriceMonitorScheduler)
        scheduler.scrapers = []
        scheduler.notifiers = []
        scheduler._SessionLocal = session_factory
        scheduler._scrape_all_platforms = AsyncMock(
            return_value=[_price(_flight("MATCHING", 1))]
        )
        scheduler._compute_price_stats = lambda _history: {"batch_count": 1}
        scheduler._compute_recent_3d_low = lambda _history: None
        scheduler._compute_consecutive_declining_batches = (
            lambda _history, **_kwargs: False
        )
        scheduler._update_route_recent_low = lambda *_args: None
        scheduler._should_notify = lambda *_args, **_kwargs: (False, "")
        scheduler._maybe_log_prediction = AsyncMock()
        scheduler._check_buy_plans = AsyncMock()

        await scheduler.scrape_route(route)

    with session_factory() as verification_session:
        stored_route = verification_session.get(Route, route_id)
        assert stored_route is not None
        assert stored_route.last_flight_status == "available"
        assert (
            verification_session.query(PriceHistory)
            .filter(PriceHistory.route_id == route_id)
            .count()
            == 1
        )
    engine.dispose()
