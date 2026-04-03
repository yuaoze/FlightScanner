"""Unit tests for PriceMonitorScheduler pinned-flight helpers."""

import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from flightscanner.interfaces import FlightDirection, FlightInfo, FlightPrice
from flightscanner.models.database import Route, init_db
from flightscanner.core.services import RouteService
from flightscanner.scheduler.price_monitor import (
    PriceMonitorScheduler,
    _time_diff_minutes,
)


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_flight_price(
    flight_no: str,
    price: float = 500.0,
    seat_class: str = "经济舱",
    available_seats: Optional[int] = 5,
) -> FlightPrice:
    fi = FlightInfo(
        flight_no=flight_no,
        airline="测试航空",
        departure_city="上海",
        arrival_city="北京",
        departure_time="08:30",
        arrival_time="10:45",
        departure_date=date(2026, 10, 1),
        direction=FlightDirection.DEPARTURE,
    )
    return FlightPrice(
        flight_info=fi,
        price=Decimal(str(price)),
        currency="CNY",
        seat_class=seat_class,
        available_seats=available_seats,
        scraped_at=datetime.now(timezone.utc),
        source="qunar",
    )


def _make_route(**kwargs) -> Route:
    defaults = dict(
        id=1,
        origin="上海",
        destination="北京",
        target_date=date(2026, 10, 1),
        target_price=Decimal("400"),
        scrape_interval=6,
        is_active=1,
        trip_type="oneway",
        monitoring_mode="flight",
        outbound_flight_no="CA953",
        inbound_flight_no=None,
        pinned_seat_class=None,
        outbound_dep_time_ref=None,
        inbound_dep_time_ref=None,
        last_flight_status=None,
        return_date=None,
    )
    defaults.update(kwargs)
    route = MagicMock(spec=Route)
    for k, v in defaults.items():
        setattr(route, k, v)
    return route


# ── _time_diff_minutes ──────────────────────────────────────────────────────

class TestTimeDiffMinutes:
    def test_same_time(self):
        assert _time_diff_minutes("08:30", "08:30") == 0

    def test_one_hour(self):
        assert _time_diff_minutes("08:00", "09:00") == 60

    def test_reversed(self):
        assert _time_diff_minutes("10:00", "09:00") == 60

    def test_partial_hour(self):
        assert _time_diff_minutes("08:00", "08:45") == 45

    def test_invalid_input(self):
        # "invalid" cannot be parsed → treated as 00:00; diff with 08:00 = 480 min
        assert _time_diff_minutes("invalid", "08:00") == 480


# ── _match_pinned_flight ────────────────────────────────────────────────────

class TestMatchPinnedFlight:
    def test_found_available(self):
        prices = [
            _make_flight_price("CA953", price=500),
            _make_flight_price("CA954", price=400),
        ]
        fp, status = PriceMonitorScheduler._match_pinned_flight(prices, "CA953", None)
        assert status == "available"
        assert fp is not None
        assert fp.flight_info.flight_no == "CA953"

    def test_not_found(self):
        prices = [_make_flight_price("CA954", price=400)]
        fp, status = PriceMonitorScheduler._match_pinned_flight(prices, "CA953", None)
        assert status == "not_found"
        assert fp is None

    def test_sold_out(self):
        prices = [_make_flight_price("CA953", price=400, available_seats=0)]
        fp, status = PriceMonitorScheduler._match_pinned_flight(prices, "CA953", None)
        assert status == "sold_out"
        assert fp is None

    def test_seat_class_filter_match(self):
        prices = [
            _make_flight_price("CA953", price=500, seat_class="经济舱"),
            _make_flight_price("CA953", price=1200, seat_class="商务舱"),
        ]
        fp, status = PriceMonitorScheduler._match_pinned_flight(prices, "CA953", "经济舱")
        assert status == "available"
        assert fp.seat_class == "经济舱"
        assert fp.price == Decimal("500")

    def test_seat_class_filter_no_match(self):
        prices = [_make_flight_price("CA953", price=500, seat_class="经济舱")]
        fp, status = PriceMonitorScheduler._match_pinned_flight(prices, "CA953", "商务舱")
        assert status == "not_found"

    def test_case_insensitive(self):
        prices = [_make_flight_price("ca953", price=500)]
        fp, status = PriceMonitorScheduler._match_pinned_flight(prices, "CA953", None)
        assert status == "available"

    def test_returns_cheapest(self):
        prices = [
            _make_flight_price("CA953", price=600),
            _make_flight_price("CA953", price=500),
            _make_flight_price("CA953", price=700),
        ]
        fp, status = PriceMonitorScheduler._match_pinned_flight(prices, "CA953", None)
        assert fp.price == Decimal("500")

    def test_available_seats_none_treated_as_available(self):
        """available_seats=None means unknown, not sold out."""
        prices = [_make_flight_price("CA953", price=500, available_seats=None)]
        fp, status = PriceMonitorScheduler._match_pinned_flight(prices, "CA953", None)
        assert status == "available"
        assert fp is not None


# ── _determine_flight_status ────────────────────────────────────────────────

class TestDetermineFlightStatus:
    def test_available(self):
        route = _make_route(outbound_dep_time_ref=None)
        fp = _make_flight_price("CA953")
        status = PriceMonitorScheduler._determine_flight_status(
            route, fp, None, "available", "available", "oneway", None
        )
        assert status == "available"

    def test_out_sold_out(self):
        route = _make_route()
        status = PriceMonitorScheduler._determine_flight_status(
            route, None, None, "sold_out", "available", "oneway", None
        )
        assert status == "sold_out"

    def test_out_not_found(self):
        route = _make_route()
        status = PriceMonitorScheduler._determine_flight_status(
            route, None, None, "not_found", "available", "oneway", None
        )
        assert status == "not_found"

    def test_schedule_changed_outbound(self):
        route = _make_route(outbound_dep_time_ref="08:30")
        # Build flight price with departure_time > 60 min different
        fp = _make_flight_price("CA953")
        fp.flight_info = MagicMock()
        fp.flight_info.departure_time = "11:00"  # 150 min diff
        status = PriceMonitorScheduler._determine_flight_status(
            route, fp, None, "available", "available", "oneway", None
        )
        assert status == "schedule_changed"

    def test_schedule_not_changed_within_60min(self):
        route = _make_route(outbound_dep_time_ref="08:30")
        fp = _make_flight_price("CA953")
        fp.flight_info = MagicMock()
        fp.flight_info.departure_time = "09:00"  # 30 min diff — OK
        status = PriceMonitorScheduler._determine_flight_status(
            route, fp, None, "available", "available", "oneway", None
        )
        assert status == "available"

    def test_roundtrip_inbound_not_found(self):
        route = _make_route(trip_type="roundtrip", inbound_dep_time_ref=None)
        out_fp = _make_flight_price("CA953")
        status = PriceMonitorScheduler._determine_flight_status(
            route, out_fp, None, "available", "not_found", "roundtrip", "CA954"
        )
        assert status == "not_found"

    def test_roundtrip_inbound_sold_out(self):
        route = _make_route(trip_type="roundtrip", inbound_dep_time_ref=None)
        out_fp = _make_flight_price("CA953")
        status = PriceMonitorScheduler._determine_flight_status(
            route, out_fp, None, "available", "sold_out", "roundtrip", "CA954"
        )
        assert status == "sold_out"


# ── update_flight_status (DB) ───────────────────────────────────────────────

class TestUpdateFlightStatus:
    @pytest.fixture
    def db_session(self):
        engine, SessionLocal = init_db("sqlite:///:memory:")
        session = SessionLocal()
        yield session
        session.close()

    def test_update_sets_status(self, db_session):
        svc = RouteService(db_session)
        route = svc.add_route(
            origin="上海",
            destination="北京",
            target_date=date(2026, 10, 1),
            target_price=Decimal("500"),
            monitoring_mode="flight",
            outbound_flight_no="CA953",
        )
        svc.update_flight_status(route.id, "sold_out")
        db_session.expire(route)
        db_session.refresh(route)
        assert route.last_flight_status == "sold_out"

    def test_update_nonexistent_route_is_noop(self, db_session):
        svc = RouteService(db_session)
        svc.update_flight_status(9999, "sold_out")  # should not raise


# ── _combine_roundtrip_prices ────────────────────────────────────────────────

def _make_rt_price(
    flight_no: str,
    price: float,
    direction: FlightDirection = FlightDirection.DEPARTURE,
    source: str = "qunar",
    return_flight_info=None,
) -> FlightPrice:
    fi = FlightInfo(
        flight_no=flight_no,
        airline="测试航空",
        departure_city="上海",
        arrival_city="北京",
        departure_time="08:30",
        arrival_time="10:45",
        departure_date=date(2026, 10, 1),
        direction=direction,
    )
    return FlightPrice(
        flight_info=fi,
        price=Decimal(str(price)),
        currency="CNY",
        seat_class="经济舱",
        available_seats=5,
        scraped_at=datetime.now(timezone.utc),
        source=source,
        return_flight_info=return_flight_info,
    )


class TestCombineRoundtripPrices:
    def test_already_combined_returned_as_is(self):
        ret_fi = FlightInfo(
            flight_no="CA954",
            airline="测试航空",
            departure_city="北京",
            arrival_city="上海",
            departure_time="18:00",
            arrival_time="20:00",
            departure_date=date(2026, 10, 7),
            direction=FlightDirection.RETURN,
        )
        combined = _make_rt_price("CA953", 1200, return_flight_info=ret_fi)
        result = PriceMonitorScheduler._combine_roundtrip_prices([combined])
        assert len(result) == 1
        assert result[0].price == Decimal("1200")

    def test_single_leg_records_paired(self):
        out_fp = _make_rt_price("CA953", 600, direction=FlightDirection.DEPARTURE)
        ret_fp = _make_rt_price("CA954", 550, direction=FlightDirection.RETURN)
        result = PriceMonitorScheduler._combine_roundtrip_prices([out_fp, ret_fp])
        assert len(result) == 1
        assert result[0].price == Decimal("1150")
        assert result[0].return_flight_info is not None

    def test_mixed_combined_and_single_leg_both_preserved(self):
        """Bug fix: combined records + single-leg records must all be kept."""
        ret_fi = FlightInfo(
            flight_no="MU5678",
            airline="东航",
            departure_city="北京",
            arrival_city="上海",
            departure_time="18:00",
            arrival_time="20:00",
            departure_date=date(2026, 10, 7),
            direction=FlightDirection.RETURN,
        )
        # Ctrip already-combined record
        ctrip_combined = _make_rt_price("MU1234", 1100, source="ctrip", return_flight_info=ret_fi)
        # Qunar single-leg records (fallback path)
        qunar_out = _make_rt_price("CA953", 600, direction=FlightDirection.DEPARTURE, source="qunar")
        qunar_ret = _make_rt_price("CA954", 550, direction=FlightDirection.RETURN, source="qunar")

        result = PriceMonitorScheduler._combine_roundtrip_prices(
            [ctrip_combined, qunar_out, qunar_ret]
        )
        # Should have both: ctrip combined + qunar paired
        assert len(result) == 2
        prices = sorted(r.price for r in result)
        assert prices == [Decimal("1100"), Decimal("1150")]

    def test_no_pairable_records_returns_original(self):
        """If no combined records and no return leg, return prices unchanged."""
        out_fp = _make_rt_price("CA953", 600, direction=FlightDirection.DEPARTURE)
        result = PriceMonitorScheduler._combine_roundtrip_prices([out_fp])
        assert result == [out_fp]
