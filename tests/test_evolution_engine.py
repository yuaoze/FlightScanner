"""Tests for the AI Evolution Engine (4-gear self-evolution system).

Uses in-memory SQLite for fast, isolated testing.
"""

import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

# Ensure src/ is on path (mirrors conftest.py)
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from flightscanner.models.database import (
    AIPredictionLog,
    Base,
    Flight,
    PriceHistory,
    Route,
    init_db,
)
from flightscanner.analyzers.evolution_engine import (
    CATCHABLE_LOW_MIN_BATCHES,
    CIRCUIT_BREAKER_CONSECUTIVE,
    FATAL_LOSS_THRESHOLD,
    HIGH_PAIN_THRESHOLD,
    MIN_EVALUATED_FOR_CREDIBILITY,
    SIGNIFICANCE_THRESHOLD,
    _detect_catchable_low,
    _evaluate_prediction,
    build_evolved_context,
    get_route_credibility,
    log_prediction,
    run_backtesting,
)
from flightscanner.interfaces import FlightDirection, FlightInfo, FlightPrice


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db_session():
    """Provide an in-memory SQLite session for each test."""
    _, SessionLocal = init_db("sqlite:///:memory:")
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def session_factory():
    """Provide a SessionLocal factory for async engine tests."""
    _, SessionLocal = init_db("sqlite:///:memory:")
    return SessionLocal


@pytest.fixture
def sample_route(db_session):
    """Create a sample route in the past (for backtesting)."""
    route = Route(
        origin="北京",
        destination="上海",
        target_date=date.today() - timedelta(days=5),
        target_price=Decimal("500.00"),
        scrape_interval=6,
        is_active=1,
    )
    db_session.add(route)
    db_session.commit()
    db_session.refresh(route)
    return route


@pytest.fixture
def sample_flight(db_session, sample_route):
    """Create a sample flight record."""
    flight = Flight(
        flight_no="CA1234",
        airline="中国国航",
        departure_city="北京",
        arrival_city="上海",
        departure_time="08:00",
        arrival_time="10:30",
        departure_date=sample_route.target_date,
        direction="departure",
    )
    db_session.add(flight)
    db_session.commit()
    db_session.refresh(flight)
    return flight


def _make_brief(action: str = "Wait", trend: str = "稳定", confidence: float = 0.6) -> dict:
    """Helper to create a mock brief dict."""
    return {
        "action": action,
        "reason": f"测试原因（{action}）",
        "trend": trend,
        "confidence": confidence,
        "recommendation": "立即购买" if action == "Buy" else "继续观望",
        "alert_level": "medium",
        "key_factors": ["测试因素"],
        "prediction_7d": "7日测试预测",
        "_source": "rule_based",
    }


def _make_price_record(
    db_session,
    route_id: int,
    flight_id: int,
    price: float,
    batch_id: str,
    scraped_at: datetime,
) -> PriceHistory:
    """Create and commit a PriceHistory record."""
    rec = PriceHistory(
        flight_id=flight_id,
        route_id=route_id,
        price=Decimal(str(price)),
        currency="CNY",
        seat_class="经济舱",
        source="qunar",
        scraped_at=scraped_at,
        batch_id=batch_id,
    )
    db_session.add(rec)
    db_session.commit()
    return rec


# ── TestG1LogPrediction ───────────────────────────────────────────────────────

class TestG1LogPrediction:
    """Tests for G1 — log_prediction()."""

    def test_log_creates_record_with_correct_fields(self, db_session, sample_route):
        """log_prediction() should persist a record with correct field values."""
        brief = _make_brief(action="Buy", trend="下跌", confidence=0.75)
        log = log_prediction(
            session=db_session,
            route_id=sample_route.id,
            brief=brief,
            current_price=800.0,
            days_until_flight=10,
        )

        assert log.id is not None
        assert log.route_id == sample_route.id
        assert log.recommended_action == "Buy"
        assert log.trend == "下跌"
        assert float(log.confidence) == pytest.approx(0.75, abs=0.01)
        assert float(log.price_at_prediction) == pytest.approx(800.0)
        assert log.days_until_flight == 10
        assert log.outcome_status == "pending"
        assert log.llm_source == "rule_based"
        assert "测试原因" in (log.reason or "")

    def test_log_with_wait_action(self, db_session, sample_route):
        """log_prediction() should correctly map Wait action."""
        brief = _make_brief(action="Wait", trend="上涨")
        log = log_prediction(
            session=db_session,
            route_id=sample_route.id,
            brief=brief,
            current_price=600.0,
            days_until_flight=20,
        )
        assert log.recommended_action == "Wait"

    def test_12h_cooldown_prevents_duplicate_log(self, db_session, sample_route):
        """The 12-hour cooldown is enforced based on predicted_at timestamp."""
        from flightscanner.core.services.route_service import RouteService

        # Log first prediction
        brief = _make_brief()
        log_prediction(
            session=db_session,
            route_id=sample_route.id,
            brief=brief,
            current_price=700.0,
            days_until_flight=15,
        )

        svc = RouteService(db_session)
        last_pred_time = svc.get_last_prediction_time(sample_route.id)
        assert last_pred_time is not None

        # Check that last_pred_time is within the last 12 hours
        last_pred_aware = last_pred_time
        if last_pred_time.tzinfo is None:
            last_pred_aware = last_pred_time.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last_pred_aware).total_seconds()
        assert elapsed < 43200, "Last prediction should be within the 12h window"

    def test_log_with_deepseek_source(self, db_session, sample_route):
        """log_prediction() should correctly record deepseek source."""
        brief = _make_brief()
        brief["_source"] = "deepseek"
        log = log_prediction(
            session=db_session,
            route_id=sample_route.id,
            brief=brief,
            current_price=500.0,
            days_until_flight=3,
        )
        assert log.llm_source == "deepseek"


@pytest.mark.parametrize("entrypoint", ["automatic", "refresh", "notify"])
@pytest.mark.parametrize("reverse_history", [False, True])
@pytest.mark.parametrize("tied_batches", [False, True])
async def test_prediction_entrypoints_use_current_batch_and_feedback(
    session_factory, monkeypatch, entrypoint, reverse_history, tied_batches,
):
    from unittest.mock import AsyncMock

    from flightscanner.analyzers import deepseek_analyzer
    from flightscanner.core.services import RouteService
    from flightscanner.scheduler.price_monitor import PriceMonitorScheduler

    now = datetime.now(timezone.utc)
    with session_factory() as session:
        route = RouteService(session).add_route(
            origin="北京", destination="上海",
            target_date=date.today() + timedelta(days=30), target_price=Decimal("500"),
        )
        flight = FlightInfo(
            flight_no="CA1234", airline="国航", departure_city=route.origin,
            arrival_city=route.destination, departure_time="08:00", arrival_time="10:00",
            departure_date=route.target_date, direction=FlightDirection.DEPARTURE,
        )
        for index, price in enumerate([800, 900, 1000, 1100, 1000, 1100, 1200, 1600]):
            batch = min(index, 6)
            record = FlightPrice(
                flight_info=flight, price=Decimal(price), currency="CNY", seat_class="经济舱",
                available_seats=5, source="qunar", batch_id=f"batch-{index if tied_batches else batch}",
                scraped_at=now - timedelta(hours=(6 - batch) * 6)
                + timedelta(seconds=0 if tied_batches else index),
            )
            RouteService(session).save_price_for_route(route.id, record)
        for index in range(3):
            session.add(AIPredictionLog(
                route_id=route.id, predicted_at=now - timedelta(days=10 + index),
                price_at_prediction=1000, days_until_flight=40 + index,
                recommended_action="Wait", outcome_status="loss", pain_index=350,
                actual_final_price=1350, llm_source="deepseek",
            ))
        session.commit()
        history = RouteService(session).get_route_price_history(route.id, days=30)
        if reverse_history:
            history.reverse()
        generate = AsyncMock(return_value=_make_brief())
        monkeypatch.setattr(deepseek_analyzer, "generate_brief_with_fallback_async", generate)
        scheduler = PriceMonitorScheduler.__new__(PriceMonitorScheduler)
        scheduler._SessionLocal = session_factory

        if entrypoint == "automatic":
            await scheduler._maybe_log_prediction(session, route, history)
            await scheduler._maybe_log_prediction(session, route, history)
        elif entrypoint == "refresh":
            await scheduler.refresh_prediction_for_route(route.id)
        else:
            await scheduler._get_ai_brief_for_notify(history, route)

        generate.assert_awaited_once()
        assert "胜率" in generate.call_args.kwargs["evolution_context"]
        assert "experience_context" in generate.call_args.kwargs
        logs = session.query(AIPredictionLog).filter_by(outcome_status="pending").all()
        if entrypoint == "notify":
            assert logs == []
        else:
            assert len(logs) == 1
            assert float(logs[0].price_at_prediction) == 1200
            assert logs[0].recommended_action == "Wait"


@pytest.mark.parametrize("entrypoint", ["automatic", "refresh"])
@pytest.mark.parametrize("record_count", [0, 10])
async def test_prediction_entrypoints_require_distinct_batches(
    session_factory, monkeypatch, entrypoint, record_count,
):
    from unittest.mock import AsyncMock

    from flightscanner.analyzers import deepseek_analyzer
    from flightscanner.core.services import RouteService
    from flightscanner.scheduler.price_monitor import PriceMonitorScheduler

    with session_factory() as session:
        route = RouteService(session).add_route(
            origin="北京", destination="上海", target_date=date.today() + timedelta(days=30),
            target_price=Decimal("500"),
        )
        flight = FlightInfo(
            flight_no="CA1234", airline="国航", departure_city=route.origin,
            arrival_city=route.destination, departure_time="08:00", arrival_time="10:00",
            departure_date=route.target_date, direction=FlightDirection.DEPARTURE,
        )
        for index in range(record_count):
            RouteService(session).save_price_for_route(route.id, FlightPrice(
                flight_info=flight, price=Decimal(1000 + index), currency="CNY",
                seat_class="经济舱", available_seats=5, source="qunar", batch_id="one-batch",
                scraped_at=datetime.now(timezone.utc),
            ))
        generate = AsyncMock()
        monkeypatch.setattr(deepseek_analyzer, "generate_brief_with_fallback_async", generate)
        scheduler = PriceMonitorScheduler.__new__(PriceMonitorScheduler)
        scheduler._SessionLocal = session_factory
        if entrypoint == "automatic":
            history = RouteService(session).get_route_price_history(route.id, days=30)
            await scheduler._maybe_log_prediction(session, route, history)
        else:
            await scheduler.refresh_prediction_for_route(route.id)
        generate.assert_not_awaited()
        assert session.query(AIPredictionLog).count() == 0


# ── TestG2Backtesting ─────────────────────────────────────────────────────────

class TestG2Backtesting:
    """Tests for G2 — _evaluate_prediction()."""

    def _setup_prediction(
        self,
        db_session,
        route,
        flight,
        action: str,
        base_price: float,
        predicted_at: datetime,
    ) -> AIPredictionLog:
        """Create a pending AIPredictionLog record."""
        log = AIPredictionLog(
            route_id=route.id,
            predicted_at=predicted_at,
            price_at_prediction=Decimal(str(base_price)),
            days_until_flight=10,
            recommended_action=action,
            reason="test",
            outcome_status="pending",
            llm_source="rule_based",
        )
        db_session.add(log)
        db_session.commit()
        db_session.refresh(log)
        return log

    def _add_price_records(self, db_session, route_id, flight_id, prices_with_batches):
        """Add multiple price records with batch IDs.

        Args:
            prices_with_batches: list of (price, batch_id, scraped_at) tuples.
        """
        for price, batch_id, scraped_at in prices_with_batches:
            _make_price_record(db_session, route_id, flight_id, price, batch_id, scraped_at)

    def test_buy_loss_when_price_dropped_after_buy_recommendation(
        self, db_session, sample_route, sample_flight
    ):
        """Buy recommendation followed by price drop → loss with positive pain_index."""
        # predicted_at must be BEFORE target_date (target_date = today - 5 days)
        predicted_at = datetime.now(timezone.utc) - timedelta(days=6)
        base_price = 1000.0
        log = self._setup_prediction(
            db_session, sample_route, sample_flight, "Buy", base_price, predicted_at
        )

        # Add price records after prediction but before departure
        t1 = predicted_at + timedelta(hours=6)
        t2 = predicted_at + timedelta(hours=12)
        self._add_price_records(db_session, sample_route.id, sample_flight.id, [
            (950.0, "batch1", t1),
            (850.0, "batch2", t2),  # 15% drop
        ])

        _evaluate_prediction(db_session, log)

        assert log.outcome_status == "loss"
        assert log.pain_index is not None
        assert float(log.pain_index) > 0
        assert log.actual_min_price is not None
        assert float(log.actual_min_price) == pytest.approx(850.0)

    def test_wait_win_when_price_rose_after_wait_recommendation(
        self, db_session, sample_route, sample_flight
    ):
        """Wait recommendation when price rose → loss (should have bought earlier)."""
        predicted_at = datetime.now(timezone.utc) - timedelta(days=6)
        base_price = 1000.0
        log = self._setup_prediction(
            db_session, sample_route, sample_flight, "Wait", base_price, predicted_at
        )

        t1 = predicted_at + timedelta(hours=6)
        t2 = predicted_at + timedelta(hours=12)
        # Price rises significantly → Wait was wrong → loss
        self._add_price_records(db_session, sample_route.id, sample_flight.id, [
            (1050.0, "batch1", t1),
            (1100.0, "batch2", t2),  # 10% rise
        ])

        _evaluate_prediction(db_session, log)

        assert log.outcome_status == "loss"
        assert float(log.pain_index) > 0

    def test_wait_recommendation_correct_when_price_dropped(
        self, db_session, sample_route, sample_flight
    ):
        """Wait recommendation when price dropped → win (correct: waited for lower price)."""
        predicted_at = datetime.now(timezone.utc) - timedelta(days=6)
        base_price = 1000.0
        log = self._setup_prediction(
            db_session, sample_route, sample_flight, "Wait", base_price, predicted_at
        )

        t1 = predicted_at + timedelta(hours=6)
        t2 = predicted_at + timedelta(hours=12)
        self._add_price_records(db_session, sample_route.id, sample_flight.id, [
            (950.0, "batch1", t1),
            (900.0, "batch2", t2),  # 10% drop, final = 900
        ])

        _evaluate_prediction(db_session, log)
        # action=Wait, final price rose negative (dropped): rise_pct < 0 → pain=0 → win
        assert log.outcome_status == "win"
        assert float(log.pain_index or 0) == 0.0

    def test_neutral_when_price_change_below_threshold(
        self, db_session, sample_route, sample_flight
    ):
        """Price change below 5% significance threshold → neutral outcome."""
        predicted_at = datetime.now(timezone.utc) - timedelta(days=6)
        base_price = 1000.0
        log = self._setup_prediction(
            db_session, sample_route, sample_flight, "Buy", base_price, predicted_at
        )

        t1 = predicted_at + timedelta(hours=6)
        t2 = predicted_at + timedelta(hours=12)
        self._add_price_records(db_session, sample_route.id, sample_flight.id, [
            (1010.0, "batch1", t1),
            (1020.0, "batch2", t2),  # 2% change — below threshold
        ])

        _evaluate_prediction(db_session, log)

        assert log.outcome_status == "neutral"
        assert float(log.pain_index or 0) == pytest.approx(0.0)

    @pytest.mark.parametrize("low_prices", [(800.0,), (850.0, 800.0)])
    def test_buy_loss_when_price_drops_then_returns_to_base(
        self, db_session, sample_route, sample_flight, low_prices
    ):
        """A rebound must not erase Buy opportunity loss, even for a single low batch."""
        predicted_at = datetime.now(timezone.utc) - timedelta(days=6)
        log = self._setup_prediction(
            db_session, sample_route, sample_flight, "Buy", 1000.0, predicted_at
        )
        self._add_price_records(db_session, sample_route.id, sample_flight.id, [
            (price, f"batch{i}", predicted_at + timedelta(hours=i + 1))
            for i, price in enumerate((*low_prices, 1000.0))
        ])

        _evaluate_prediction(db_session, log)

        assert log.outcome_status == "loss"
        assert float(log.actual_min_price) == pytest.approx(800.0)
        assert float(log.actual_final_price) == pytest.approx(1000.0)
        assert float(log.pain_index) == pytest.approx(140.0)
        assert log.catchable_low_exists == int(len(low_prices) >= 2)

    @pytest.mark.parametrize("legacy", [False, True], ids=["batch-id", "legacy-second"])
    @pytest.mark.parametrize("latest_prices", [(900.0, 1200.0), (1200.0, 900.0)])
    @pytest.mark.parametrize("stagger_timestamps", [False, True], ids=["same-time", "staggered"])
    def test_final_price_is_latest_batch_minimum(
        self, db_session, sample_route, sample_flight,
        legacy, latest_prices, stagger_timestamps,
    ):
        """Final price uses the latest batch minimum, not quote or insertion order."""
        predicted_at = (datetime.now(timezone.utc) - timedelta(days=6)).replace(microsecond=0)
        log = self._setup_prediction(
            db_session, sample_route, sample_flight, "Wait", 1000.0, predicted_at
        )
        latest_at = predicted_at + timedelta(hours=12)
        # Real batches may span seconds; legacy quotes share a second despite microseconds.
        offset = timedelta(microseconds=500000) if legacy else timedelta(seconds=2)
        if not stagger_timestamps:
            offset = timedelta(0)
        latest_batch = None if legacy else "a_latest"
        self._add_price_records(db_session, sample_route.id, sample_flight.id, [
            (latest_prices[0], latest_batch, latest_at),
            (latest_prices[1], latest_batch, latest_at + offset),
            # Insert the earlier, cheaper batch last to distinguish time from row order.
            (700.0, None if legacy else "z_earlier", predicted_at + timedelta(hours=6)),
        ])

        _evaluate_prediction(db_session, log)

        assert float(log.actual_min_price) == pytest.approx(700.0)
        assert float(log.actual_final_price) == pytest.approx(900.0)
        assert log.outcome_status == "win"
        assert float(log.pain_index) == pytest.approx(0.0)
        assert log.catchable_low_exists == 1

    @pytest.mark.parametrize("prices", [(900, 1200), (1200, 900)])
    def test_final_price_is_deterministic_for_simultaneous_batches(
        self, db_session, sample_route, sample_flight, prices,
    ):
        predicted_at = datetime.now(timezone.utc) - timedelta(days=6)
        log = self._setup_prediction(
            db_session, sample_route, sample_flight, "Wait", 1000, predicted_at,
        )
        self._add_price_records(db_session, sample_route.id, sample_flight.id, [
            (price, f"batch-{price}", predicted_at + timedelta(hours=6))
            for price in prices
        ])

        _evaluate_prediction(db_session, log)

        assert float(log.actual_final_price) == 900
        assert log.outcome_status == "win"
        assert float(log.pain_index) == 0

    @pytest.mark.parametrize("batch_id", ["single-batch", None], ids=["batch-id", "legacy-second"])
    def test_multiple_quotes_in_one_batch_are_insufficient(
        self, db_session, sample_route, sample_flight, batch_id
    ):
        """Multiple quotes, including legacy quotes within one second, count once."""
        predicted_at = (datetime.now(timezone.utc) - timedelta(days=6)).replace(microsecond=0)
        log = self._setup_prediction(
            db_session, sample_route, sample_flight, "Wait", 1000.0, predicted_at
        )
        scraped_at = predicted_at + timedelta(hours=6)
        self._add_price_records(db_session, sample_route.id, sample_flight.id, [
            (900.0, batch_id, scraped_at),
            (1200.0, batch_id, scraped_at + timedelta(microseconds=500000)),
        ])

        _evaluate_prediction(db_session, log)

        assert log.outcome_status == "skipped"
        assert log.actual_min_price is None
        assert log.actual_final_price is None

    def test_skipped_when_insufficient_data(self, db_session, sample_route, sample_flight):
        """Only 1 batch of price data → outcome=skipped (insufficient for backtesting)."""
        predicted_at = datetime.now(timezone.utc) - timedelta(days=6)
        log = self._setup_prediction(
            db_session, sample_route, sample_flight, "Buy", 1000.0, predicted_at
        )

        # Only 1 batch record → insufficient
        t1 = predicted_at + timedelta(hours=6)
        _make_price_record(
            db_session, sample_route.id, sample_flight.id, 900.0, "batch1", t1
        )

        _evaluate_prediction(db_session, log)
        assert log.outcome_status == "skipped"

    def test_skipped_when_no_post_prediction_data(self, db_session, sample_route, sample_flight):
        """No price records after prediction → outcome=skipped."""
        predicted_at = datetime.now(timezone.utc) - timedelta(days=6)
        log = self._setup_prediction(
            db_session, sample_route, sample_flight, "Wait", 800.0, predicted_at
        )

        # No price records added
        _evaluate_prediction(db_session, log)
        assert log.outcome_status == "skipped"

    @pytest.mark.parametrize("action", ["Buy", "Wait"])
    def test_7day_window_used_for_active_routes(self, db_session, action):
        """Only prices strictly after prediction and up to day 7 affect evaluation."""
        # Create a route with a FUTURE target_date (flight hasn't departed)
        future_route = Route(
            origin="北京",
            destination="成都",
            target_date=date.today() + timedelta(days=30),
            target_price=Decimal("400.00"),
            scrape_interval=6,
            is_active=1,
        )
        db_session.add(future_route)
        db_session.flush()

        future_flight = Flight(
            flight_no="SC4321",
            airline="四川航空",
            departure_city="北京",
            arrival_city="成都",
            departure_time="12:00",
            arrival_time="14:30",
            departure_date=future_route.target_date,
            direction="departure",
        )
        db_session.add(future_flight)
        db_session.flush()

        # Prediction made 8 days ago (qualifies for 7-day window)
        predicted_at = datetime.now(timezone.utc) - timedelta(days=8)
        log = AIPredictionLog(
            route_id=future_route.id,
            predicted_at=predicted_at,
            price_at_prediction=Decimal("800.00"),
            days_until_flight=38,
            recommended_action=action,
            outcome_status="pending",
            llm_source="rule_based",
        )
        db_session.add(log)
        db_session.flush()

        cutoff = predicted_at + timedelta(days=7)
        self._add_price_records(db_session, future_route.id, future_flight.id, [
            (100.0, "at_prediction", predicted_at),  # excluded start boundary
            (780.0, "b1", predicted_at + timedelta(days=1)),
            (760.0, "b2", cutoff),  # included end boundary
            # Even another quote in the final batch must not leak past the cutoff.
            (500.0, "b2", cutoff + timedelta(microseconds=1)),
            (1500.0, "b_outside", predicted_at + timedelta(days=9)),
        ])

        _evaluate_prediction(db_session, log)

        assert log.outcome_status == "neutral"
        assert float(log.actual_min_price) == pytest.approx(760.0)
        assert float(log.actual_final_price) == pytest.approx(760.0)
        assert float(log.pain_index) == pytest.approx(0.0)
        assert log.catchable_low_exists == 0


# ── TestDetectCatchableLow ────────────────────────────────────────────────────

class TestDetectCatchableLow:
    """Tests for _detect_catchable_low()."""

    def _make_history_records(self, route_id, flight_id, batches_with_prices):
        """Create mock PriceHistory objects (no DB needed).

        Args:
            batches_with_prices: list of (batch_id, price) tuples.

        Returns:
            List of PriceHistory objects (not committed to DB).
        """
        records = []
        for i, (batch_id, price) in enumerate(batches_with_prices):
            rec = PriceHistory(
                flight_id=flight_id,
                route_id=route_id,
                price=Decimal(str(price)),
                currency="CNY",
                seat_class="经济舱",
                source="qunar",
                scraped_at=datetime.now(timezone.utc) + timedelta(hours=i),
                batch_id=batch_id,
            )
            records.append(rec)
        return records

    def test_catchable_low_detected_with_2_batches(self):
        """Two consecutive batches with price below threshold → catchable_low=1."""
        base_price = 1000.0
        threshold = base_price * (1 - SIGNIFICANCE_THRESHOLD)  # 950

        # Two consecutive batches with price below threshold
        records = self._make_history_records(
            route_id=1,
            flight_id=1,
            batches_with_prices=[
                ("batch1", threshold - 10),  # 940 < 950 ✓
                ("batch2", threshold - 20),  # 930 < 950 ✓
            ],
        )

        result = _detect_catchable_low(records, base_price)
        assert result == 1

    def test_no_catchable_low_single_batch(self):
        """Only one batch with low price → not enough for catchable_low."""
        base_price = 1000.0
        threshold = base_price * (1 - SIGNIFICANCE_THRESHOLD)  # 950

        records = self._make_history_records(
            route_id=1,
            flight_id=1,
            batches_with_prices=[
                ("batch1", threshold - 10),  # 940 < 950 ✓ (only 1)
                ("batch2", threshold + 20),  # 970 > 950 ✗ (breaks streak)
            ],
        )

        result = _detect_catchable_low(records, base_price)
        assert result == 0

    def test_no_catchable_low_prices_above_threshold(self):
        """All prices above threshold → catchable_low=0."""
        base_price = 1000.0
        threshold = base_price * (1 - SIGNIFICANCE_THRESHOLD)  # 950

        records = self._make_history_records(
            route_id=1,
            flight_id=1,
            batches_with_prices=[
                ("batch1", threshold + 10),  # 960 > 950
                ("batch2", threshold + 20),  # 970 > 950
                ("batch3", threshold + 5),   # 955 > 950
            ],
        )

        result = _detect_catchable_low(records, base_price)
        assert result == 0

    def test_catchable_low_with_streak_interrupted_then_resumed(self):
        """Streak broken and resumed, but second streak meets threshold."""
        base_price = 1000.0
        threshold = base_price * (1 - SIGNIFICANCE_THRESHOLD)  # 950

        records = self._make_history_records(
            route_id=1,
            flight_id=1,
            batches_with_prices=[
                ("batch1", threshold - 10),  # 940 ✓
                ("batch2", threshold + 10),  # 960 ✗ (breaks streak)
                ("batch3", threshold - 5),   # 945 ✓
                ("batch4", threshold - 15),  # 935 ✓ (streak of 2 → detected)
            ],
        )

        result = _detect_catchable_low(records, base_price)
        assert result == 1


# ── TestG4Credibility ─────────────────────────────────────────────────────────

class TestG4Credibility:
    """Tests for G4 — get_route_credibility() and build_evolved_context()."""

    def _add_prediction_logs(self, db_session, route_id, outcomes_and_pains):
        """Add AIPredictionLog records with specified outcomes and pain indices.

        Args:
            outcomes_and_pains: list of (outcome_status, pain_index) tuples,
                                ordered from oldest to newest.
        """
        base_time = datetime.now(timezone.utc) - timedelta(days=len(outcomes_and_pains))
        for i, (outcome, pain) in enumerate(outcomes_and_pains):
            log = AIPredictionLog(
                route_id=route_id,
                predicted_at=base_time + timedelta(days=i),
                price_at_prediction=Decimal("1000.00"),
                days_until_flight=10,
                recommended_action="Buy",
                outcome_status=outcome,
                pain_index=Decimal(str(pain)) if pain is not None else None,
                llm_source="rule_based",
            )
            db_session.add(log)
        db_session.commit()

    def test_circuit_breaker_triggers_after_consecutive_fatal_losses(
        self, db_session, sample_route
    ):
        """Three consecutive fatal losses → circuit_broken=True."""
        fatal_pain = FATAL_LOSS_THRESHOLD + 50.0

        # Most recent 3 are fatal losses (listed oldest→newest, latest 3 trigger breaker)
        self._add_prediction_logs(
            db_session, sample_route.id,
            [
                ("win", 0.0),           # older win
                ("loss", fatal_pain),   # fatal loss 1 (most recent 3)
                ("loss", fatal_pain),   # fatal loss 2
                ("loss", fatal_pain),   # fatal loss 3 ← most recent
            ],
        )

        cred = get_route_credibility(db_session, sample_route.id)
        assert cred["circuit_broken"] is True
        assert cred["consecutive_fatal_losses"] >= CIRCUIT_BREAKER_CONSECUTIVE

    def test_no_circuit_breaker_when_wins_interspersed(
        self, db_session, sample_route
    ):
        """Fatal losses not consecutive → circuit not broken."""
        fatal_pain = FATAL_LOSS_THRESHOLD + 50.0

        self._add_prediction_logs(
            db_session, sample_route.id,
            [
                ("loss", fatal_pain),
                ("win", 0.0),        # breaks streak
                ("loss", fatal_pain),
                ("loss", fatal_pain),
            ],
        )

        cred = get_route_credibility(db_session, sample_route.id)
        # Latest 2 are fatal losses but not 3 consecutive
        assert cred["circuit_broken"] is False

    def test_green_credibility_when_win_rate_above_70pct(
        self, db_session, sample_route
    ):
        """Win rate ≥ 70% should be reflected correctly."""
        self._add_prediction_logs(
            db_session, sample_route.id,
            [
                ("win", 0.0),
                ("win", 0.0),
                ("win", 0.0),
                ("loss", 50.0),  # 1 loss, 3 wins → 75%
            ],
        )

        cred = get_route_credibility(db_session, sample_route.id)
        assert cred["evaluated_count"] == 4
        assert cred["win_rate"] >= 0.7

    def test_no_badge_when_insufficient_evaluations(
        self, db_session, sample_route
    ):
        """Fewer than MIN_EVALUATED_FOR_CREDIBILITY records → context is empty."""
        # Add only 2 records (less than MIN_EVALUATED_FOR_CREDIBILITY = 3)
        self._add_prediction_logs(
            db_session, sample_route.id,
            [
                ("win", 0.0),
                ("loss", 50.0),
            ],
        )

        # build_evolved_context returns empty string when < MIN_EVALUATED records
        ctx = build_evolved_context(db_session, sample_route.id)
        assert ctx == ""

    def test_build_evolved_context_with_sufficient_data(
        self, db_session, sample_route
    ):
        """With ≥ MIN_EVALUATED_FOR_CREDIBILITY records, context is non-empty."""
        self._add_prediction_logs(
            db_session, sample_route.id,
            [
                ("win", 0.0),
                ("loss", 250.0),  # high pain > HIGH_PAIN_THRESHOLD
                ("win", 0.0),
            ],
        )

        ctx = build_evolved_context(db_session, sample_route.id)
        assert ctx != ""
        assert "胜率" in ctx

    def test_no_evaluations_returns_zero_credibility(self, db_session, sample_route):
        """No evaluated records → all metrics are zero/False."""
        cred = get_route_credibility(db_session, sample_route.id)
        assert cred["evaluated_count"] == 0
        assert cred["win_rate"] == 0.0
        assert cred["circuit_broken"] is False


# ── TestRunBacktesting ────────────────────────────────────────────────────────

class TestRunBacktesting:
    """Integration test for run_backtesting() using async."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("new_age_days", [8, 1], ids=["all-mature", "new-pending"])
    async def test_7day_trigger_backtests_all_mature_predictions(
        self, session_factory, monkeypatch, new_age_days
    ):
        """Each mature prediction is evaluated once, regardless of newer pending logs."""
        with session_factory() as session:
            route = Route(
                origin="北京",
                destination="杭州",
                target_date=date.today() + timedelta(days=60),
                target_price=Decimal("500.00"),
                scrape_interval=6,
                is_active=1,
            )
            session.add(route)
            session.flush()

            flight = Flight(
                flight_no="HO1234",
                airline="吉祥航空",
                departure_city="北京",
                arrival_city="杭州",
                departure_time="09:00",
                arrival_time="11:00",
                departure_date=route.target_date,
                direction="departure",
            )
            session.add(flight)
            session.flush()

            now = datetime.now(timezone.utc)
            old_log = AIPredictionLog(
                route_id=route.id,
                predicted_at=now - timedelta(days=20),
                price_at_prediction=Decimal("900.00"),
                days_until_flight=80,
                recommended_action="Buy",
                outcome_status="pending",
                llm_source="rule_based",
            )
            new_log = AIPredictionLog(
                route_id=route.id,
                predicted_at=now - timedelta(days=new_age_days),
                price_at_prediction=Decimal("850.00"),
                days_until_flight=60 + new_age_days,
                recommended_action="Wait",
                outcome_status="pending",
                llm_source="rule_based",
            )
            session.add_all([old_log, new_log])
            session.flush()

            # Separate windows also ensure old prices cannot affect the newer prediction.
            for log, prices in [(old_log, [700.0, 800.0]), (new_log, [830.0, 820.0])]:
                for i, price in enumerate(prices):
                    _make_price_record(
                        session, route.id, flight.id, price, f"{log.id}_b{i}",
                        log.predicted_at + timedelta(hours=(i + 1) * 6),
                    )

            old_log_id = old_log.id
            new_log_id = new_log.id

        evaluate = Mock(wraps=_evaluate_prediction)
        monkeypatch.setattr(
            "flightscanner.analyzers.evolution_engine._evaluate_prediction", evaluate
        )
        expected_count = 2 if new_age_days >= 7 else 1
        assert await run_backtesting(session_factory) == expected_count
        assert evaluate.call_count == expected_count

        with session_factory() as verify:
            refreshed_old = verify.get(AIPredictionLog, old_log_id)
            refreshed_new = verify.get(AIPredictionLog, new_log_id)

            assert refreshed_old.outcome_status == "loss"
            assert float(refreshed_old.actual_min_price) == pytest.approx(700.0)
            assert float(refreshed_old.actual_final_price) == pytest.approx(800.0)
            assert float(refreshed_old.pain_index) == pytest.approx(140.0)
            if new_age_days >= 7:
                assert refreshed_new.outcome_status == "neutral"
                assert float(refreshed_new.actual_min_price) == pytest.approx(820.0)
                assert float(refreshed_new.actual_final_price) == pytest.approx(820.0)
                assert float(refreshed_new.pain_index) == pytest.approx(0.0)
            else:
                assert refreshed_new.outcome_status == "pending"
                assert refreshed_new.actual_min_price is None
                assert refreshed_new.actual_final_price is None
                assert refreshed_new.pain_index is None

        evaluate.reset_mock()
        assert await run_backtesting(session_factory) == 0
        evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_backtesting_processes_pending_records(self, session_factory):
        """run_backtesting() processes pending records with expired routes."""
        # Setup: create route with past target_date and pending prediction
        session = session_factory()
        try:
            route = Route(
                origin="北京",
                destination="广州",
                target_date=date.today() - timedelta(days=10),
                target_price=Decimal("600.00"),
                scrape_interval=6,
                is_active=1,
            )
            session.add(route)
            session.flush()

            flight = Flight(
                flight_no="CZ3456",
                airline="南方航空",
                departure_city="北京",
                arrival_city="广州",
                departure_time="10:00",
                arrival_time="14:00",
                departure_date=route.target_date,
                direction="departure",
            )
            session.add(flight)
            session.flush()

            predicted_at = datetime.now(timezone.utc) - timedelta(days=11)  # before departure
            log = AIPredictionLog(
                route_id=route.id,
                predicted_at=predicted_at,
                price_at_prediction=Decimal("800.00"),
                days_until_flight=1,
                recommended_action="Buy",
                outcome_status="pending",
                llm_source="rule_based",
            )
            session.add(log)

            # Add 2 batches of post-prediction price records
            for i, (price, batch_id) in enumerate([
                (750.0, "batch1"), (700.0, "batch2")
            ]):
                rec = PriceHistory(
                    flight_id=flight.id,
                    route_id=route.id,
                    price=Decimal(str(price)),
                    currency="CNY",
                    seat_class="经济舱",
                    source="qunar",
                    scraped_at=predicted_at + timedelta(hours=(i + 1) * 6),
                    batch_id=batch_id,
                )
                session.add(rec)

            session.commit()
            log_id = log.id
            route_id = route.id
        finally:
            session.close()

        # Run backtesting
        processed = await run_backtesting(session_factory)
        assert processed >= 1

        # Verify the record was updated
        verify_session = session_factory()
        try:
            updated_log = (
                verify_session.query(AIPredictionLog)
                .filter(AIPredictionLog.id == log_id)
                .first()
            )
            assert updated_log is not None
            assert updated_log.outcome_status != "pending"
            assert updated_log.actual_min_price is not None
        finally:
            verify_session.close()
