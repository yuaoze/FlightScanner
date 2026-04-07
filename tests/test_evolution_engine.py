"""Tests for the AI Evolution Engine (4-gear self-evolution system).

Uses in-memory SQLite for fast, isolated testing.
"""

import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

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

    def test_7day_window_used_for_active_routes(self, db_session):
        """Prediction 7+ days old on a future route → uses 7-day window only."""
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
            recommended_action="Buy",
            outcome_status="pending",
            llm_source="rule_based",
        )
        db_session.add(log)
        db_session.flush()

        # Add 2 price records within the 7-day window (days 1-6 after prediction)
        for i, (price, batch_id) in enumerate([(780.0, "b1"), (760.0, "b2")]):
            rec = PriceHistory(
                flight_id=future_flight.id,
                route_id=future_route.id,
                price=Decimal(str(price)),
                currency="CNY",
                seat_class="经济舱",
                source="qunar",
                scraped_at=predicted_at + timedelta(days=i + 1),
                batch_id=batch_id,
            )
            db_session.add(rec)

        # Add a price record OUTSIDE the 7-day window (day 9) — should be ignored
        rec_outside = PriceHistory(
            flight_id=future_flight.id,
            route_id=future_route.id,
            price=Decimal("500.00"),  # extreme drop outside window
            currency="CNY",
            seat_class="经济舱",
            source="qunar",
            scraped_at=predicted_at + timedelta(days=9),
            batch_id="b_outside",
        )
        db_session.add(rec_outside)
        db_session.commit()

        _evaluate_prediction(db_session, log)

        # Should be evaluated (not skipped) and actual_min should NOT include 500
        assert log.outcome_status != "skipped"
        assert log.actual_min_price is not None
        assert float(log.actual_min_price) > 500.0  # 500 record should be excluded


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
    async def test_7day_trigger_only_backtests_latest_prediction(self, session_factory):
        """7-day window: only the latest pending prediction per route is backtested.

        If a route has two old predictions (both ≥7 days), the older one should
        remain 'pending' (not evaluated) — only the newest gets the 7-day backtest.
        """
        session = session_factory()
        try:
            route = Route(
                origin="北京",
                destination="杭州",
                target_date=date.today() + timedelta(days=60),  # future route
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

            # Older prediction: 20 days ago (≥7 days, but NOT the latest)
            old_log = AIPredictionLog(
                route_id=route.id,
                predicted_at=now - timedelta(days=20),
                price_at_prediction=Decimal("900.00"),
                days_until_flight=80,
                recommended_action="Buy",
                outcome_status="pending",
                llm_source="rule_based",
            )
            # Newer prediction: 8 days ago (≥7 days and IS the latest)
            new_log = AIPredictionLog(
                route_id=route.id,
                predicted_at=now - timedelta(days=8),
                price_at_prediction=Decimal("850.00"),
                days_until_flight=68,
                recommended_action="Wait",
                outcome_status="pending",
                llm_source="rule_based",
            )
            session.add(old_log)
            session.add(new_log)
            session.flush()

            # Add 2 price batches within the 7-day window of the NEWER prediction
            for i, (price, bid) in enumerate([(830.0, "b1"), (820.0, "b2")]):
                rec = PriceHistory(
                    flight_id=flight.id,
                    route_id=route.id,
                    price=Decimal(str(price)),
                    currency="CNY",
                    seat_class="经济舱",
                    source="qunar",
                    scraped_at=new_log.predicted_at + timedelta(hours=(i + 1) * 12),
                    batch_id=bid,
                )
                session.add(rec)

            session.commit()
            old_log_id = old_log.id
            new_log_id = new_log.id
        finally:
            session.close()

        await run_backtesting(session_factory)

        verify = session_factory()
        try:
            refreshed_old = verify.query(AIPredictionLog).filter(
                AIPredictionLog.id == old_log_id
            ).first()
            refreshed_new = verify.query(AIPredictionLog).filter(
                AIPredictionLog.id == new_log_id
            ).first()

            # Older prediction should still be pending (not selected for 7-day backtest)
            assert refreshed_old.outcome_status == "pending", (
                "Older prediction should remain pending — only the latest is selected"
            )
            # Newer (latest) prediction should have been evaluated
            assert refreshed_new.outcome_status != "pending", (
                "Latest prediction should have been backtested via the 7-day window"
            )
        finally:
            verify.close()

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
