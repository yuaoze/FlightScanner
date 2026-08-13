"""Tests for the purchase tracking closed loop（v2.2.0 买入闭环）.

覆盖：
- 买入计划：创建校验 / 触发判定 / 状态机（confirm/cancel/expire/sweep）
- 一键买入：最新批次最低价取价与字段落库
- 买点分析：窗口统计（regret/savings/verdict 分级、5% 显著性门槛）、
  generate_analysis 降级路径、状态流转与经验沉淀
- 经验库：CRUD 与合并逻辑、build_experience_context 注入
"""

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from time import monotonic

import pytest

from flightscanner.core.services import PurchaseService, RouteService, build_experience_context
from flightscanner.models.database import (
    BuyPlan,
    BuyPointAnalysis,
    ExperienceEntry,
    Flight,
    PriceHistory,
    PurchaseRecord,
    Route,
    init_db,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db_session():
    """Provide an in-memory SQLite session for each test."""
    _, SessionLocal = init_db("sqlite:///:memory:")
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def svc(db_session):
    return PurchaseService(db_session)


@pytest.fixture
def future_route(db_session):
    """未起飞路线（10 天后）。"""
    route = Route(
        origin="北京",
        destination="上海",
        target_date=date.today() + timedelta(days=10),
        target_price=Decimal("500.00"),
        scrape_interval=6,
        is_active=1,
    )
    db_session.add(route)
    db_session.commit()
    db_session.refresh(route)
    return route


@pytest.fixture
def departed_route(db_session):
    """已起飞路线（5 天前）。"""
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
def sample_flight(db_session, future_route):
    flight = Flight(
        flight_no="CA1234",
        airline="中国国航",
        departure_city="北京",
        arrival_city="上海",
        departure_time="08:00",
        arrival_time="10:30",
        departure_date=future_route.target_date,
        direction="departure",
    )
    db_session.add(flight)
    db_session.commit()
    db_session.refresh(flight)
    return flight


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_price_record(
    db_session,
    route_id: int,
    flight_id: int,
    price: float,
    batch_id: str,
    scraped_at: datetime,
    source: str = "qunar",
) -> PriceHistory:
    rec = PriceHistory(
        flight_id=flight_id,
        route_id=route_id,
        price=Decimal(str(price)),
        currency="CNY",
        seat_class="经济舱",
        source=source,
        scraped_at=scraped_at,
        batch_id=batch_id,
    )
    db_session.add(rec)
    db_session.commit()
    return rec


def _make_purchase(
    db_session,
    route_id: int,
    price: float = 1000.0,
    purchased_at: datetime = None,
    status: str = "holding",
) -> PurchaseRecord:
    purchase = PurchaseRecord(
        route_id=route_id,
        purchase_price=Decimal(str(price)),
        purchased_at=purchased_at or (datetime.now(timezone.utc) - timedelta(days=2)),
        purchase_type="instant",
        status=status,
    )
    db_session.add(purchase)
    db_session.commit()
    db_session.refresh(purchase)
    return purchase


NOW = datetime.now(timezone.utc)


# ── 买入计划：创建与校验 ──────────────────────────────────────────────────────


class TestPlanCreation:
    def test_create_plan_with_price_only(self, svc, future_route):
        plan = svc.create_plan(route_id=future_route.id, plan_price=600.0)
        assert plan.status == "pending"
        assert float(plan.plan_price) == 600.0
        assert plan.plan_execute_by is None

    def test_create_plan_with_deadline_only(self, svc, future_route):
        deadline = NOW + timedelta(days=2)
        plan = svc.create_plan(route_id=future_route.id, plan_execute_by=deadline)
        assert plan.status == "pending"
        assert plan.plan_price is None
        assert plan.plan_execute_by is not None

    def test_create_plan_requires_at_least_one_condition(self, svc, future_route):
        with pytest.raises(ValueError, match="至少填写一个"):
            svc.create_plan(route_id=future_route.id)

    def test_create_plan_rejects_non_positive_price(self, svc, future_route):
        with pytest.raises(ValueError, match="正数"):
            svc.create_plan(route_id=future_route.id, plan_price=0)

    def test_create_plan_rejects_unknown_route(self, svc):
        with pytest.raises(ValueError, match="路线不存在"):
            svc.create_plan(route_id=99999, plan_price=600.0)


# ── 买入计划：触发判定 ────────────────────────────────────────────────────────


class TestPlanTrigger:
    def test_price_hit_triggers_plan(self, svc, future_route):
        plan = svc.create_plan(route_id=future_route.id, plan_price=600.0)
        triggered = svc.check_plans_for_route(future_route, 550.0)
        assert [p.id for p in triggered] == [plan.id]
        assert plan.status == "triggered"
        assert plan.trigger_reason == "price_hit"
        assert float(plan.trigger_price) == 550.0
        assert plan.triggered_at is not None

    def test_price_above_target_does_not_trigger(self, svc, future_route):
        plan = svc.create_plan(route_id=future_route.id, plan_price=500.0)
        triggered = svc.check_plans_for_route(future_route, 550.0)
        assert triggered == []
        assert plan.status == "pending"

    def test_deadline_triggers_plan(self, svc, future_route):
        plan = svc.create_plan(
            route_id=future_route.id,
            plan_execute_by=NOW + timedelta(hours=1),
        )
        # 创建接口拒绝过去时间；模拟一个创建后刚刚到期的计划。
        plan.plan_execute_by = NOW - timedelta(hours=1)
        svc.session.commit()
        triggered = svc.check_plans_for_route(future_route, None)
        assert [p.id for p in triggered] == [plan.id]
        assert plan.trigger_reason == "deadline"
        assert plan.trigger_price is None

    def test_deadline_not_reached_does_not_trigger(self, svc, future_route):
        plan = svc.create_plan(
            route_id=future_route.id,
            plan_execute_by=NOW + timedelta(hours=1),
        )
        triggered = svc.check_plans_for_route(future_route, 100.0)
        assert triggered == []
        assert plan.status == "pending"

    def test_price_hit_takes_priority_over_deadline(self, svc, future_route):
        plan = svc.create_plan(
            route_id=future_route.id,
            plan_price=600.0,
            plan_execute_by=NOW + timedelta(hours=1),
        )
        plan.plan_execute_by = NOW - timedelta(hours=1)
        svc.session.commit()
        triggered = svc.check_plans_for_route(future_route, 550.0)
        assert triggered[0].trigger_reason == "price_hit"

    def test_triggered_plan_not_retriggered(self, svc, future_route):
        svc.create_plan(route_id=future_route.id, plan_price=600.0)
        first = svc.check_plans_for_route(future_route, 550.0)
        second = svc.check_plans_for_route(future_route, 540.0)
        assert len(first) == 1
        assert second == []


# ── 买入计划：状态机 ──────────────────────────────────────────────────────────


class TestPlanStateMachine:
    def _triggered_plan(self, svc, route) -> BuyPlan:
        plan = svc.create_plan(route_id=route.id, plan_price=600.0)
        svc.check_plans_for_route(route, 550.0)
        return plan

    def test_confirm_plan_converts_and_creates_purchase(self, svc, future_route):
        plan = self._triggered_plan(svc, future_route)
        purchase = svc.confirm_plan(
            plan.id, actual_price=545.0, seat_class="经济舱", passengers=2, notes="测试成交"
        )
        assert plan.status == "converted"
        assert purchase.plan_id == plan.id
        assert purchase.purchase_type == "planned"
        assert float(purchase.purchase_price) == 545.0
        assert purchase.passengers == 2
        assert purchase.status == "holding"

    def test_confirm_plan_is_idempotent(self, svc, db_session, future_route):
        plan = self._triggered_plan(svc, future_route)
        first = svc.confirm_plan(plan.id, actual_price=545.0)
        second = svc.confirm_plan(plan.id, actual_price=999.0)

        assert second.id == first.id
        assert (
            db_session.query(PurchaseRecord)
            .filter(PurchaseRecord.plan_id == plan.id)
            .count()
            == 1
        )

    def test_confirm_rejects_non_triggered_plan(self, svc, future_route):
        plan = svc.create_plan(route_id=future_route.id, plan_price=600.0)
        with pytest.raises(ValueError, match="triggered"):
            svc.confirm_plan(plan.id, actual_price=545.0)

    def test_confirm_rejects_non_positive_price(self, svc, future_route):
        plan = self._triggered_plan(svc, future_route)
        with pytest.raises(ValueError, match="正数"):
            svc.confirm_plan(plan.id, actual_price=0)

    def test_cancel_pending_plan(self, svc, future_route):
        plan = svc.create_plan(route_id=future_route.id, plan_price=600.0)
        cancelled = svc.cancel_plan(plan.id)
        assert cancelled.status == "cancelled"

    def test_cancel_triggered_plan(self, svc, future_route):
        plan = self._triggered_plan(svc, future_route)
        cancelled = svc.cancel_plan(plan.id)
        assert cancelled.status == "cancelled"

    def test_cancel_converted_plan_rejected(self, svc, future_route):
        plan = self._triggered_plan(svc, future_route)
        svc.confirm_plan(plan.id, actual_price=545.0)
        with pytest.raises(ValueError, match="取消"):
            svc.cancel_plan(plan.id)

    def test_expire_stale_triggered_plan(self, svc, db_session, future_route):
        plan = svc.create_plan(route_id=future_route.id, plan_price=600.0)
        plan.status = "triggered"
        plan.triggered_at = NOW - timedelta(hours=49)
        plan.notification_status = "sent"
        plan.last_notification_at = NOW - timedelta(hours=49)
        db_session.commit()
        expired = svc.expire_stale_plans()
        assert expired == 1
        assert plan.status == "expired"

    def test_fresh_triggered_plan_not_expired(self, svc, db_session, future_route):
        plan = svc.create_plan(route_id=future_route.id, plan_price=600.0)
        plan.status = "triggered"
        plan.triggered_at = NOW - timedelta(hours=1)
        db_session.commit()
        expired = svc.expire_stale_plans()
        assert expired == 0
        assert plan.status == "triggered"

    def test_unnotified_triggered_plan_is_not_silently_expired(
        self, svc, db_session, future_route
    ):
        plan = svc.create_plan(route_id=future_route.id, plan_price=600.0)
        plan.status = "triggered"
        plan.triggered_at = NOW - timedelta(hours=49)
        plan.notification_status = "failed"
        db_session.commit()

        assert svc.expire_stale_plans() == 0
        assert plan.status == "triggered"

    def test_departed_route_pending_plan_expired(self, svc, departed_route):
        # 新建接口已禁止给起飞路线创建计划，直接构造一条升级前遗留数据。
        plan = BuyPlan(
            route_id=departed_route.id,
            plan_price=Decimal("600"),
            status="pending",
        )
        svc.session.add(plan)
        svc.session.commit()
        expired = svc.expire_stale_plans()
        assert expired == 1
        assert plan.status == "expired"

    def test_sweep_deadline_plans(self, svc, future_route):
        plan = svc.create_plan(
            route_id=future_route.id,
            plan_execute_by=NOW + timedelta(hours=1),
        )
        plan.plan_execute_by = NOW - timedelta(hours=2)
        svc.session.commit()
        triggered = svc.sweep_deadline_plans()
        assert [p.id for p in triggered] == [plan.id]
        assert plan.status == "triggered"
        assert plan.trigger_reason == "deadline"

    def test_sweep_ignores_price_only_plans(self, svc, future_route):
        svc.create_plan(route_id=future_route.id, plan_price=600.0)
        assert svc.sweep_deadline_plans() == []


# ── 一键买入 ──────────────────────────────────────────────────────────────────


class TestInstantBuy:
    def test_instant_buy_uses_latest_batch_min_price(
        self, svc, db_session, future_route, sample_flight
    ):
        now = datetime.now(timezone.utc)
        # qunar 旧批次 500 / 新批次 600；ctrip 新批次 550 → 跨平台最新批次最低 550
        _make_price_record(db_session, future_route.id, sample_flight.id, 500, "q1",
                           now - timedelta(hours=6), source="qunar")
        _make_price_record(db_session, future_route.id, sample_flight.id, 600, "q2",
                           now - timedelta(hours=1), source="qunar")
        ctrip_rec = _make_price_record(db_session, future_route.id, sample_flight.id, 550, "c1",
                                       now - timedelta(hours=1), source="ctrip")

        purchase = svc.instant_buy(route_id=future_route.id, notes="一键买入")
        assert float(purchase.purchase_price) == 550.0
        assert purchase.flight_id == ctrip_rec.flight_id
        assert purchase.purchase_type == "instant"
        assert purchase.status == "holding"
        assert purchase.currency == "CNY"

    def test_instant_buy_with_explicit_price(self, svc, future_route):
        purchase = svc.instant_buy(route_id=future_route.id, price=999.0, seat_class="商务舱")
        assert float(purchase.purchase_price) == 999.0
        assert purchase.flight_id is None
        assert purchase.seat_class == "商务舱"

    def test_instant_buy_without_price_data_rejected(self, svc, future_route):
        with pytest.raises(ValueError, match="暂无价格数据"):
            svc.instant_buy(route_id=future_route.id)

    def test_instant_buy_unknown_route_rejected(self, svc):
        with pytest.raises(ValueError, match="路线不存在"):
            svc.instant_buy(route_id=99999, price=100.0)

    def test_get_latest_min_price_empty(self, svc, future_route):
        price, flight_id = svc.get_latest_min_price(future_route.id)
        assert price is None
        assert flight_id is None

    def test_latest_price_uses_scrape_time_not_batch_id(
        self, svc, db_session, future_route, sample_flight
    ):
        now = datetime.now(timezone.utc)
        _make_price_record(
            db_session, future_route.id, sample_flight.id, 300, "z-old",
            now - timedelta(hours=2),
        )
        _make_price_record(
            db_session, future_route.id, sample_flight.id, 800, "a-new", now,
        )
        _make_price_record(
            db_session, future_route.id, sample_flight.id, 750, "a-new", now,
            source="ctrip",
        )

        price, flight_id = svc.get_latest_min_price(future_route.id)
        assert price == 750.0
        assert flight_id == sample_flight.id

    def test_explicit_buy_keeps_unit_total_and_quote_snapshot(
        self, svc, db_session, future_route, sample_flight
    ):
        _make_price_record(
            db_session,
            future_route.id,
            sample_flight.id,
            550,
            "latest",
            datetime.now(timezone.utc),
        )
        purchase = svc.instant_buy(
            route_id=future_route.id,
            unit_price=550,
            total_paid=1088,
            passengers=2,
            seat_class="经济舱",
        )

        assert float(purchase.purchase_price) == 550
        assert float(purchase.total_paid) == 1088
        assert purchase.flight_id == sample_flight.id
        assert purchase.quote_batch_id == "latest"
        assert purchase.route_origin == future_route.origin
        assert purchase.route_target_date == future_route.target_date


class TestRouteListPerformance:
    def test_latest_batch_query_scales_with_large_history(
        self, db_session, future_route, sample_flight
    ):
        """Dashboard listing must not compare every quote with every other quote."""
        now = datetime.now(timezone.utc)
        rows = [
            {
                "flight_id": sample_flight.id,
                "route_id": future_route.id,
                "price": Decimal(str(1000 + index % 100)),
                "currency": "CNY",
                "seat_class": "经济舱",
                "source": "qunar",
                "scraped_at": now - timedelta(seconds=5000 - index),
                "batch_id": f"history-{index}",
            }
            for index in range(4998)
        ]
        rows.extend(
            [
                {
                    "flight_id": sample_flight.id,
                    "route_id": future_route.id,
                    "price": Decimal("900"),
                    "currency": "CNY",
                    "seat_class": "经济舱",
                    "source": "qunar",
                    "scraped_at": now,
                    "batch_id": "latest",
                },
                {
                    "flight_id": sample_flight.id,
                    "route_id": future_route.id,
                    "price": Decimal("700"),
                    "currency": "CNY",
                    "seat_class": "经济舱",
                    "source": "ctrip",
                    "scraped_at": now,
                    "batch_id": "latest",
                },
            ]
        )
        db_session.bulk_insert_mappings(PriceHistory, rows)
        db_session.commit()

        started = monotonic()
        routes = RouteService(db_session).get_all_routes()
        elapsed = monotonic() - started

        assert len(routes) == 1
        assert float(routes[0].latest_price) == 700
        assert elapsed < 3.0, f"latest-batch query regressed to {elapsed:.2f}s"


# ── 买点分析：窗口统计 ────────────────────────────────────────────────────────


class TestWindowStats:
    def _setup_window(
        self, svc, db_session, route, flight, purchase_price, window_prices
    ) -> PurchaseRecord:
        """构造 purchased_at=10 天前，窗口内依次出现 window_prices 的场景。"""
        purchased_at = datetime.now(timezone.utc) - timedelta(days=10)
        purchase = _make_purchase(
            db_session, route.id, price=purchase_price, purchased_at=purchased_at
        )
        base_day = date.today() - timedelta(days=9)
        for i, price in enumerate(window_prices):
            _make_price_record(
                db_session, route.id, flight.id, price, f"b{i}",
                datetime(base_day.year, base_day.month, base_day.day, 10, 0, tzinfo=timezone.utc)
                + timedelta(days=i),
            )
        return purchase

    @pytest.fixture
    def departed_flight(self, db_session, departed_route):
        flight = Flight(
            flight_no="CA1234",
            airline="中国国航",
            departure_city="北京",
            arrival_city="上海",
            departure_time="08:00",
            arrival_time="10:30",
            departure_date=departed_route.target_date,
            direction="departure",
        )
        db_session.add(flight)
        db_session.commit()
        db_session.refresh(flight)
        return flight

    def test_verdict_excellent(self, svc, db_session, departed_route, departed_flight):
        # 买后最低 980（跌 2% < 5% 门槛 → regret=0），最终 1100 → savings=100
        purchase = self._setup_window(
            svc, db_session, departed_route, departed_flight, 1000, [980, 1020, 1100]
        )
        stats = svc.compute_window_stats(purchase, departed_route)
        assert stats["post_min_price"] == 980.0
        assert stats["post_max_price"] == 1100.0
        assert stats["final_price"] == 1100.0
        assert stats["regret_cost"] == 0.0
        assert stats["savings_vs_final"] == 100.0
        assert stats["verdict"] == "excellent"
        assert stats["pre_departure"] is False

    def test_verdict_good(self, svc, db_session, departed_route, departed_flight):
        # 买后最低 900（跌 10% → regret=100 ≤ 100）
        purchase = self._setup_window(
            svc, db_session, departed_route, departed_flight, 1000, [950, 900, 950]
        )
        stats = svc.compute_window_stats(purchase, departed_route)
        assert stats["regret_cost"] == 100.0
        assert stats["verdict"] == "good"

    def test_verdict_fair(self, svc, db_session, departed_route, departed_flight):
        # regret=200（≤300）
        purchase = self._setup_window(
            svc, db_session, departed_route, departed_flight, 1000, [800, 850, 900]
        )
        stats = svc.compute_window_stats(purchase, departed_route)
        assert stats["regret_cost"] == 200.0
        assert stats["verdict"] == "fair"

    def test_verdict_poor(self, svc, db_session, departed_route, departed_flight):
        # regret=400（>300）
        purchase = self._setup_window(
            svc, db_session, departed_route, departed_flight, 1000, [600, 700, 650]
        )
        stats = svc.compute_window_stats(purchase, departed_route)
        assert stats["regret_cost"] == 400.0
        assert stats["verdict"] == "poor"

    def test_significance_threshold_filters_small_drops(
        self, svc, db_session, departed_route, departed_flight
    ):
        # 买后最低 960（跌 4% < 5% → regret=0）；最终 990 → savings=-10 → good
        purchase = self._setup_window(
            svc, db_session, departed_route, departed_flight, 1000, [960, 990]
        )
        stats = svc.compute_window_stats(purchase, departed_route)
        assert stats["regret_cost"] == 0.0
        assert stats["savings_vs_final"] == -10.0
        assert stats["verdict"] == "good"

    def test_insufficient_samples(self, svc, db_session, future_route, sample_flight):
        purchase = _make_purchase(db_session, future_route.id)
        _make_price_record(
            db_session, future_route.id, sample_flight.id, 900, "b0",
            datetime.now(timezone.utc) - timedelta(days=1),
        )
        stats = svc.compute_window_stats(purchase, future_route)
        assert stats["post_min_price"] is None
        assert stats["final_price"] is None
        assert stats["regret_cost"] is None
        assert stats["verdict"] is None
        assert stats["analysis_status"] == "insufficient"
        assert stats["data_quality"] == "insufficient"
        assert stats["sample_size"] == 1

    def test_pre_departure_window_uses_now_as_cutoff(
        self, svc, db_session, future_route, sample_flight
    ):
        purchase = _make_purchase(db_session, future_route.id)
        _make_price_record(
            db_session, future_route.id, sample_flight.id, 950, "b0",
            datetime.now(timezone.utc) - timedelta(days=1),
        )
        _make_price_record(
            db_session, future_route.id, sample_flight.id, 920, "b1",
            datetime.now(timezone.utc) - timedelta(hours=1),
        )
        stats = svc.compute_window_stats(purchase, future_route)
        assert stats["pre_departure"] is True
        assert stats["sample_size"] == 2
        assert stats["final_price"] == 920.0

    def test_days_before_departure(self, svc, db_session, future_route, sample_flight):
        purchase = _make_purchase(
            db_session, future_route.id,
            purchased_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
        stats = svc.compute_window_stats(purchase, future_route)
        # target_date = today+10，purchased = today-2 → 12 天
        assert stats["days_before_departure"] == 12

    def test_same_batch_quotes_count_once_and_final_is_batch_min(
        self, svc, db_session, future_route, sample_flight
    ):
        purchased_at = datetime.now(timezone.utc) - timedelta(days=2)
        purchase = _make_purchase(
            db_session, future_route.id, purchased_at=purchased_at
        )
        first_at = datetime.now(timezone.utc) - timedelta(days=1)
        last_at = datetime.now(timezone.utc) - timedelta(hours=1)
        for price, source in ((900, "qunar"), (500, "ctrip")):
            _make_price_record(
                db_session, future_route.id, sample_flight.id, price, "batch-1",
                first_at, source=source,
            )
        for price, source in ((800, "qunar"), (700, "ctrip")):
            _make_price_record(
                db_session, future_route.id, sample_flight.id, price, "batch-2",
                last_at, source=source,
            )

        stats = svc.compute_window_stats(purchase, future_route)
        assert stats["sample_size"] == 2
        assert stats["post_min_price"] == 500.0
        assert stats["post_max_price"] == 700.0
        assert stats["final_price"] == 700.0

    def test_multiple_quotes_in_one_batch_are_still_insufficient(
        self, svc, db_session, future_route, sample_flight
    ):
        purchase = _make_purchase(
            db_session,
            future_route.id,
            purchased_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
        scraped_at = datetime.now(timezone.utc) - timedelta(hours=1)
        _make_price_record(
            db_session, future_route.id, sample_flight.id, 500, "only-batch", scraped_at
        )
        _make_price_record(
            db_session, future_route.id, sample_flight.id, 900, "only-batch", scraped_at,
            source="ctrip",
        )

        stats = svc.compute_window_stats(purchase, future_route)
        assert stats["sample_size"] == 1
        assert stats["analysis_status"] == "insufficient"
        assert stats["final_price"] is None


# ── 买点分析：generate_analysis（规则降级路径）────────────────────────────────


class TestGenerateAnalysis:
    @pytest.fixture
    def departed_setup(self, db_session, departed_route):
        flight = Flight(
            flight_no="CA1234",
            airline="中国国航",
            departure_city="北京",
            arrival_city="上海",
            departure_time="08:00",
            arrival_time="10:30",
            departure_date=departed_route.target_date,
            direction="departure",
        )
        db_session.add(flight)
        db_session.commit()
        db_session.refresh(flight)
        # purchased_at 必须早于窗口价格记录（窗口 = purchased_at → 起飞日）
        purchase = _make_purchase(
            db_session, departed_route.id, price=1000.0,
            purchased_at=datetime.now(timezone.utc) - timedelta(days=10),
        )
        base_day = date.today() - timedelta(days=9)
        for i, price in enumerate([980, 1020, 1050, 1100]):
            _make_price_record(
                db_session, departed_route.id, flight.id, price, f"b{i}",
                datetime(base_day.year, base_day.month, base_day.day, 10, 0, tzinfo=timezone.utc)
                + timedelta(days=i),
            )
        return purchase

    async def test_generate_analysis_rule_based(
        self, svc, db_session, departed_route, departed_setup
    ):
        purchase = departed_setup
        analysis = await svc.generate_analysis(purchase.id, api_key=None)

        assert analysis.verdict == "excellent"
        assert analysis.llm_source == "rule_based"
        assert analysis.auto_generated == 0
        assert analysis.pre_departure == 0
        assert float(analysis.post_min_price) == 980.0
        assert float(analysis.savings_vs_final) == 100.0

        payload = json.loads(analysis.ai_analysis)
        assert "verdict_comment" in payload
        assert payload["key_lessons"]

        # 起飞后分析完成 → 买入记录状态流转 completed
        db_session.refresh(purchase)
        assert purchase.status == "completed"

    async def test_generate_analysis_marks_auto(
        self, svc, db_session, departed_route, departed_setup
    ):
        analysis = await svc.generate_analysis(departed_setup.id, api_key=None, auto=True)
        assert analysis.auto_generated == 1

    async def test_regenerate_upserts_single_analysis(
        self, svc, db_session, departed_route, departed_setup
    ):
        await svc.generate_analysis(departed_setup.id, api_key=None)
        await svc.generate_analysis(departed_setup.id, api_key=None)
        count = (
            db_session.query(BuyPointAnalysis)
            .filter(BuyPointAnalysis.purchase_id == departed_setup.id)
            .count()
        )
        assert count == 1

    async def test_analysis_auto_distills_and_merges_experience(
        self, svc, db_session, departed_route, departed_setup
    ):
        """同一买入反复生成分析不能把一份证据刷成多份。"""
        await svc.generate_analysis(departed_setup.id, api_key=None)
        await svc.generate_analysis(departed_setup.id, api_key=None)

        entries = db_session.query(ExperienceEntry).all()
        assert len(entries) >= 1
        assert all(e.route_pattern == "北京→上海" for e in entries)
        # 规则模板输出确定，但独立购买案例仍只有一笔。
        assert all(e.evidence_count == 1 for e in entries)

    def test_auto_analyze_departed_filtering(
        self, svc, db_session, departed_route, future_route, departed_setup
    ):
        departed_purchase = departed_setup
        # 未起飞路线的 holding 记录不应入选
        future_purchase = _make_purchase(db_session, future_route.id)
        # 已起飞但已 completed 的不应入选
        _make_purchase(db_session, departed_route.id, status="completed")

        ids = svc.auto_analyze_departed()
        assert departed_purchase.id in ids
        assert future_purchase.id not in ids

        # 可靠终评且买入状态已闭合后不再入选；旧版 provisional/缺质量
        # 元数据的分析仍应入选，以便升级后自动修正。
        db_session.add(
            BuyPointAnalysis(
                purchase_id=departed_purchase.id,
                verdict="good",
                analysis_status="final",
                data_quality="good",
                sample_size=4,
            )
        )
        departed_purchase.status = "completed"
        db_session.commit()
        assert departed_purchase.id not in svc.auto_analyze_departed()

    def test_final_insufficient_analysis_is_not_requeued_daily(
        self, svc, db_session, departed_route
    ):
        purchase = _make_purchase(
            db_session, departed_route.id, status="completed"
        )
        db_session.add(
            BuyPointAnalysis(
                purchase_id=purchase.id,
                analysis_status="insufficient",
                data_quality="insufficient",
                sample_size=0,
            )
        )
        db_session.commit()

        assert purchase.id not in svc.auto_analyze_departed()

    async def test_reanalysis_replaces_old_experience_evidence(
        self, svc, db_session, departed_setup, monkeypatch
    ):
        lesson = {"value": "经验 A"}

        async def fake_analysis(**_kwargs):
            return {
                "verdict_comment": "测试",
                "timing_assessment": "测试",
                "key_lessons": [lesson["value"]],
                "_source": "rule_based",
            }

        monkeypatch.setattr(
            "flightscanner.analyzers.buy_point_analyzer.generate_buy_point_analysis_async",
            fake_analysis,
        )
        await svc.generate_analysis(departed_setup.id, api_key=None)
        lesson["value"] = "经验 B"
        await svc.generate_analysis(departed_setup.id, api_key=None)

        active = svc.list_experiences(status="active")
        archived = svc.list_experiences(status="archived")
        assert [entry.title for entry in active] == ["经验 B"]
        assert active[0].evidence_count == 1
        assert any(entry.title == "经验 A" for entry in archived)


# ── 买后价格序列 ──────────────────────────────────────────────────────────────


class TestPriceSeries:
    def test_series_takes_batch_min_and_sorts_by_time(
        self, svc, db_session, future_route, sample_flight
    ):
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=1)
        # 同批次两条记录取最低；不同批次按时间排序
        _make_price_record(db_session, future_route.id, sample_flight.id, 600, "b1",
                           now - timedelta(hours=20))
        _make_price_record(db_session, future_route.id, sample_flight.id, 550, "b1",
                           now - timedelta(hours=20), source="ctrip")
        _make_price_record(db_session, future_route.id, sample_flight.id, 580, "b2",
                           now - timedelta(hours=5))
        # 窗口外记录应被排除
        _make_price_record(db_session, future_route.id, sample_flight.id, 400, "b0",
                           now - timedelta(days=3))

        series = svc.get_price_series_since(future_route.id, since)
        assert [p for _, p in series] == [550.0, 580.0]
        assert series[0][0] < series[1][0]


# ── 经验库 ────────────────────────────────────────────────────────────────────


class TestExperience:
    def test_create_and_list(self, svc):
        entry = svc.create_experience(
            title="提前 30 天买最便宜",
            content="北京→上海航线提前 30 天价格通常最低",
            route_pattern="北京→上海",
            category="timing",
        )
        assert entry.evidence_count == 0
        assert entry.status == "active"

        entries = svc.list_experiences(status="active", route_pattern="北京→上海")
        assert [e.id for e in entries] == [entry.id]

    def test_create_rejects_blank_fields(self, svc):
        with pytest.raises(ValueError, match="不能为空"):
            svc.create_experience(title="  ", content="内容")

    def test_update_experience(self, svc):
        entry = svc.create_experience(title="旧标题", content="旧内容")
        updated = svc.update_experience(entry.id, title="新标题", status="archived")
        assert updated.title == "新标题"
        assert updated.status == "archived"
        assert updated.content == "旧内容"

    def test_update_missing_experience_rejected(self, svc):
        with pytest.raises(ValueError, match="不存在"):
            svc.update_experience(99999, title="x")

    def test_delete_experience(self, svc, db_session):
        entry = svc.create_experience(title="t", content="c")
        svc.delete_experience(entry.id)
        assert db_session.query(ExperienceEntry).count() == 0

    def test_delete_missing_experience_rejected(self, svc):
        with pytest.raises(ValueError, match="不存在"):
            svc.delete_experience(99999)

    def test_distill_merges_same_pattern_and_title(self, svc, db_session, future_route):
        purchase = _make_purchase(db_session, future_route.id)
        analysis = BuyPointAnalysis(purchase_id=purchase.id, verdict="good")
        db_session.add(analysis)
        db_session.commit()
        db_session.refresh(analysis)

        lesson = "北京→上海：提前 12 天买入有效，锁定低价后持续上涨"
        first = svc.distill_experience(analysis, [lesson], "北京 → 上海")
        second = svc.distill_experience(analysis, [lesson], "北京 → 上海")

        assert len(first) == 1
        assert len(second) == 1
        assert first[0].id == second[0].id
        assert second[0].evidence_count == 1
        # pattern 去空格
        assert second[0].route_pattern == "北京→上海"

    def test_distill_limits_two_lessons_per_run(self, svc, db_session, future_route):
        purchase = _make_purchase(db_session, future_route.id)
        analysis = BuyPointAnalysis(purchase_id=purchase.id, verdict="good")
        db_session.add(analysis)
        db_session.commit()
        db_session.refresh(analysis)

        entries = svc.distill_experience(
            analysis, ["经验一", "经验二", "经验三"], "北京 → 上海"
        )
        assert len(entries) == 2

    def test_build_experience_context(self, svc, future_route):
        assert build_experience_context(svc.session, future_route) == ""

        svc.create_experience(
            title="航线经验", content="北京→上海提前 30 天最便宜",
            route_pattern="北京→上海",
        )
        svc.create_experience(title="通用经验", content="节假日前两周涨价", route_pattern="通用")
        svc.create_experience(title="无关航线", content="广州→深圳经验", route_pattern="广州→深圳")

        ctx = build_experience_context(svc.session, future_route)
        assert "【历史买入经验】" in ctx
        assert "北京→上海提前 30 天最便宜" in ctx
        assert "节假日前两周涨价" in ctx
        assert "广州→深圳经验" not in ctx


class TestLedgerIntegrity:
    def test_route_delete_preserves_ledger_and_cancels_active_plan(
        self, db_session, svc, future_route
    ):
        plan = svc.create_plan(route_id=future_route.id, plan_price=600)
        purchase = svc.instant_buy(route_id=future_route.id, unit_price=550)

        assert RouteService(db_session).delete_route(future_route.id) is True
        db_session.refresh(plan)

        assert RouteService(db_session).get_route_by_id(future_route.id) is None
        assert db_session.query(PurchaseRecord).filter_by(id=purchase.id).one()
        assert plan.status == "cancelled"

    def test_sqlite_foreign_keys_are_enabled(self, db_session):
        from sqlalchemy import text

        assert db_session.execute(text("PRAGMA foreign_keys")).scalar() == 1
