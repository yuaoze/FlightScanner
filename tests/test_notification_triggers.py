"""Unit tests for v1.6.0 notification trigger logic.

Tests cover:
- _should_notify() with all 6 trigger conditions and priority ordering
- _is_cooldown_active() with tiered cooldown
- _compute_consecutive_declining_batches()
- _compute_recent_3d_low()
- _ai_should_suppress()
"""

import pytest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from flightscanner.interfaces import FlightDirection, FlightInfo, FlightPrice
from flightscanner.scheduler.price_monitor import PriceMonitorScheduler


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_route(
    target_price: float = 1000.0,
    target_date: date = None,
    last_notified_at: datetime = None,
    last_notified_price: float = None,
    notify_threshold_pct: float = None,
    recent_3d_low: float = None,
):
    """Create a mock Route object for testing."""
    route = MagicMock()
    route.id = 1
    route.origin = "上海"
    route.destination = "东京"
    route.target_price = Decimal(str(target_price))
    route.target_date = target_date or (date.today() + timedelta(days=30))
    route.last_notified_at = last_notified_at
    route.last_notified_price = (
        Decimal(str(last_notified_price)) if last_notified_price else None
    )
    route.notify_threshold_pct = (
        Decimal(str(notify_threshold_pct)) if notify_threshold_pct else None
    )
    route.recent_3d_low = (
        Decimal(str(recent_3d_low)) if recent_3d_low else None
    )
    return route


def _make_fp(
    price: float,
    scraped_at: datetime = None,
    batch_id: str = None,
) -> FlightPrice:
    """Create a FlightPrice for testing."""
    return FlightPrice(
        flight_info=FlightInfo(
            flight_no="CA123",
            airline="国航",
            departure_city="上海",
            arrival_city="东京",
            departure_time="08:00",
            arrival_time="12:00",
            departure_date=date.today() + timedelta(days=30),
            direction=FlightDirection.DEPARTURE,
        ),
        price=Decimal(str(price)),
        currency="CNY",
        seat_class="经济舱",
        available_seats=5,
        scraped_at=scraped_at or datetime.now(timezone.utc),
        source="qunar",
        batch_id=batch_id,
    )


def _stats(avg: float = 1000.0, min_p: float = 800.0, max_p: float = 1200.0, count: int = 10):
    """Create a stats dict."""
    return {"avg_30d": avg, "min_30d": min_p, "max_30d": max_p, "batch_count": float(count)}


# ── Tests for _should_notify ─────────────────────────────────────────────────


class TestShouldNotify:
    """Test _should_notify() with all trigger conditions."""

    def test_target_hit(self):
        route = _make_route(target_price=1000)
        ok, reason = PriceMonitorScheduler._should_notify(
            route, Decimal("999"), _stats(), 10
        )
        assert ok is True
        assert reason == "target_hit"

    def test_target_hit_exact(self):
        route = _make_route(target_price=1000)
        ok, reason = PriceMonitorScheduler._should_notify(
            route, Decimal("1000"), _stats(), 10
        )
        assert ok is True
        assert reason == "target_hit"

    def test_near_30d_low(self):
        route = _make_route(target_price=500)
        ok, reason = PriceMonitorScheduler._should_notify(
            route, Decimal("830"), _stats(avg=1000, min_p=800), 10
        )
        assert ok is True
        assert reason == "near_30d_low"

    def test_below_avg(self):
        route = _make_route(target_price=500)
        ok, reason = PriceMonitorScheduler._should_notify(
            route, Decimal("880"), _stats(avg=1000, min_p=800), 10
        )
        assert ok is True
        assert reason == "below_avg"

    def test_below_avg_insufficient_data(self):
        route = _make_route(target_price=500)
        ok, reason = PriceMonitorScheduler._should_notify(
            route, Decimal("880"), _stats(avg=1000, min_p=800), 5
        )
        assert ok is False

    def test_departure_approaching(self):
        route = _make_route(target_price=1000, target_date=date.today() + timedelta(days=5))
        ok, reason = PriceMonitorScheduler._should_notify(
            route, Decimal("1090"), _stats(), 10,
            days_until_departure=5,
        )
        assert ok is True
        assert reason == "departure_approaching"

    def test_departure_approaching_price_too_high(self):
        route = _make_route(target_price=1000, target_date=date.today() + timedelta(days=5))
        ok, reason = PriceMonitorScheduler._should_notify(
            route, Decimal("1200"), _stats(avg=1300, min_p=1100), 10,
            days_until_departure=5,
        )
        # Price is > target*1.10 (=1100), so departure_approaching won't trigger
        assert reason != "departure_approaching"

    def test_rebound_warning(self):
        route = _make_route(target_price=500)
        ok, reason = PriceMonitorScheduler._should_notify(
            route, Decimal("900"), _stats(avg=1000, min_p=800), 10,
            recent_3d_low=800.0,  # 900 > 800*1.10=880
        )
        assert ok is True
        assert reason == "rebound_warning"

    def test_rebound_not_enough(self):
        route = _make_route(target_price=500)
        ok, reason = PriceMonitorScheduler._should_notify(
            route, Decimal("860"), _stats(avg=1000, min_p=800), 10,
            recent_3d_low=800.0,  # 860 < 800*1.10=880, not enough rebound
        )
        assert reason != "rebound_warning"

    def test_trend_down(self):
        route = _make_route(target_price=500)
        ok, reason = PriceMonitorScheduler._should_notify(
            route, Decimal("950"), _stats(avg=1000, min_p=900), 10,
            consecutive_declining_batches=3,
        )
        assert ok is True
        assert reason == "trend_down"

    def test_no_trigger(self):
        route = _make_route(target_price=500)
        ok, reason = PriceMonitorScheduler._should_notify(
            route, Decimal("950"), _stats(avg=1000, min_p=800), 10,
        )
        assert ok is False
        assert reason == ""

    def test_priority_departure_over_target(self):
        """departure_approaching has higher priority than target_hit."""
        route = _make_route(target_price=1000, target_date=date.today() + timedelta(days=3))
        ok, reason = PriceMonitorScheduler._should_notify(
            route, Decimal("990"), _stats(), 10,
            days_until_departure=3,
        )
        assert ok is True
        assert reason == "departure_approaching"

    def test_priority_target_over_rebound(self):
        """target_hit has higher priority than rebound_warning."""
        route = _make_route(target_price=900)
        ok, reason = PriceMonitorScheduler._should_notify(
            route, Decimal("890"), _stats(avg=1000, min_p=800), 10,
            recent_3d_low=800.0,  # would also trigger rebound
        )
        assert ok is True
        assert reason == "target_hit"


# ── Tests for _is_cooldown_active ────────────────────────────────────────────


class TestCooldownActive:
    """Test tiered cooldown logic."""

    def _make_scheduler(self):
        with patch.object(PriceMonitorScheduler, "__init__", lambda self, **kw: None):
            s = PriceMonitorScheduler.__new__(PriceMonitorScheduler)
        return s

    def test_no_last_notified(self):
        s = self._make_scheduler()
        route = _make_route()
        route.last_notified_at = None
        assert s._is_cooldown_active(route, Decimal("900"), "target_hit") is False

    def test_target_hit_4h_cooldown(self):
        s = self._make_scheduler()
        route = _make_route(last_notified_at=datetime.now(timezone.utc) - timedelta(hours=3))
        assert s._is_cooldown_active(route, Decimal("900"), "target_hit") is True

    def test_target_hit_after_4h(self):
        s = self._make_scheduler()
        route = _make_route(last_notified_at=datetime.now(timezone.utc) - timedelta(hours=5))
        assert s._is_cooldown_active(route, Decimal("900"), "target_hit") is False

    def test_departure_approaching_2h_cooldown(self):
        s = self._make_scheduler()
        route = _make_route(last_notified_at=datetime.now(timezone.utc) - timedelta(hours=1))
        assert s._is_cooldown_active(route, Decimal("900"), "departure_approaching") is True

    def test_departure_approaching_after_2h(self):
        s = self._make_scheduler()
        route = _make_route(last_notified_at=datetime.now(timezone.utc) - timedelta(hours=3))
        assert s._is_cooldown_active(route, Decimal("900"), "departure_approaching") is False

    def test_below_avg_12h_cooldown(self):
        s = self._make_scheduler()
        route = _make_route(last_notified_at=datetime.now(timezone.utc) - timedelta(hours=10))
        assert s._is_cooldown_active(route, Decimal("900"), "below_avg") is True

    def test_5pct_drop_breaks_cooldown(self):
        s = self._make_scheduler()
        route = _make_route(
            last_notified_at=datetime.now(timezone.utc) - timedelta(hours=1),
            last_notified_price=1000.0,
        )
        # 5% drop = 950, price 940 is below that → breaks cooldown
        assert s._is_cooldown_active(route, Decimal("940"), "target_hit") is False


# ── Tests for _compute_consecutive_declining_batches ─────────────────────────


class TestConsecutiveDeclining:
    """Test batch decline detection."""

    def test_3_declining_batches(self):
        now = datetime.now(timezone.utc)
        history = [
            _make_fp(1000, scraped_at=now - timedelta(hours=12), batch_id="b1"),
            _make_fp(950, scraped_at=now - timedelta(hours=8), batch_id="b2"),
            _make_fp(900, scraped_at=now - timedelta(hours=4), batch_id="b3"),
            _make_fp(850, scraped_at=now, batch_id="b4"),
        ]
        result = PriceMonitorScheduler._compute_consecutive_declining_batches(history, lookback=5)
        assert result == 3

    def test_mixed_pattern(self):
        now = datetime.now(timezone.utc)
        history = [
            _make_fp(1000, scraped_at=now - timedelta(hours=16), batch_id="b1"),
            _make_fp(900, scraped_at=now - timedelta(hours=12), batch_id="b2"),
            _make_fp(950, scraped_at=now - timedelta(hours=8), batch_id="b3"),  # up
            _make_fp(920, scraped_at=now - timedelta(hours=4), batch_id="b4"),
            _make_fp(880, scraped_at=now, batch_id="b5"),
        ]
        result = PriceMonitorScheduler._compute_consecutive_declining_batches(history, lookback=5)
        assert result == 2  # only last 2 are declining

    def test_single_batch(self):
        history = [_make_fp(1000, batch_id="b1")]
        result = PriceMonitorScheduler._compute_consecutive_declining_batches(history, lookback=5)
        assert result == 0

    def test_all_equal(self):
        now = datetime.now(timezone.utc)
        history = [
            _make_fp(1000, scraped_at=now - timedelta(hours=8), batch_id="b1"),
            _make_fp(1000, scraped_at=now - timedelta(hours=4), batch_id="b2"),
            _make_fp(1000, scraped_at=now, batch_id="b3"),
        ]
        result = PriceMonitorScheduler._compute_consecutive_declining_batches(history, lookback=5)
        assert result == 0

    def test_empty_history(self):
        result = PriceMonitorScheduler._compute_consecutive_declining_batches([], lookback=5)
        assert result == 0


# ── Tests for _compute_recent_3d_low ─────────────────────────────────────────


class TestRecent3dLow:
    """Test recent low calculation."""

    def _make_scheduler(self):
        with patch.object(PriceMonitorScheduler, "__init__", lambda self, **kw: None):
            s = PriceMonitorScheduler.__new__(PriceMonitorScheduler)
        return s

    def test_basic(self):
        s = self._make_scheduler()
        now = datetime.now(timezone.utc)
        history = [
            _make_fp(1000, scraped_at=now - timedelta(days=5)),  # outside window
            _make_fp(900, scraped_at=now - timedelta(days=2)),
            _make_fp(850, scraped_at=now - timedelta(days=1)),
            _make_fp(870, scraped_at=now),
        ]
        result = s._compute_recent_3d_low(history)
        assert result == 850.0

    def test_empty(self):
        s = self._make_scheduler()
        assert s._compute_recent_3d_low([]) is None

    def test_all_outside_window(self):
        s = self._make_scheduler()
        now = datetime.now(timezone.utc)
        history = [
            _make_fp(800, scraped_at=now - timedelta(days=5)),
            _make_fp(900, scraped_at=now - timedelta(days=4)),
        ]
        result = s._compute_recent_3d_low(history)
        assert result is None


# ── Tests for _ai_should_suppress ────────────────────────────────────────────


class TestAiShouldSuppress:
    """Test AI-based notification suppression."""

    def test_no_brief(self):
        assert PriceMonitorScheduler._ai_should_suppress("below_avg", None) is False

    def test_target_hit_never_suppressed(self):
        brief = {"action": "Wait", "confidence": 0.9}
        assert PriceMonitorScheduler._ai_should_suppress("target_hit", brief) is False

    def test_departure_approaching_never_suppressed(self):
        brief = {"action": "Wait", "confidence": 0.9}
        assert PriceMonitorScheduler._ai_should_suppress("departure_approaching", brief) is False

    def test_rebound_warning_never_suppressed(self):
        brief = {"action": "Wait", "confidence": 0.9}
        assert PriceMonitorScheduler._ai_should_suppress("rebound_warning", brief) is False

    def test_below_avg_suppressed_when_wait_high_confidence(self):
        brief = {"action": "Wait", "confidence": 0.8}
        assert PriceMonitorScheduler._ai_should_suppress("below_avg", brief) is True

    def test_below_avg_not_suppressed_when_low_confidence(self):
        brief = {"action": "Wait", "confidence": 0.5}
        assert PriceMonitorScheduler._ai_should_suppress("below_avg", brief) is False

    def test_trend_down_suppressed_when_wait(self):
        brief = {"action": "Wait", "confidence": 0.85}
        assert PriceMonitorScheduler._ai_should_suppress("trend_down", brief) is True

    def test_buy_action_never_suppresses(self):
        brief = {"action": "Buy", "confidence": 0.9}
        assert PriceMonitorScheduler._ai_should_suppress("below_avg", brief) is False
