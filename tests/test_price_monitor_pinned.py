"""Regression tests for pinned-flight helpers and the persisted scrape pipeline."""

import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from flightscanner.interfaces import FlightDirection, FlightInfo, FlightPrice
from flightscanner.models.database import Flight, PriceHistory, Route, init_db
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

    @pytest.mark.parametrize("invalid_price", [0, -50])
    @pytest.mark.parametrize("has_valid_quote", [False, True])
    def test_nonpositive_prices_are_not_available(self, invalid_price, has_valid_quote):
        prices = [_make_flight_price("CA953", price=invalid_price)]
        valid = _make_flight_price("CA953", price=500)
        if has_valid_quote:
            prices.append(valid)

        fp, status = PriceMonitorScheduler._match_pinned_flight(prices, "CA953", None)

        assert status == ("available" if has_valid_quote else "not_found")
        assert fp is (valid if has_valid_quote else None)

    @pytest.mark.parametrize("has_single_leg", [False, True])
    def test_complete_roundtrip_is_not_a_single_leg(self, has_single_leg):
        complete = _make_flight_price("CA953", price=100)
        complete.return_flight_info = _make_flight_price("CA954").flight_info
        single_leg = _make_flight_price("CA953", price=500)
        prices = [complete, single_leg] if has_single_leg else [complete]

        fp, status = PriceMonitorScheduler._match_pinned_flight(prices, "CA953", None)

        assert status == ("available" if has_single_leg else "not_found")
        assert fp is (single_leg if has_single_leg else None)


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

    def test_no_pairable_records_returns_empty(self):
        """If no combined records and no return leg, drop orphan single-leg data."""
        out_fp = _make_rt_price("CA953", 600, direction=FlightDirection.DEPARTURE)
        result = PriceMonitorScheduler._combine_roundtrip_prices([out_fp])
        assert result == []

    def test_different_sources_are_never_paired(self):
        """An outbound and return fare from different platforms is not a product."""
        out_fp = _make_rt_price(
            "CA953", 600,
            direction=FlightDirection.DEPARTURE,
            source="qunar",
        )
        ret_fp = _make_rt_price(
            "MU5102", 450,
            direction=FlightDirection.RETURN,
            source="tongcheng",
        )

        result = PriceMonitorScheduler._combine_roundtrip_prices([out_fp, ret_fp])

        assert result == []

    def test_different_cabin_or_currency_are_never_paired(self):
        """A combined quote must retain one comparable currency/cabin scope."""
        out_fp = _make_rt_price(
            "MU5101", 500,
            direction=FlightDirection.DEPARTURE,
            source="tongcheng",
        )
        business_return = _make_rt_price(
            "MU5102", 450,
            direction=FlightDirection.RETURN,
            source="tongcheng",
        )
        business_return.seat_class = "商务舱"
        usd_return = _make_rt_price(
            "MU5103", 80,
            direction=FlightDirection.RETURN,
            source="tongcheng",
        )
        usd_return.currency = "USD"

        result = PriceMonitorScheduler._combine_roundtrip_prices(
            [out_fp, business_return, usd_return]
        )

        assert result == []

    def test_unmatched_source_does_not_contaminate_other_platform_pair(self):
        """Keep a valid same-source pair while dropping another source's orphan."""
        orphan_out = _make_rt_price(
            "CA953", 600,
            direction=FlightDirection.DEPARTURE,
            source="qunar",
        )
        tc_out = _make_rt_price(
            "MU5101", 500,
            direction=FlightDirection.DEPARTURE,
            source="tongcheng",
        )
        tc_ret = _make_rt_price(
            "MU5102", 450,
            direction=FlightDirection.RETURN,
            source="tongcheng",
        )

        result = PriceMonitorScheduler._combine_roundtrip_prices(
            [orphan_out, tc_out, tc_ret]
        )

        assert len(result) == 1
        assert result[0].source == "tongcheng"
        assert result[0].price == Decimal("950")


# ── pinned scrape integration (real SQLite, no external I/O) ─────────────────

@pytest.fixture
def pinned_scheduler():
    engine, session_factory = init_db("sqlite:///:memory:")
    scheduler = PriceMonitorScheduler.__new__(PriceMonitorScheduler)
    scheduler._SessionLocal = session_factory
    scheduler.scrapers = [MagicMock(max_results=20)]
    scheduler.notifiers = [MagicMock()]
    scheduler.analyzer = MagicMock()
    scheduler._scrape_all_platforms = AsyncMock(return_value=[])
    # Force the notification gate open: empty-result tests must stop upstream,
    # not pass merely because no notifier or a cooldown suppressed an alert.
    scheduler._should_notify = MagicMock(return_value=(True, "target_hit"))
    scheduler._is_cooldown_active = MagicMock(return_value=False)
    scheduler._get_ai_brief_for_notify = AsyncMock(return_value=None)
    scheduler._send_alert = AsyncMock(return_value=True)
    scheduler._maybe_log_prediction = AsyncMock()
    scheduler._check_buy_plans = AsyncMock()
    try:
        yield scheduler
    finally:
        engine.dispose()


def _persist_pinned_route(scheduler, **overrides):
    values = dict(
        origin="上海",
        destination="北京",
        target_date=date(2026, 10, 1),
        return_date=date(2026, 10, 7),
        target_price=Decimal("2000"),
        scrape_interval=6,
        is_active=1,
        trip_type="roundtrip",
        monitoring_mode="flight",
        outbound_flight_no="CA953",
        inbound_flight_no="CA954",
    )
    values.update(overrides)
    with scheduler._SessionLocal() as session:
        route = Route(**values)
        session.add(route)
        session.commit()
        session.refresh(route)
        return route


def _pinned_leg(price, *, inbound=False, source="qunar", **kwargs):
    """Model one-way search responses, including DEPARTURE on the return search."""
    fp = _make_flight_price("CA954" if inbound else "CA953", price=price, **kwargs)
    fp.source = source
    if inbound:
        fp.flight_info = replace(
            fp.flight_info,
            departure_city="北京",
            arrival_city="上海",
            departure_date=date(2026, 10, 7),
            arrival_date=date(2026, 10, 7),
            departure_time="18:00",
            arrival_time="20:00",
        )
    else:
        fp.flight_info = replace(fp.flight_info, arrival_date=date(2026, 10, 1))
    return fp


def _assert_no_pinned_quotes(scheduler, route, status):
    with scheduler._SessionLocal() as session:
        stored_route = session.get(Route, route.id)
        assert stored_route.last_flight_status == status
        assert session.query(PriceHistory).filter_by(route_id=route.id).count() == 0
        assert session.query(Flight).count() == 0
        assert stored_route.recent_3d_low is None
        assert stored_route.last_notified_at is None
    scheduler._should_notify.assert_not_called()
    scheduler._get_ai_brief_for_notify.assert_not_called()
    scheduler._send_alert.assert_not_called()
    scheduler._maybe_log_prediction.assert_not_called()
    scheduler._check_buy_plans.assert_not_called()


def _assert_pinned_success(scheduler, route, expected_quotes, expected_status="available"):
    """Downstream consumers must see only persisted, legal quotes and their best."""
    scheduler._send_alert.assert_awaited_once()
    scheduler._maybe_log_prediction.assert_awaited_once()
    scheduler._check_buy_plans.assert_awaited_once()
    scheduler._should_notify.assert_called_once()
    best = scheduler._send_alert.await_args.args[0]
    expected_best = min(expected_quotes, key=lambda quote: quote[1])
    assert (best.source, best.price) == expected_best
    assert scheduler._should_notify.call_args.args[1] == best.price
    assert scheduler._check_buy_plans.await_args.args[2] is best

    prediction_session, prediction_route, history = (
        scheduler._maybe_log_prediction.await_args.args
    )
    assert prediction_session.get_bind().url.database == ":memory:"
    assert prediction_route.id == route.id
    assert sorted((fp.source, fp.price) for fp in history) == sorted(expected_quotes)
    assert {fp.batch_id for fp in history} == {best.batch_id}
    assert best.batch_id.startswith(f"route_{route.id}_")
    assert scheduler._check_buy_plans.await_args.args[3] == history
    assert scheduler._check_buy_plans.await_args.args[4]["batch_count"] == 1
    assert scheduler._check_buy_plans.await_args.args[5] == 1
    for fp in history:
        assert fp.flight_info.direction == FlightDirection.DEPARTURE
        if route.trip_type == "roundtrip":
            assert fp.return_flight_info is not None
            assert fp.return_flight_info.direction == FlightDirection.RETURN
        else:
            assert fp.return_flight_info is None

    with scheduler._SessionLocal() as session:
        rows = session.query(PriceHistory).filter_by(route_id=route.id).all()
        assert sorted((row.source, row.price) for row in rows) == sorted(expected_quotes)
        assert {row.batch_id for row in rows} == {best.batch_id}
        stored_route = session.get(Route, route.id)
        assert stored_route.last_flight_status == expected_status
        assert stored_route.recent_3d_low == best.price
        assert stored_route.last_notified_price == best.price


async def test_pinned_roundtrip_keeps_legal_platform_pairs_and_selects_legal_best(
    pinned_scheduler,
):
    scheduler = pinned_scheduler
    route = _persist_pinned_route(scheduler, pinned_seat_class="经济舱")
    cheapest_outbound = _pinned_leg(100)
    # Even a wrongly labelled outbound response must be normalized by search leg.
    cheapest_outbound.flight_info.direction = FlightDirection.RETURN
    unrelated = _make_flight_price("CA999", price=1)
    scheduler._scrape_all_platforms.side_effect = [
        [
            cheapest_outbound,
            _pinned_leg(150),
            _pinned_leg(300, source="ctrip"),
            unrelated,
            _pinned_leg(2, available_seats=0),
            _pinned_leg(0),
            _pinned_leg(-10),
            _pinned_leg(10, seat_class="商务舱"),
        ],
        [
            _pinned_leg(700, inbound=True, available_seats=3),
            _pinned_leg(200, inbound=True, source="ctrip", available_seats=2),
            _pinned_leg(1, inbound=True, available_seats=0),
            _pinned_leg(0, inbound=True),
            _pinned_leg(5, inbound=True, seat_class="商务舱"),
        ],
    ]

    await scheduler.scrape_route(route)

    _assert_pinned_success(scheduler, route, [
        ("qunar", Decimal("800")),
        ("qunar", Decimal("850")),
        ("ctrip", Decimal("500")),
    ])
    # The tempting cross-platform 100 + 200 = 300 is not a purchasable quote.
    with scheduler._SessionLocal() as session:
        for row in session.query(PriceHistory).filter_by(route_id=route.id).all():
            assert row.currency == "CNY"
            assert row.seat_class == "经济舱"
            assert row.available_seats == (3 if row.source == "qunar" else 2)
            assert row.flight.flight_no == "CA953"
            assert row.flight.direction == FlightDirection.DEPARTURE.value
            assert row.flight.departure_city == route.origin
            assert row.flight.arrival_city == route.destination
            assert row.flight.departure_date == route.target_date
            assert row.return_flight_id is not None
            assert row.return_flight.flight_no == "CA954"
            assert row.return_flight.direction == FlightDirection.RETURN.value
            assert row.return_flight.departure_city == route.destination
            assert row.return_flight.arrival_city == route.origin
            assert row.return_flight.departure_date == route.return_date

    calls = scheduler._scrape_all_platforms.await_args_list
    assert len(calls) == 2
    out_params, in_params = (call.args[0] for call in calls)
    assert (out_params.departure_city, out_params.arrival_city) == ("上海", "北京")
    assert out_params.departure_date == route.target_date
    assert (in_params.departure_city, in_params.arrival_city) == ("北京", "上海")
    assert in_params.departure_date == route.return_date
    assert out_params.return_date is None and in_params.return_date is None
    assert scheduler.scrapers[0].max_results == 100


@pytest.mark.parametrize(
    ("field", "value"),
    [("source", "ctrip"), ("currency", "USD"), ("seat_class", "商务舱")],
    ids=["cross-platform", "cross-currency", "cross-cabin"],
)
async def test_pinned_roundtrip_incompatible_scopes_never_persist_or_trigger_actions(
    pinned_scheduler, field, value,
):
    scheduler = pinned_scheduler
    route = _persist_pinned_route(scheduler)
    inbound = _pinned_leg(200, inbound=True)
    setattr(inbound, field, value)
    scheduler._scrape_all_platforms.side_effect = [[_pinned_leg(100)], [inbound]]

    await scheduler.scrape_route(route)

    assert scheduler._scrape_all_platforms.await_count == 2
    _assert_no_pinned_quotes(scheduler, route, "filtered_out")


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("departure_time", "16:00"),
        ("arrival_time", "22:00"),
        ("arrival_date", date(2026, 10, 9)),
    ],
    ids=["return-departure-window", "return-arrival-window", "return-arrival-day"],
)
async def test_pinned_roundtrip_filters_before_choosing_cheapest_return(
    pinned_scheduler, field, invalid_value,
):
    scheduler = pinned_scheduler
    route = _persist_pinned_route(
        scheduler,
        dep_time_from="08:00", dep_time_to="10:00",
        arr_time_from="10:00", arr_time_to="12:00",
        max_arrival_day_offset=0,
        ret_dep_time_from="17:00", ret_dep_time_to="19:00",
        ret_arr_time_from="19:00", ret_arr_time_to="21:00",
        ret_max_arrival_day_offset=1,
    )
    valid_return = _pinned_leg(300, inbound=True)
    # D+1 is legal for the return but not for the outbound.  Both search
    # responses initially carry DEPARTURE, so direction normalization matters.
    valid_return.flight_info.arrival_date = date(2026, 10, 8)
    cheap_return = _pinned_leg(100, inbound=True)
    cheap_return.flight_info = replace(valid_return.flight_info, **{field: invalid_value})
    scheduler._scrape_all_platforms.side_effect = [
        [_pinned_leg(500)], [cheap_return, valid_return],
    ]

    await scheduler.scrape_route(route)

    _assert_pinned_success(scheduler, route, [("qunar", Decimal("800"))])
    with scheduler._SessionLocal() as session:
        row = session.query(PriceHistory).filter_by(route_id=route.id).one()
        assert row.return_flight.departure_time == "18:00"
        assert row.return_flight.arrival_time == "20:00"
        assert row.return_flight.arrival_date == route.return_date + timedelta(days=1)


@pytest.mark.parametrize(
    ("reference_time", "expected_status"),
    [("18:00", "available"), ("16:00", "schedule_changed")],
)
async def test_pinned_schedule_status_uses_filtered_quote(
    pinned_scheduler, reference_time, expected_status,
):
    scheduler = pinned_scheduler
    route = _persist_pinned_route(
        scheduler, inbound_dep_time_ref=reference_time,
        ret_dep_time_from="17:00", ret_dep_time_to="19:00",
    )
    invalid = _pinned_leg(100, inbound=True)
    invalid.flight_info.departure_time = "16:00"
    scheduler._scrape_all_platforms.side_effect = [
        [_pinned_leg(500)], [invalid, _pinned_leg(300, inbound=True)],
    ]

    await scheduler.scrape_route(route)

    _assert_pinned_success(
        scheduler, route, [("qunar", Decimal("800"))], expected_status,
    )


@pytest.mark.parametrize(
    ("failure", "expected_status", "search_count"),
    [
        ("missing-number", "not_found", 1),
        ("missing-date", "not_found", 1),
        ("no-match", "not_found", 2),
        ("sold-out", "sold_out", 2),
        ("zero-price", "not_found", 2),
        ("negative-price", "not_found", 2),
        ("complete-roundtrip-only", "not_found", 2),
    ],
)
async def test_pinned_roundtrip_unusable_return_overrides_outbound_schedule_change(
    pinned_scheduler, failure, expected_status, search_count,
):
    scheduler = pinned_scheduler
    overrides = {"outbound_dep_time_ref": "08:30"}
    if failure == "missing-number":
        overrides["inbound_flight_no"] = None
    elif failure == "missing-date":
        overrides["return_date"] = None
    route = _persist_pinned_route(scheduler, **overrides)
    outbound = _pinned_leg(500)
    outbound.flight_info.departure_time = "11:00"  # >60-minute schedule change
    inbound = _pinned_leg(200, inbound=True)
    if failure == "no-match":
        inbound.flight_info.flight_no = "CA999"
    elif failure == "sold-out":
        inbound.available_seats = 0
    elif failure == "zero-price":
        inbound.price = Decimal("0")
    elif failure == "negative-price":
        inbound.price = Decimal("-10")
    elif failure == "complete-roundtrip-only":
        inbound.return_flight_info = outbound.flight_info
    scheduler._scrape_all_platforms.side_effect = [[outbound], [inbound]]

    await scheduler.scrape_route(route)

    assert scheduler._scrape_all_platforms.await_count == search_count
    _assert_no_pinned_quotes(scheduler, route, expected_status)


@pytest.mark.parametrize("complete_leg", ["outbound", "inbound"])
async def test_pinned_roundtrip_does_not_add_complete_products_as_single_legs(
    pinned_scheduler, complete_leg,
):
    scheduler = pinned_scheduler
    route = _persist_pinned_route(scheduler)
    outbound = _pinned_leg(500)
    inbound = _pinned_leg(300, inbound=True)
    complete = _pinned_leg(100, inbound=complete_leg == "inbound")
    complete.return_flight_info = (
        outbound.flight_info if complete_leg == "inbound" else inbound.flight_info
    )
    out_prices, in_prices = [outbound], [inbound]
    (out_prices if complete_leg == "outbound" else in_prices).insert(0, complete)
    scheduler._scrape_all_platforms.side_effect = [out_prices, in_prices]

    await scheduler.scrape_route(route)

    _assert_pinned_success(scheduler, route, [("qunar", Decimal("800"))])


async def test_pinned_oneway_persists_single_legs_and_calls_prediction(pinned_scheduler):
    scheduler = pinned_scheduler
    route = _persist_pinned_route(
        scheduler, trip_type="oneway", inbound_flight_no=None, return_date=None,
    )
    scheduler._scrape_all_platforms.return_value = [
        _pinned_leg(500),
        _pinned_leg(450, source="ctrip"),
        _make_flight_price("CA999", price=1),
        _pinned_leg(0),
        _pinned_leg(-10),
        _pinned_leg(100, available_seats=0),
    ]

    await scheduler.scrape_route(route)

    scheduler._scrape_all_platforms.assert_awaited_once()
    assert scheduler._scrape_all_platforms.await_args.args[0].return_date is None
    _assert_pinned_success(scheduler, route, [
        ("qunar", Decimal("500")), ("ctrip", Decimal("450")),
    ])
    with scheduler._SessionLocal() as session:
        rows = session.query(PriceHistory).filter_by(route_id=route.id).all()
        assert all(row.return_flight_id is None for row in rows)
        assert all(row.flight.flight_no == "CA953" for row in rows)
        assert all(row.flight.direction == FlightDirection.DEPARTURE.value for row in rows)
