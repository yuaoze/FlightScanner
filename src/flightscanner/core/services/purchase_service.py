"""买入闭环业务逻辑：买入计划、买入记录、买点分析与经验沉淀。

设计要点：
- 买入 = 用户记录实际成交（系统不对接真实订票）
- 买入计划状态机：pending → triggered → converted / cancelled / expired
- 买后监控复用路线现有采集，分析时取 purchased_at → 起飞日价格窗口
- 买点分析仿进化引擎 G2/G3：规则统计 + LLM 复盘（降级可用）
- 经验沉淀仿 G4：从分析提炼经验条目，注入后续 AI 简报上下文
"""

import logging
import math
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from flightscanner.models.database import (
    BuyPlan,
    BuyPointAnalysis,
    ExperienceEntry,
    ExperienceEvidence,
    PriceHistory,
    PurchaseRecord,
    Route,
)

logger = logging.getLogger(__name__)

# 与进化引擎保持一致的显著性门槛：价格变动 ≤5% 视为不显著
SIGNIFICANCE_THRESHOLD = 0.05
# 评级使用相对票价的比例，避免固定金额在高/低票价上失真。
REGRET_GOOD_MAX_PCT = 0.10
REGRET_FAIR_MAX_PCT = 0.25
# 触发后未确认的宽限时长，超时置 expired
TRIGGER_GRACE_HOURS = 48
# 注入 AI 简报的经验条数上限
MAX_EXPERIENCE_CONTEXT = 5
DEFAULT_LIST_LIMIT = 200
MAX_LIST_LIMIT = 500

# 防止 NaN/Infinity 或误把订单号之类的数字写入金额字段。
MAX_UNIT_PRICE = 1_000_000.0
MAX_TOTAL_PAID = 100_000_000.0
MAX_PASSENGERS = 100
# 报价与用户录入的单价相差过大时不强行关联航班。
QUOTE_MATCH_MAX_PCT = 0.10
QUOTE_MATCH_MAX_ABS = 100.0


class PurchaseService:
    """买入闭环业务逻辑层。所有方法均使用传入的 session，提交由调用方控制。"""

    def __init__(self, session: Session):
        self.session = session

    # ── 价格查询辅助 ─────────────────────────────────────────────────────

    def _latest_quote_records(self, route_id: int) -> List[PriceHistory]:
        """取路线最新一次整体采集的报价。

        ``batch_id`` 可能是 UUID，不能用 max(batch_id) 判断新旧。先按
        ``scraped_at`` 找到最新记录，再使用其 batch_id 取出同一采集会话的
        全部平台报价。无 batch_id 的旧数据退化为最新时刻的记录。
        """
        newest = (
            self.session.query(PriceHistory)
            .filter(PriceHistory.route_id == route_id)
            .order_by(PriceHistory.scraped_at.desc(), PriceHistory.id.desc())
            .first()
        )
        if newest is None:
            return []

        query = self.session.query(PriceHistory).filter(PriceHistory.route_id == route_id)
        if newest.batch_id:
            query = query.filter(PriceHistory.batch_id == newest.batch_id)
        else:
            # 旧采集通常在同一秒写入多条报价。
            latest_second = newest.scraped_at.replace(microsecond=0)
            query = query.filter(
                PriceHistory.batch_id.is_(None),
                PriceHistory.scraped_at >= latest_second,
                PriceHistory.scraped_at < latest_second + timedelta(seconds=1),
            )
        return query.order_by(PriceHistory.price.asc(), PriceHistory.id.asc()).all()

    def _select_latest_quote(
        self,
        route_id: int,
        *,
        unit_price: Optional[float] = None,
        seat_class: Optional[str] = None,
        currency: str = "CNY",
        passengers: int = 1,
    ) -> Optional[PriceHistory]:
        """从最新批次选取最符合成交信息的可用报价。"""
        records = [
            record
            for record in self._latest_quote_records(route_id)
            if record.currency == currency
            and (not seat_class or record.seat_class == seat_class)
            and (record.available_seats is None or record.available_seats >= passengers)
        ]
        if not records:
            return None
        if unit_price is None:
            return min(records, key=lambda record: (float(record.price), record.id))
        best = min(
            records,
            key=lambda record: (abs(float(record.price) - unit_price), float(record.price), record.id),
        )
        tolerance = min(QUOTE_MATCH_MAX_ABS, unit_price * QUOTE_MATCH_MAX_PCT)
        return best if abs(float(best.price) - unit_price) <= tolerance else None

    def _select_quote_at_purchase(
        self,
        route_id: int,
        purchased_at: Optional[datetime],
        **criteria: Any,
    ) -> Optional[PriceHistory]:
        """历史补录仅尝试匹配成交时间附近的报价，避免绑定当前航班。"""
        if purchased_at is None:
            return self._select_latest_quote(route_id, **criteria)

        at = _as_utc(purchased_at)
        records = (
            self.session.query(PriceHistory)
            .filter(
                PriceHistory.route_id == route_id,
                PriceHistory.scraped_at <= at + timedelta(hours=6),
                PriceHistory.scraped_at >= at - timedelta(hours=24),
            )
            .order_by(PriceHistory.scraped_at.asc(), PriceHistory.id.asc())
            .all()
        )
        unit_price = criteria.get("unit_price")
        seat_class = criteria.get("seat_class")
        currency = criteria.get("currency", "CNY")
        passengers = criteria.get("passengers", 1)
        candidates = [
            record
            for record in records
            if record.currency == currency
            and (not seat_class or record.seat_class == seat_class)
            and (record.available_seats is None or record.available_seats >= passengers)
        ]
        if not candidates:
            return None
        # 仅在最靠近的采集批次内选择，不混合几小时前的陈旧低价。
        before = [record for record in candidates if _as_utc(record.scraped_at) <= at]
        if before:
            anchor = max(before, key=lambda record: (_as_utc(record.scraped_at), record.id))
        else:
            anchor = min(candidates, key=lambda record: (_as_utc(record.scraped_at), record.id))
        candidates = [
            record
            for record in candidates
            if (
                record.batch_id == anchor.batch_id
                if anchor.batch_id
                else record.scraped_at.replace(microsecond=0)
                == anchor.scraped_at.replace(microsecond=0)
            )
        ]
        if unit_price is None:
            return min(candidates, key=lambda record: (float(record.price), record.id))
        best = min(candidates, key=lambda record: abs(float(record.price) - unit_price))
        tolerance = min(QUOTE_MATCH_MAX_ABS, unit_price * QUOTE_MATCH_MAX_PCT)
        return best if abs(float(best.price) - unit_price) <= tolerance else None

    def get_latest_min_price(
        self,
        route_id: int,
        *,
        purchase: Optional[PurchaseRecord] = None,
    ) -> Tuple[Optional[float], Optional[int]]:
        """取路线最新整体批次的可售最低价及对应 flight_id。

        Returns:
            (最低价, flight_id)；无数据时返回 (None, None)。
        """
        records = self._latest_quote_records(route_id)
        if purchase is None:
            records = [
                record
                for record in records
                if record.available_seats is None or record.available_seats > 0
            ]
        else:
            records = [
                record
                for record in records
                if (
                    purchase.flight_id is None
                    or purchase.route_trip_type == "roundtrip"
                    or record.flight_id == purchase.flight_id
                )
                and (not purchase.currency or record.currency == purchase.currency)
                and (not purchase.seat_class or record.seat_class == purchase.seat_class)
                and (
                    record.available_seats is None
                    or record.available_seats >= max(1, int(purchase.passengers or 1))
                )
            ]
        if not records:
            return None, None
        best = min(records, key=lambda record: (float(record.price), record.id))
        return float(best.price), best.flight_id

    # ── 买入计划 ─────────────────────────────────────────────────────────

    def create_plan(
        self,
        route_id: int,
        plan_price: Optional[float] = None,
        plan_execute_by: Optional[datetime] = None,
    ) -> BuyPlan:
        """创建买入计划。plan_price 与 plan_execute_by 至少填一个。"""
        if plan_price is None and plan_execute_by is None:
            raise ValueError("plan_price 与 plan_execute_by 至少填写一个")
        if plan_price is not None:
            _validate_money("plan_price", plan_price, MAX_UNIT_PRICE)

        route = (
            self.session.query(Route)
            .filter(Route.id == route_id, Route.deleted_at.is_(None))
            .first()
        )
        if route is None:
            raise ValueError(f"路线不存在：route_id={route_id}")

        now = datetime.now(timezone.utc)
        target_end = _target_date_end(route.target_date)
        if target_end < now:
            raise ValueError("该路线已起飞，不能新建买入计划")
        if plan_execute_by is not None:
            execute_by = _as_utc(plan_execute_by)
            if execute_by <= now:
                raise ValueError("plan_execute_by 必须晚于当前时间")
            if execute_by > target_end:
                raise ValueError("plan_execute_by 不能晚于出发日")
            plan_execute_by = execute_by

        plan = BuyPlan(
            route_id=route_id,
            plan_price=Decimal(str(plan_price)) if plan_price is not None else None,
            plan_execute_by=plan_execute_by,
            status="pending",
        )
        self.session.add(plan)
        self.session.commit()
        self.session.refresh(plan)
        logger.info(
            "买入计划已创建 id=%d route=%s→%s price=%s by=%s",
            plan.id, route.origin, route.destination, plan_price, plan_execute_by,
        )
        return plan

    def cancel_plan(self, plan_id: int) -> BuyPlan:
        """取消 pending/triggered 状态的计划。"""
        plan = self._get_plan(plan_id)
        if plan.status not in ("pending", "triggered"):
            raise ValueError(f"仅 pending/triggered 状态可取消，当前：{plan.status}")
        plan.status = "cancelled"
        self.session.commit()
        return plan

    def list_plans(
        self,
        status: Optional[str] = None,
        route_id: Optional[int] = None,
        *,
        offset: int = 0,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> List[BuyPlan]:
        """查询买入计划列表，支持过滤和有界分页。"""
        offset = max(int(offset), 0)
        limit = min(max(int(limit), 1), MAX_LIST_LIMIT)
        q = self.session.query(BuyPlan).options(joinedload(BuyPlan.route))
        if status:
            q = q.filter(BuyPlan.status == status)
        if route_id:
            q = q.filter(BuyPlan.route_id == route_id)
        return q.order_by(BuyPlan.created_at.desc()).offset(offset).limit(limit).all()

    def _get_plan(self, plan_id: int) -> BuyPlan:
        plan = self.session.query(BuyPlan).filter(BuyPlan.id == plan_id).first()
        if plan is None:
            raise ValueError(f"买入计划不存在：id={plan_id}")
        return plan

    def confirm_plan(
        self,
        plan_id: int,
        actual_price: Optional[float] = None,
        seat_class: Optional[str] = None,
        passengers: int = 1,
        notes: Optional[str] = None,
        *,
        unit_price: Optional[float] = None,
        total_paid: Optional[float] = None,
        currency: str = "CNY",
        purchased_at: Optional[datetime] = None,
    ) -> PurchaseRecord:
        """确认已触发计划成交：计划置 converted 并创建买入记录。"""
        plan = self._get_plan(plan_id)
        if plan.status == "converted":
            existing = (
                self.session.query(PurchaseRecord)
                .filter(PurchaseRecord.plan_id == plan_id)
                .first()
            )
            if existing is not None:
                return existing
        if plan.status != "triggered":
            raise ValueError(f"仅 triggered 状态可确认成交，当前：{plan.status}")

        resolved_price = _resolve_unit_price(unit_price, actual_price)
        _validate_purchase_inputs(resolved_price, total_paid, passengers)

        quote = self._select_quote_at_purchase(
            plan.route_id,
            purchased_at,
            unit_price=resolved_price,
            seat_class=seat_class,
            currency=currency.upper(),
            passengers=passengers,
        )

        plan.status = "converted"
        try:
            purchase = self._create_purchase(
                route_id=plan.route_id,
                price=resolved_price,
                total_paid=total_paid,
                plan_id=plan.id,
                flight_id=quote.flight_id if quote is not None else None,
                quote=quote,
                purchase_type="planned",
                seat_class=seat_class or (quote.seat_class if quote is not None else None),
                passengers=passengers,
                currency=currency,
                purchased_at=purchased_at,
                notes=notes,
            )
        except IntegrityError:
            # 并发确认时由 plan_id 唯一索引决出胜者；输家回滚后
            # 返回已存在的同一账本记录，使重试幂等。
            self.session.rollback()
            existing = (
                self.session.query(PurchaseRecord)
                .filter(PurchaseRecord.plan_id == plan_id)
                .first()
            )
            if existing is None:
                raise
            purchase = existing
        logger.info("计划 id=%d 已确认成交 → 买入记录 id=%d", plan.id, purchase.id)
        return purchase

    def check_plans_for_route(
        self,
        route: Route,
        current_min_price: Optional[float],
        now: Optional[datetime] = None,
    ) -> List[BuyPlan]:
        """检查路线 pending 计划是否触发（价格达标或到达截止时间）。

        每次采集后由调度器调用；纯 DB 逻辑，通知由调用方发送。

        Args:
            route: 路线对象。
            current_min_price: 本次采集的跨平台最低价（无数据传 None）。
            now: 当前时间（默认 UTC now）。

        Returns:
            本次被触发的计划列表（已置 triggered 状态）。
        """
        now = now or datetime.now(timezone.utc)
        pending = (
            self.session.query(BuyPlan)
            .filter(BuyPlan.route_id == route.id, BuyPlan.status == "pending")
            .all()
        )

        triggered: List[BuyPlan] = []
        for plan in pending:
            reason: Optional[str] = None
            if (
                plan.plan_price is not None
                and current_min_price is not None
                and current_min_price <= float(plan.plan_price)
            ):
                reason = "price_hit"
            elif plan.plan_execute_by is not None and _as_utc(plan.plan_execute_by) <= now:
                reason = "deadline"

            if reason:
                plan.status = "triggered"
                plan.triggered_at = now
                plan.trigger_reason = reason
                plan.trigger_price = (
                    Decimal(str(current_min_price)) if current_min_price is not None else None
                )
                triggered.append(plan)
                logger.info(
                    "买入计划触发 id=%d route_id=%d reason=%s price=%s",
                    plan.id, route.id, reason, current_min_price,
                )

        if triggered:
            self.session.commit()
        return triggered

    def sweep_deadline_plans(self, now: Optional[datetime] = None) -> List[BuyPlan]:
        """全量扫描到期的纯时间计划（无价格条件未触发者），供每日任务调用。"""
        now = now or datetime.now(timezone.utc)
        local_today = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
        pending = (
            self.session.query(BuyPlan)
            .join(Route, BuyPlan.route_id == Route.id)
            .filter(
                BuyPlan.status == "pending",
                BuyPlan.plan_execute_by.isnot(None),
                Route.deleted_at.is_(None),
                Route.target_date >= local_today,
            )
            .all()
        )
        triggered: List[BuyPlan] = []
        for plan in pending:
            if _as_utc(plan.plan_execute_by) <= now:
                plan.status = "triggered"
                plan.triggered_at = now
                plan.trigger_reason = "deadline"
                price, _ = self.get_latest_min_price(plan.route_id)
                plan.trigger_price = Decimal(str(price)) if price is not None else None
                triggered.append(plan)
        if triggered:
            self.session.commit()
            logger.info("到期计划扫描：触发 %d 条", len(triggered))
        return triggered

    def expire_stale_plans(self, now: Optional[datetime] = None) -> int:
        """过期处理：triggered 超宽限未确认、或路线已起飞仍 pending 的计划。"""
        now = now or datetime.now(timezone.utc)
        grace_deadline = now - timedelta(hours=TRIGGER_GRACE_HOURS)
        today = now.astimezone(ZoneInfo("Asia/Shanghai")).date()

        stale_triggered = (
            self.session.query(BuyPlan)
            .filter(
                BuyPlan.status == "triggered",
                BuyPlan.notification_status == "sent",
                or_(
                    BuyPlan.last_notification_at <= grace_deadline,
                    (
                        BuyPlan.last_notification_at.is_(None)
                        & (BuyPlan.triggered_at <= grace_deadline)
                    ),
                ),
            )
            .all()
        )
        departed_active = (
            self.session.query(BuyPlan)
            .join(Route, BuyPlan.route_id == Route.id)
            .filter(
                BuyPlan.status.in_(["pending", "triggered"]),
                Route.target_date < today,
            )
            .all()
        )

        count = 0
        for plan in {plan.id: plan for plan in stale_triggered + departed_active}.values():
            plan.status = "expired"
            count += 1
        if count:
            self.session.commit()
            logger.info("过期计划处理：%d 条置 expired", count)
        return count

    # ── 买入记录 ─────────────────────────────────────────────────────────

    def instant_buy(
        self,
        route_id: int,
        price: Optional[float] = None,
        seat_class: Optional[str] = None,
        passengers: int = 1,
        notes: Optional[str] = None,
        *,
        unit_price: Optional[float] = None,
        total_paid: Optional[float] = None,
        currency: str = "CNY",
        purchased_at: Optional[datetime] = None,
    ) -> PurchaseRecord:
        """一键买入：unit_price/price 为空时取最新可售报价。"""
        route = (
            self.session.query(Route)
            .filter(Route.id == route_id, Route.deleted_at.is_(None))
            .first()
        )
        if route is None:
            raise ValueError(f"路线不存在：route_id={route_id}")

        resolved_price = _resolve_unit_price(unit_price, price, required=False)
        quote = self._select_quote_at_purchase(
            route_id,
            purchased_at,
            unit_price=resolved_price,
            seat_class=seat_class,
            currency=currency.upper(),
            passengers=passengers,
        )
        if resolved_price is None:
            if quote is None:
                raise ValueError("该路线暂无价格数据，无法一键买入")
            resolved_price = float(quote.price)

        _validate_purchase_inputs(resolved_price, total_paid, passengers)

        return self._create_purchase(
            route_id=route_id,
            price=resolved_price,
            total_paid=total_paid,
            plan_id=None,
            flight_id=quote.flight_id if quote is not None else None,
            quote=quote,
            purchase_type="instant",
            seat_class=seat_class or (quote.seat_class if quote is not None else None),
            passengers=passengers,
            currency=currency,
            purchased_at=purchased_at,
            notes=notes,
        )

    def _create_purchase(
        self,
        *,
        route_id: int,
        price: float,
        total_paid: Optional[float],
        plan_id: Optional[int],
        purchase_type: str,
        flight_id: Optional[int] = None,
        quote: Optional[PriceHistory] = None,
        seat_class: Optional[str] = None,
        passengers: int = 1,
        currency: str = "CNY",
        purchased_at: Optional[datetime] = None,
        notes: Optional[str] = None,
    ) -> PurchaseRecord:
        _validate_purchase_inputs(price, total_paid, passengers)
        now = datetime.now(timezone.utc)
        route = (
            self.session.query(Route)
            .filter(Route.id == route_id, Route.deleted_at.is_(None))
            .first()
        )
        if route is None:
            raise ValueError(f"路线不存在：route_id={route_id}")

        explicit_purchase_time = purchased_at is not None
        effective_purchased_at = _as_utc(purchased_at) if purchased_at is not None else now
        if effective_purchased_at > now:
            raise ValueError("purchased_at 不能晚于当前时间")
        if effective_purchased_at.date() > route.target_date:
            raise ValueError("purchased_at 不能晚于出发日")
        if route.target_date < now.date() and not explicit_purchase_time:
            raise ValueError("已起飞路线仅支持填写 purchased_at 的历史补录")

        currency = (currency or "CNY").strip().upper()
        if not currency or len(currency) > 10:
            raise ValueError("currency 格式不正确")
        effective_total = total_paid if total_paid is not None else price * passengers
        purchase = PurchaseRecord(
            route_id=route_id,
            plan_id=plan_id,
            flight_id=flight_id,
            purchase_price=Decimal(str(price)),
            total_paid=Decimal(str(effective_total)),
            currency=currency,
            seat_class=seat_class,
            passengers=passengers,
            quote_price=Decimal(str(quote.price)) if quote is not None else None,
            quote_source=quote.source if quote is not None else None,
            quote_batch_id=quote.batch_id if quote is not None else None,
            route_origin=route.origin,
            route_destination=route.destination,
            route_target_date=route.target_date,
            route_return_date=route.return_date,
            route_trip_type=route.trip_type,
            purchased_at=effective_purchased_at,
            purchase_type=purchase_type,
            notes=notes,
            status="holding",
        )
        self.session.add(purchase)
        self.session.commit()
        self.session.refresh(purchase)
        logger.info(
            "买入记录已创建 id=%d route_id=%d price=%.2f type=%s",
            purchase.id, route_id, price, purchase_type,
        )
        return purchase

    def list_purchases(
        self,
        status: Optional[str] = None,
        route_id: Optional[int] = None,
        *,
        offset: int = 0,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> List[PurchaseRecord]:
        """查询买入记录列表，预加载展示关系并限制单次读取量。"""
        offset = max(int(offset), 0)
        limit = min(max(int(limit), 1), MAX_LIST_LIMIT)
        q = self.session.query(PurchaseRecord).options(
            joinedload(PurchaseRecord.route),
            joinedload(PurchaseRecord.flight),
            joinedload(PurchaseRecord.analysis),
        )
        if status:
            q = q.filter(PurchaseRecord.status == status)
        if route_id:
            q = q.filter(PurchaseRecord.route_id == route_id)
        return (
            q.order_by(PurchaseRecord.purchased_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def get_purchase(self, purchase_id: int) -> PurchaseRecord:
        purchase = (
            self.session.query(PurchaseRecord)
            .filter(PurchaseRecord.id == purchase_id)
            .first()
        )
        if purchase is None:
            raise ValueError(f"买入记录不存在：id={purchase_id}")
        return purchase

    def get_price_series_since(
        self,
        route_id: int,
        since: datetime,
        *,
        purchase: Optional[PurchaseRecord] = None,
        until: Optional[datetime] = None,
    ) -> List[Tuple[datetime, float]]:
        """买后可比价格序列：每个独立采集批次仅保留最低可售价。

        传入 ``purchase`` 时，与统计使用完全相同的产品口径：已关联
        flight_id 则仅比较同航班，并限定币种、已知舱位与足够余票；
        未关联航班则退化为路线口径。
        """
        if purchase is not None and until is None:
            target_date = purchase.route_target_date or purchase.route.target_date
            departure_at = _purchase_departure_at(purchase, target_date)
            now = datetime.now(timezone.utc)
            until = min(now, departure_at or _target_date_end(target_date))

        query = self.session.query(PriceHistory).filter(
            PriceHistory.route_id == route_id,
            PriceHistory.scraped_at > _as_utc(since),
        )
        if until is not None:
            query = query.filter(PriceHistory.scraped_at <= _as_utc(until))
        if purchase is not None:
            if purchase.flight_id is not None and purchase.route_trip_type != "roundtrip":
                query = query.filter(PriceHistory.flight_id == purchase.flight_id)
            if purchase.currency:
                query = query.filter(PriceHistory.currency == purchase.currency)
            if purchase.seat_class:
                query = query.filter(PriceHistory.seat_class == purchase.seat_class)
            passengers = max(1, int(purchase.passengers or 1))
            query = query.filter(
                or_(
                    PriceHistory.available_seats.is_(None),
                    PriceHistory.available_seats >= passengers,
                )
            )

        records = query.order_by(
            PriceHistory.scraped_at.asc(), PriceHistory.price.asc(), PriceHistory.id.asc()
        ).all()

        # batch_id 为正式批次键；旧数据按秒聚合，避免同次采集的
        # 多航班/多平台行被错认为多个独立样本。
        batch_min: Dict[str, Tuple[datetime, float]] = {}
        for rec in records:
            bid = rec.batch_id or f"legacy:{rec.scraped_at.replace(microsecond=0).isoformat()}"
            price = float(rec.price)
            if bid not in batch_min or price < batch_min[bid][1]:
                batch_min[bid] = (rec.scraped_at, price)
        return sorted(batch_min.values(), key=lambda x: x[0])

    # ── 买点分析 ─────────────────────────────────────────────────────────

    def compute_window_stats(
        self, purchase: PurchaseRecord, route: Route
    ) -> Dict[str, Any]:
        """计算买后价格窗口统计（纯规则，不含 LLM）。

        Returns:
            包含 post_min/post_max/final/regret_cost/savings_vs_final/verdict/
            pre_departure/days_before_departure 的字典；样本不足时统计字段为 None。
        """
        now = datetime.now(timezone.utc)
        target_date = purchase.route_target_date or route.target_date
        departure_at = _purchase_departure_at(purchase, target_date)
        flight_departed = (
            now >= departure_at if departure_at is not None else target_date < now.date()
        )
        purchased_at = _as_utc(purchase.purchased_at)

        if flight_departed:
            cutoff_end = departure_at or _target_date_end(target_date)
        else:
            cutoff_end = now

        series = self.get_price_series_since(
            purchase.route_id,
            purchased_at,
            purchase=purchase,
            until=cutoff_end,
        )

        base_price = float(purchase.purchase_price)
        days_before = (target_date - purchased_at.date()).days
        sample_size = len(series)
        coverage_hours = (
            round((series[-1][0] - series[0][0]).total_seconds() / 3600, 2)
            if sample_size >= 2
            else None
        )
        comparison_scope = (
            "flight"
            if purchase.flight_id is not None and purchase.route_trip_type != "roundtrip"
            else "route"
        )

        if sample_size < 2:
            return {
                "post_min_price": None,
                "post_max_price": None,
                "final_price": None,
                "regret_cost": None,
                "regret_pct": None,
                "savings_vs_final": None,
                "verdict": None,
                "pre_departure": not flight_departed,
                "days_before_departure": days_before,
                "sample_size": sample_size,
                "coverage_hours": coverage_hours,
                "data_quality": "insufficient",
                "analysis_status": "insufficient",
                "comparison_scope": comparison_scope,
            }

        prices = [price for _, price in series]
        post_min = min(prices)
        post_max = max(prices)
        final_price = prices[-1]

        # 多付成本：买后最低价较买入价下跌超过显著性门槛才计入
        drop = base_price - post_min
        regret = drop if base_price > 0 and drop / base_price > SIGNIFICANCE_THRESHOLD else 0.0
        regret_pct = regret / base_price if base_price > 0 else 0.0
        savings = final_price - base_price

        if regret == 0.0 and savings > 0:
            verdict = "excellent"
        elif regret_pct <= REGRET_GOOD_MAX_PCT:
            verdict = "good"
        elif regret_pct <= REGRET_FAIR_MAX_PCT:
            verdict = "fair"
        else:
            verdict = "poor"

        data_quality = (
            "good"
            if sample_size >= 4 and coverage_hours is not None and coverage_hours >= 24
            else "limited"
        )
        # 当前账本未保存往返的 return_flight_id，只能做路线级近似比较。
        if purchase.route_trip_type == "roundtrip":
            data_quality = "limited"

        return {
            "post_min_price": post_min,
            "post_max_price": post_max,
            "final_price": final_price,
            "regret_cost": regret,
            "regret_pct": regret_pct,
            "savings_vs_final": savings,
            "verdict": verdict,
            "pre_departure": not flight_departed,
            "days_before_departure": days_before,
            "sample_size": sample_size,
            "coverage_hours": coverage_hours,
            "data_quality": data_quality,
            "analysis_status": "provisional" if not flight_departed else "final",
            "comparison_scope": comparison_scope,
        }

    async def generate_analysis(
        self,
        purchase_id: int,
        *,
        api_key: Optional[str] = None,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        auto: bool = False,
    ) -> BuyPointAnalysis:
        """生成/重新生成买点分析（规则统计 + LLM 复盘），并自动提炼经验。

        Args:
            purchase_id: 买入记录 ID。
            api_key: DeepSeek API key（为空降级规则模板）。
            base_url: API 基础 URL。
            model: 模型名称。
            auto: True 标记为自动生成（起飞后每日任务）。

        Returns:
            已写入的 BuyPointAnalysis 记录。
        """
        from flightscanner.analyzers.buy_point_analyzer import (
            generate_buy_point_analysis_async,
        )

        purchase = self.get_purchase(purchase_id)
        route = self.session.query(Route).filter(Route.id == purchase.route_id).first()
        if route is None:
            raise ValueError(f"买入记录关联路线不存在：route_id={purchase.route_id}")

        route_label = f"{route.origin} → {route.destination}"
        stats = self.compute_window_stats(purchase, route)

        ai_result = await generate_buy_point_analysis_async(
            route_label=route_label,
            purchase_price=float(purchase.purchase_price),
            purchased_at=purchase.purchased_at.strftime("%Y-%m-%d %H:%M"),
            days_before_departure=stats["days_before_departure"],
            target_date=route.target_date,
            post_min_price=stats["post_min_price"],
            post_max_price=stats["post_max_price"],
            final_price=stats["final_price"],
            regret_cost=stats["regret_cost"],
            savings_vs_final=stats["savings_vs_final"],
            verdict=stats["verdict"],
            sample_size=stats["sample_size"],
            coverage_hours=stats["coverage_hours"],
            data_quality=stats["data_quality"],
            analysis_status=stats["analysis_status"],
            comparison_scope=stats["comparison_scope"],
            api_key=api_key,
            base_url=base_url,
            model=model,
        )

        import json

        analysis = (
            self.session.query(BuyPointAnalysis)
            .filter(BuyPointAnalysis.purchase_id == purchase_id)
            .first()
        )
        if analysis is None:
            analysis = BuyPointAnalysis(purchase_id=purchase_id)
            self.session.add(analysis)

        analysis.post_min_price = _dec(stats["post_min_price"])
        analysis.post_max_price = _dec(stats["post_max_price"])
        analysis.final_price = _dec(stats["final_price"])
        analysis.regret_cost = _dec(stats["regret_cost"])
        analysis.savings_vs_final = _dec(stats["savings_vs_final"])
        analysis.verdict = stats["verdict"]
        analysis.ai_analysis = json.dumps(ai_result, ensure_ascii=False)
        analysis.llm_source = ai_result.get("_source", "rule_based")
        analysis.auto_generated = 1 if auto else 0
        analysis.pre_departure = 1 if stats["pre_departure"] else 0
        analysis.sample_size = stats["sample_size"]
        analysis.coverage_hours = _dec(stats["coverage_hours"])
        analysis.data_quality = stats["data_quality"]
        analysis.analysis_status = stats["analysis_status"]
        analysis.analyzed_at = datetime.now(timezone.utc)

        # 起飞后分析完成 → 买入记录状态流转
        if not stats["pre_departure"] and purchase.status == "holding":
            purchase.status = "completed"

        self.session.commit()
        self.session.refresh(analysis)
        logger.info(
            "买点分析已生成 purchase_id=%d verdict=%s source=%s",
            purchase_id, analysis.verdict, analysis.llm_source,
        )

        # 仅终评且数据足够时才沉淀经验。临时分析/数据不足不能
        # 反哺后续 AI，否则会把未经证实的单次观察当成航线规律。
        affected_experience_ids = self._remove_purchase_evidence(analysis.purchase_id)
        if (
            stats["analysis_status"] == "final"
            and stats["data_quality"] == "good"
            and stats["verdict"] is not None
        ):
            entries = self.distill_experience(
                analysis=analysis,
                lessons=ai_result.get("key_lessons") or [],
                route_label=route_label,
            )
            affected_experience_ids -= {entry.id for entry in entries}
        self._archive_empty_auto_experiences(affected_experience_ids)
        return analysis

    def auto_analyze_departed(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
    ) -> "list[int]":
        """返回需要自动终评的已起飞买入记录 ID。

        注：仅做筛选不做分析，分析由调用方逐条 await generate_analysis，
        避免在长 session 中嵌套协程调用。
        """
        now = datetime.now(timezone.utc)
        today = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
        rows = (
            self.session.query(PurchaseRecord)
            .join(Route, PurchaseRecord.route_id == Route.id)
            .outerjoin(
                BuyPointAnalysis,
                BuyPointAnalysis.purchase_id == PurchaseRecord.id,
            )
            .filter(
                Route.target_date <= today,
                or_(
                    PurchaseRecord.status == "holding",
                    BuyPointAnalysis.id.is_(None),
                    BuyPointAnalysis.analysis_status == "provisional",
                    BuyPointAnalysis.data_quality.is_(None),
                ),
            )
            .all()
        )
        pending_ids: List[int] = []
        for purchase in rows:
            target_date = purchase.route_target_date or purchase.route.target_date
            departure_at = _purchase_departure_at(purchase, target_date)
            departed = now >= departure_at if departure_at is not None else target_date < today
            if departed:
                pending_ids.append(purchase.id)
        return pending_ids

    # ── 经验库 ───────────────────────────────────────────────────────────

    def _remove_purchase_evidence(self, purchase_id: int) -> set[int]:
        """重分析时撤销该买入的旧自动证据，使新经验替换而非累加。"""
        evidence_rows = (
            self.session.query(ExperienceEvidence)
            .filter(ExperienceEvidence.purchase_id == purchase_id)
            .all()
        )
        affected_ids = {row.experience_id for row in evidence_rows}
        for row in evidence_rows:
            self.session.delete(row)
        self.session.flush()
        if not affected_ids:
            return set()
        entries = (
            self.session.query(ExperienceEntry)
            .filter(ExperienceEntry.id.in_(affected_ids))
            .all()
        )
        for entry in entries:
            entry.evidence_count = (
                self.session.query(ExperienceEvidence)
                .filter(ExperienceEvidence.experience_id == entry.id)
                .count()
            )
        self.session.flush()
        return affected_ids

    def _archive_empty_auto_experiences(self, entry_ids: set[int]) -> None:
        """将重分析后已无任何案例证据的旧自动经验归档。"""
        if not entry_ids:
            return
        entries = (
            self.session.query(ExperienceEntry)
            .filter(ExperienceEntry.id.in_(entry_ids))
            .all()
        )
        for entry in entries:
            if entry.analysis_id is not None and entry.evidence_count == 0:
                entry.status = "archived"
        self.session.commit()

    def distill_experience(
        self,
        analysis: BuyPointAnalysis,
        lessons: List[str],
        route_label: str,
    ) -> List[ExperienceEntry]:
        """从买点分析的经验列表沉淀经验条目。

        合并策略：同 route_pattern 且同 title 的 active 经验合并；
        ``ExperienceEvidence`` 对 (experience_id, purchase_id) 去重，同一笔
        买入反复分析不会刷高证据数。
        """
        pattern = route_label.replace(" ", "")
        entries: List[ExperienceEntry] = []
        for lesson in lessons[:2]:  # 每次分析最多沉淀 2 条
            lesson = (lesson or "").strip()
            if not lesson:
                continue
            title = lesson[:40]
            existing = (
                self.session.query(ExperienceEntry)
                .filter(
                    ExperienceEntry.route_pattern == pattern,
                    ExperienceEntry.title == title,
                    ExperienceEntry.status == "active",
                )
                .first()
            )
            if existing:
                existing.content = lesson
                existing.updated_at = datetime.now(timezone.utc)
            else:
                existing = ExperienceEntry(
                    analysis_id=analysis.id,
                    route_pattern=pattern,
                    category=_infer_category(lesson),
                    title=title,
                    content=lesson,
                    evidence_count=0,
                    status="active",
                )
                self.session.add(existing)
                self.session.flush()

            evidence = (
                self.session.query(ExperienceEvidence)
                .filter(
                    ExperienceEvidence.experience_id == existing.id,
                    ExperienceEvidence.purchase_id == analysis.purchase_id,
                )
                .first()
            )
            if evidence is None:
                self.session.add(
                    ExperienceEvidence(
                        experience_id=existing.id,
                        purchase_id=analysis.purchase_id,
                    )
                )
                self.session.flush()

            existing.evidence_count = (
                self.session.query(ExperienceEvidence)
                .filter(ExperienceEvidence.experience_id == existing.id)
                .count()
            )
            entries.append(existing)

        if entries:
            self.session.commit()
            logger.info("经验沉淀：新增/合并 %d 条（pattern=%s）", len(entries), pattern)
        return entries

    def list_experiences(
        self,
        status: Optional[str] = "active",
        route_pattern: Optional[str] = None,
        *,
        offset: int = 0,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> List[ExperienceEntry]:
        offset = max(int(offset), 0)
        limit = min(max(int(limit), 1), MAX_LIST_LIMIT)
        q = self.session.query(ExperienceEntry)
        if status:
            q = q.filter(ExperienceEntry.status == status)
        if route_pattern:
            q = q.filter(ExperienceEntry.route_pattern == route_pattern)
        return (
            q.order_by(
                ExperienceEntry.evidence_count.desc(), ExperienceEntry.updated_at.desc()
            )
            .offset(offset)
            .limit(limit)
            .all()
        )

    def create_experience(
        self,
        title: str,
        content: str,
        route_pattern: str = "通用",
        category: str = "general",
    ) -> ExperienceEntry:
        title = title.strip()
        content = content.strip()
        route_pattern = route_pattern.strip() or "通用"
        if not title or not content:
            raise ValueError("title 与 content 不能为空")
        if len(title) > 200 or len(content) > 6000 or len(route_pattern) > 120:
            raise ValueError("经验文本超过长度限制")
        if category not in {"timing", "route", "holiday", "general"}:
            raise ValueError("category 不合法")
        entry = ExperienceEntry(
            analysis_id=None,
            route_pattern=route_pattern,
            category=category,
            title=title,
            content=content,
            evidence_count=0,
            status="active",
        )
        self.session.add(entry)
        self.session.commit()
        self.session.refresh(entry)
        return entry

    def update_experience(self, entry_id: int, **fields: Any) -> ExperienceEntry:
        entry = (
            self.session.query(ExperienceEntry)
            .filter(ExperienceEntry.id == entry_id)
            .first()
        )
        if entry is None:
            raise ValueError(f"经验不存在：id={entry_id}")
        for key in ("title", "content", "category", "route_pattern", "status"):
            if key not in fields or fields[key] is None:
                continue
            value = fields[key]
            if isinstance(value, str):
                value = value.strip()
            if key in {"title", "content", "route_pattern"} and not value:
                raise ValueError(f"{key} 不能为空")
            if key == "title" and len(value) > 200:
                raise ValueError("title 超过长度限制")
            if key == "content" and len(value) > 6000:
                raise ValueError("content 超过长度限制")
            if key == "route_pattern" and len(value) > 120:
                raise ValueError("route_pattern 超过长度限制")
            if key == "category" and value not in {"timing", "route", "holiday", "general"}:
                raise ValueError("category 不合法")
            if key == "status" and value not in {"active", "archived"}:
                raise ValueError("status 不合法")
            setattr(entry, key, value)
        entry.updated_at = datetime.now(timezone.utc)
        self.session.commit()
        return entry

    def delete_experience(self, entry_id: int) -> None:
        entry = (
            self.session.query(ExperienceEntry)
            .filter(ExperienceEntry.id == entry_id)
            .first()
        )
        if entry is None:
            raise ValueError(f"经验不存在：id={entry_id}")
        self.session.delete(entry)
        self.session.commit()


# ── 模块级辅助函数 ──────────────────────────────────────────────────────────


def build_experience_context(session: Session, route: Route) -> str:
    """拼接该路线相关的 active 经验，作为 AI 简报上下文注入（仿 G4）。

    取「该路线 pattern + 通用」的经验，按证据数排序，最多 MAX_EXPERIENCE_CONTEXT 条。
    无经验时返回空串，不影响现有简报生成。
    """
    pattern = f"{route.origin}→{route.destination}"
    entries = (
        session.query(ExperienceEntry)
        .filter(
            ExperienceEntry.status == "active",
            ExperienceEntry.route_pattern.in_([pattern, "通用"]),
        )
        .order_by(ExperienceEntry.evidence_count.desc(), ExperienceEntry.updated_at.desc())
        .limit(MAX_EXPERIENCE_CONTEXT)
        .all()
    )
    if not entries:
        return ""

    lines = ["【历史买入经验】"]
    for e in entries:
        evidence_label = (
            f"独立案例证据 {e.evidence_count} 次"
            if e.evidence_count > 0
            else "手工经验，未经案例验证"
        )
        lines.append(f"  - {e.content}（{evidence_label}）")
    lines.append("请根据证据强度审慎参考，不要将手工经验视为已验证规律。")
    return "\n".join(lines)


def _infer_category(lesson: str) -> str:
    """根据经验文本推断分类。"""
    if any(k in lesson for k in ("提前", "天数", "窗口", "周")):
        return "timing"
    if any(k in lesson for k in ("节假日", "春节", "国庆", "假期")):
        return "holiday"
    if "→" in lesson or "航线" in lesson:
        return "route"
    return "general"


def _dec(value: Optional[float]) -> Optional[Decimal]:
    return Decimal(str(value)) if value is not None else None


def _as_utc(dt: datetime) -> datetime:
    """将（可能 naive 的）datetime 统一为 UTC aware。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _target_date_end(target_date: date) -> datetime:
    """无精确起飞时间时的保守截止点。"""
    local_end = datetime.combine(
        target_date,
        dtime(23, 59, 59, 999999),
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    return local_end.astimezone(timezone.utc)


def _purchase_departure_at(
    purchase: PurchaseRecord,
    fallback_date: date,
) -> Optional[datetime]:
    """在有关联航班时使用精确起飞时刻作为复盘截止点。

    现有 Flight 没有机场时区字段，因此按应用的 Asia/Shanghai
    默认时区解释航班时刻。无法解析时由调用方回退到出发日末。
    """
    flight = purchase.flight
    if flight is None or not flight.departure_time:
        return None
    try:
        hour_text, minute_text = str(flight.departure_time).strip().split(":", 1)
        departure_date = flight.departure_date or fallback_date
        local_dt = datetime.combine(
            departure_date,
            dtime(int(hour_text), int(minute_text[:2])),
            tzinfo=ZoneInfo("Asia/Shanghai"),
        )
        return local_dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        logger.warning(
            "无法解析航班起飞时间 purchase_id=%s value=%r",
            purchase.id,
            flight.departure_time,
        )
        return None


def _validate_money(name: str, value: float, maximum: float) -> None:
    if isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"{name} 必须为有限数值")
    if float(value) <= 0:
        raise ValueError(f"{name} 必须为正数")
    if float(value) > maximum:
        raise ValueError(f"{name} 超过允许上限 {maximum:.0f}")


def _validate_purchase_inputs(
    unit_price: float,
    total_paid: Optional[float],
    passengers: int,
) -> None:
    _validate_money("unit_price", unit_price, MAX_UNIT_PRICE)
    if total_paid is not None:
        _validate_money("total_paid", total_paid, MAX_TOTAL_PAID)
    if isinstance(passengers, bool) or passengers < 1 or passengers > MAX_PASSENGERS:
        raise ValueError(f"passengers 必须在 1~{MAX_PASSENGERS} 之间")


def _resolve_unit_price(
    preferred: Optional[float],
    legacy: Optional[float],
    *,
    required: bool = True,
) -> Optional[float]:
    if preferred is not None and legacy is not None and abs(preferred - legacy) > 0.01:
        raise ValueError("unit_price 与兼容价格字段不一致")
    value = preferred if preferred is not None else legacy
    if value is None and required:
        raise ValueError("unit_price 必须填写")
    return value
