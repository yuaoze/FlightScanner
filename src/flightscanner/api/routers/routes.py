"""Routes API endpoints for Dashboard data."""

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from flightscanner.analyzers.rule_based_analyzer import RuleBasedAnalyzer
from flightscanner.api.deps import get_db
from flightscanner.api.route_filter import filter_history_by_route
from flightscanner.api.schemas import (
    BatchInfo,
    FlightBriefInfo,
    FlightListItem,
    PredictionLogItem,
    PriceHistoryPoint,
    PriceHistoryResponse,
    RouteBatchesResponse,
    RouteDetailResponse,
    RouteFlightsResponse,
    RoutePredictionsResponse,
    RouteResponse,
    ScrapeTaskResponse,
    SparklinePoint,
    UpdateRouteRequest,
)
from flightscanner.api.status_resolver import (
    STATUS_PRIORITY,
    is_ai_prediction_stale,
    resolve_status,
)
from flightscanner.api.time_utils import fmt_cst, iso_utc
from flightscanner.core.route_filter import (
    arrival_day_offset,
    itinerary_matches_route,
)
from flightscanner.core.services.route_service import RouteService
from flightscanner.interfaces import FlightInfo, FlightPrice
from flightscanner.models.database import AIPredictionLog, Flight, PriceHistory, Route

router = APIRouter()
_analyzer = RuleBasedAnalyzer()


def _latest_comparable_records(price_history: List[FlightPrice]) -> List[FlightPrice]:
    """Return records from the newest scrape batch in an already-filtered history.

    ``price_history`` contains many flights per scrape.  Taking the minimum of
    the first N rows mixes the newest quote with older batches and can surface a
    stale bargain as the current price.  Keep the fallback for legacy rows that
    predate ``batch_id`` while still restricting it to the newest timestamp.
    """
    if not price_history:
        return []
    newest = max(price_history, key=lambda fp: fp.scraped_at)
    if newest.batch_id:
        return [fp for fp in price_history if fp.batch_id == newest.batch_id]
    return [fp for fp in price_history if fp.scraped_at == newest.scraped_at]


def _batch_min_values(price_history: List[FlightPrice]) -> List[float]:
    """Collapse raw quote rows to one comparable minimum per scrape batch."""
    batches: dict[str, float] = {}
    for fp in price_history:
        key = fp.batch_id or fp.scraped_at.isoformat()
        price = float(fp.price)
        if key not in batches or price < batches[key]:
            batches[key] = price
    return list(batches.values())


def _compute_sparkline(
    price_history: List[FlightPrice], days: int = 14
) -> List[SparklinePoint]:
    """Aggregate price history to daily minimums for sparkline (CST day boundary)."""
    daily_min: dict[str, float] = {}
    for fp in price_history:
        day_key = fmt_cst(fp.scraped_at, "%m-%d")
        if day_key is None:
            continue
        price = float(fp.price)
        if day_key not in daily_min or price < daily_min[day_key]:
            daily_min[day_key] = price
    points = [SparklinePoint(date=d, price=p) for d, p in daily_min.items()]
    points.sort(key=lambda pt: pt.date)
    return points[-days:]


def _compute_duration(
    dep_time: str,
    arr_time: str,
    departure_date: Optional[date] = None,
    arrival_date: Optional[date] = None,
) -> Optional[str]:
    """Compute duration using calendar dates when available, including D+2+."""
    try:
        dh, dm = map(int, dep_time.split(":"))
        ah, am = map(int, arr_time.split(":"))
        dep_mins = dh * 60 + dm
        arr_mins = ah * 60 + am
        if departure_date is not None and arrival_date is not None:
            arr_mins += (arrival_date - departure_date).days * 24 * 60
        elif arr_mins < dep_mins:
            arr_mins += 24 * 60
        diff = arr_mins - dep_mins
        if diff < 0:
            return None
        return f"{diff // 60}h{diff % 60:02d}m"
    except (ValueError, AttributeError, TypeError):
        return None


def _resolved_arrival_date(flight: Any) -> tuple[Optional[date], bool]:
    """Return a trustworthy or inferred arrival date and whether it was inferred."""
    if flight is None or getattr(flight, "flight_no", None) == "VIRTUAL_RETURN":
        return None, False
    offset = arrival_day_offset(flight)
    if offset is None:
        return None, False
    departure_date = getattr(flight, "departure_date", None)
    if departure_date is None:
        return None, False
    actual_arrival_date = flight.arrival_date
    estimated = (
        actual_arrival_date is None
        or actual_arrival_date < departure_date
    )
    if estimated:
        actual_arrival_date = departure_date + timedelta(days=offset)
    return actual_arrival_date, estimated


def _flight_brief_info(flight: Optional[FlightInfo]) -> Optional[FlightBriefInfo]:
    """Convert a real flight leg to the dashboard's date-aware brief."""
    if flight is None or flight.flight_no == "VIRTUAL_RETURN":
        return None
    offset = arrival_day_offset(flight)
    actual_arrival_date, estimated = _resolved_arrival_date(flight)
    return FlightBriefInfo(
        flight_no=flight.flight_no,
        airline=flight.airline,
        departure_date=flight.departure_date,
        arrival_date=actual_arrival_date,
        arrival_day_offset=offset,
        arrival_date_is_estimated=estimated,
        departure_time=flight.departure_time,
        arrival_time=flight.arrival_time,
        duration=_compute_duration(
            flight.departure_time,
            flight.arrival_time,
            flight.departure_date,
            actual_arrival_date,
        ),
        departure_airport_code=flight.departure_airport_code,
        arrival_airport_code=flight.arrival_airport_code,
    )


def _get_latest_itinerary_info(
    latest_records: List[FlightPrice],
) -> tuple[Optional[FlightBriefInfo], Optional[FlightBriefInfo]]:
    """Return both legs from the cheapest matching record in one latest batch."""
    if not latest_records:
        return None, None
    latest = min(latest_records, key=lambda fp: fp.price)
    return (
        _flight_brief_info(latest.flight_info),
        _flight_brief_info(latest.return_flight_info),
    )


@router.get("/routes", response_model=List[RouteResponse])
def get_routes(
    only_expired: bool = Query(False, description="Only return expired routes"),
    db: Session = Depends(get_db),
) -> List[RouteResponse]:
    """Get monitored routes with analysis data.

    By default returns only active (non-expired) routes for the dashboard.
    Use ?only_expired=true for the history page.
    """
    service = RouteService(db)
    all_routes = service.get_all_routes()
    today = date.today()

    if only_expired:
        filtered_routes = [r for r in all_routes if r.target_date < today]
    else:
        filtered_routes = [r for r in all_routes if r.target_date >= today]

    results: List[RouteResponse] = []
    for route in filtered_routes:
        days_until = (route.target_date - today).days

        raw_history = service.get_route_price_history(route.id, days=14)
        latest_raw_records = _latest_comparable_records(raw_history)
        latest_records = filter_history_by_route(route, latest_raw_records)
        if route.last_flight_status == "filtered_out":
            latest_records = []
        # Apply route time-window/airport filter so the dashboard reflects the
        # currently configured constraints (existing data is filtered, future
        # scrapes are also constrained by the same fields).
        price_history = filter_history_by_route(route, raw_history)
        trend = _analyzer.predict_trend(price_history, route.target_date)

        latest_price = (
            float(min(fp.price for fp in latest_records))
            if latest_records
            else None
        )

        price_vs_avg_pct: Optional[float] = None
        if price_history and latest_price is not None:
            prices = _batch_min_values(price_history)
            avg_price = sum(prices) / len(prices)
            if avg_price > 0:
                price_vs_avg_pct = round(
                    (latest_price - avg_price) / avg_price * 100, 1
                )

        status, ai_reason, ai_confidence = resolve_status(
            db,
            route.id,
            trend.direction,
            price_vs_avg_pct,
            latest_price=latest_price,
            target_price=float(route.target_price) if route.target_price else None,
        )
        if route.last_flight_status == "filtered_out":
            status = "建议观望"
            ai_reason = "最新采集批次暂无符合当前过滤条件的航班"
            ai_confidence = 0.0
        # 检测 AI 是否已过时；过时则异步触发重预测（带去重，不会重复打 API）
        _enqueue_repredict_if_stale(db, route.id, latest_price)
        prediction_text = ai_reason or trend.recommendation
        confidence = ai_confidence if ai_confidence is not None else trend.confidence

        sparkline = _compute_sparkline(price_history)
        flight_info, return_flight_info = _get_latest_itinerary_info(latest_records)
        latest_scraped_at = (
            max(fp.scraped_at for fp in latest_raw_records)
            if latest_raw_records
            else None
        )

        has_alert = (
            route.is_active
            and route.target_price is not None
            and float(route.target_price) > 0
        )

        results.append(
            RouteResponse(
                id=route.id,
                origin=route.origin,
                destination=route.destination,
                target_date=route.target_date,
                return_date=route.return_date,
                trip_type=route.trip_type,
                target_price=float(route.target_price),
                latest_price=latest_price,
                status=status,
                trend_direction=trend.direction,
                trend_confidence=confidence,
                trend_recommendation=trend.recommendation,
                price_vs_avg_pct=price_vs_avg_pct,
                prediction_text=prediction_text,
                sparkline=sparkline,
                flight_info=flight_info,
                return_flight_info=return_flight_info,
                days_until=days_until,
                has_alert=has_alert,
                is_active=route.is_active,
                monitoring_mode=route.monitoring_mode,
                outbound_flight_no=route.outbound_flight_no,
                inbound_flight_no=route.inbound_flight_no,
                seat_class=route.pinned_seat_class,
                last_flight_status=route.last_flight_status,
                latest_scraped_at=iso_utc(latest_scraped_at),
                scrape_interval=route.scrape_interval,
                max_arrival_day_offset=route.max_arrival_day_offset,
                ret_max_arrival_day_offset=route.ret_max_arrival_day_offset,
            )
        )

    results.sort(key=lambda r: STATUS_PRIORITY.get(r.status, 1))
    return results


# ── Create / Delete routes ─────────────────────────────────────────────────


class CreateRouteRequest(BaseModel):
    origin: str
    destination: str
    target_date: date
    target_price: float
    scrape_interval: int = 6
    return_date: Optional[date] = None
    trip_type: str = "oneway"
    dep_airport_code: Optional[str] = None
    arr_airport_code: Optional[str] = None
    dep_time_from: Optional[str] = None
    dep_time_to: Optional[str] = None
    arr_time_from: Optional[str] = None
    arr_time_to: Optional[str] = None
    ret_dep_time_from: Optional[str] = None
    ret_dep_time_to: Optional[str] = None
    ret_arr_time_from: Optional[str] = None
    ret_arr_time_to: Optional[str] = None
    max_arrival_day_offset: Optional[int] = Field(default=None, ge=0, le=2)
    ret_max_arrival_day_offset: Optional[int] = Field(default=None, ge=0, le=2)
    max_results: int = 20
    monitoring_mode: str = "route"
    outbound_flight_no: Optional[str] = None
    inbound_flight_no: Optional[str] = None
    pinned_seat_class: Optional[str] = None

    @field_validator(
        "dep_time_from", "dep_time_to", "arr_time_from", "arr_time_to",
        "ret_dep_time_from", "ret_dep_time_to", "ret_arr_time_from", "ret_arr_time_to",
    )
    @classmethod
    def validate_time(cls, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
            raise ValueError("Time must be HH:MM between 00:00 and 23:59")
        return value

    @model_validator(mode="after")
    def validate_time_windows(self) -> "CreateRouteRequest":
        for prefix in ("dep_time", "arr_time", "ret_dep_time", "ret_arr_time"):
            time_from = getattr(self, f"{prefix}_from")
            time_to = getattr(self, f"{prefix}_to")
            if time_from is not None and time_to is not None and time_from > time_to:
                raise ValueError(f"{prefix}_from must be <= {prefix}_to")
        return self


class CreateRouteResponse(BaseModel):
    id: int
    message: str


@router.post("/routes", response_model=CreateRouteResponse, status_code=201)
def create_route(
    body: CreateRouteRequest, db: Session = Depends(get_db)
) -> CreateRouteResponse:
    """Create a new monitored route."""
    service = RouteService(db)
    try:
        route = service.add_route(
            origin=body.origin,
            destination=body.destination,
            target_date=body.target_date,
            target_price=Decimal(str(body.target_price)),
            scrape_interval=body.scrape_interval,
            return_date=body.return_date,
            trip_type=body.trip_type,
            dep_airport_code=body.dep_airport_code,
            arr_airport_code=body.arr_airport_code,
            dep_time_from=body.dep_time_from,
            dep_time_to=body.dep_time_to,
            arr_time_from=body.arr_time_from,
            arr_time_to=body.arr_time_to,
            ret_dep_time_from=body.ret_dep_time_from,
            ret_dep_time_to=body.ret_dep_time_to,
            ret_arr_time_from=body.ret_arr_time_from,
            ret_arr_time_to=body.ret_arr_time_to,
            max_arrival_day_offset=body.max_arrival_day_offset,
            ret_max_arrival_day_offset=body.ret_max_arrival_day_offset,
            max_results=body.max_results,
            monitoring_mode=body.monitoring_mode,
            outbound_flight_no=body.outbound_flight_no,
            inbound_flight_no=body.inbound_flight_no,
            pinned_seat_class=body.pinned_seat_class,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # 通知后台调度器：注册定时任务 + 立即采集一次。
    # 不做这一步的话，新路线只能等 uvicorn 重启时 reschedule_all_routes() 才会被调度。
    _register_route_with_scheduler(route)

    return CreateRouteResponse(id=route.id, message="监控创建成功")


def _get_live_monitor():
    """Return the running PriceMonitorScheduler, or None if scheduler is disabled."""
    try:
        from flightscanner.api import main as api_main

        return api_main._monitor
    except Exception:
        return None


def _register_route_with_scheduler(route) -> None:
    """让调度器接管该路线（注册 cron job + 立即采集一次）。"""
    monitor = _get_live_monitor()
    if monitor is None:
        return  # 调度器未启用（如测试模式 FLIGHTSCANNER_DISABLE_SCHEDULER=1）
    try:
        monitor.register_new_route(route)
    except Exception:
        # register 失败不影响 DB 写入，路线仍然存在；下次重启时 reschedule_all_routes 会兜底
        import logging
        logging.getLogger(__name__).exception("调度器注册路线 %s 失败", route.id)


def _enqueue_repredict_if_stale(db, route_id: int, latest_price) -> None:
    """如果该路线的 AI 预测已过时，异步触发重预测。

    在 GET 端点（list / detail）里调用 — 检测到过时就把重预测协程
    扔进调度器的事件循环，本次请求不阻塞。重预测自带进程级去重，
    dashboard 频繁轮询不会重复打 DeepSeek API。
    """
    if latest_price is None:
        return
    monitor = _get_live_monitor()
    if monitor is None:
        return
    loop = getattr(monitor, "_loop", None)
    if not (loop and loop.is_running()):
        return
    try:
        if is_ai_prediction_stale(db, route_id, float(latest_price)):
            import asyncio
            asyncio.run_coroutine_threadsafe(
                monitor.refresh_prediction_for_route(route_id), loop
            )
    except Exception:
        # 失败不影响读路径；下次还会再次检测
        pass


@router.delete("/routes/{route_id}", status_code=204)
def delete_route(route_id: int, db: Session = Depends(get_db)) -> None:
    """Delete a monitored route."""
    # 先从调度器移除，再删 DB —— 避免删除瞬间后台 job 触发采集时路线已不存在
    monitor = _get_live_monitor()
    if monitor is not None:
        try:
            monitor.unschedule_route(route_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("调度器移除路线 %s 失败", route_id)

    service = RouteService(db)
    deleted = service.delete_route(route_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Route not found")


# ── Cities list ────────────────────────────────────────────────────────────


class CityItem(BaseModel):
    name: str
    code: str


@router.get("/cities", response_model=List[CityItem])
def get_cities() -> List[CityItem]:
    """Get all available cities with IATA codes."""
    from flightscanner.utils.city_codes import CITY_CODE_MAP

    return [CityItem(name=name, code=code) for name, code in CITY_CODE_MAP.items()]


# ── Price history ──────────────────────────────────────────────────────────


@router.get("/routes/{route_id}/history", response_model=PriceHistoryResponse)
def get_route_history(
    route_id: int, days: int = 30, db: Session = Depends(get_db)
) -> PriceHistoryResponse:
    """Get detailed price history for a specific route."""
    service = RouteService(db)
    route = service.get_route_by_id(route_id)
    if route is None:
        raise HTTPException(status_code=404, detail="Route not found")
    history = service.get_route_price_history(route_id, days=days)
    # Filter by the route's currently configured time windows so the trend
    # chart and aggregate stats stay consistent with the active constraints.
    history = filter_history_by_route(route, history)

    points = [
        PriceHistoryPoint(
            date=fmt_cst(fp.scraped_at) or "",
            price=float(fp.price),
            source=fp.source,
        )
        for fp in history
    ]
    return PriceHistoryResponse(route_id=route_id, points=points)


# ── Single route detail ───────────────────────────────────────────────────


@router.get("/routes/{route_id}/detail", response_model=RouteDetailResponse)
def get_route_detail(
    route_id: int, db: Session = Depends(get_db)
) -> RouteDetailResponse:
    """Get single route with full detail including config fields."""
    service = RouteService(db)
    route = service.get_route_by_id(route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    today = date.today()
    days_until = (route.target_date - today).days

    raw_history = service.get_route_price_history(route.id, days=14)
    latest_raw_records = _latest_comparable_records(raw_history)
    latest_records = filter_history_by_route(route, latest_raw_records)
    if route.last_flight_status == "filtered_out":
        latest_records = []
    price_history = filter_history_by_route(route, raw_history)
    trend = _analyzer.predict_trend(price_history, route.target_date)

    # Compute the current price from the newest batch only.  Older versions used
    # ``min(price_history[:20])``, which could prefill a purchase with a stale
    # low from a previous scrape whenever the newest batch contained <20 rows.
    latest_price: Optional[float] = None
    if latest_records:
        latest_price = float(min(fp.price for fp in latest_records))

    price_vs_avg_pct: Optional[float] = None
    if price_history and latest_price is not None:
        prices = _batch_min_values(price_history)
        avg_price = sum(prices) / len(prices)
        if avg_price > 0:
            price_vs_avg_pct = round(
                (latest_price - avg_price) / avg_price * 100, 1
            )

    status, ai_reason, ai_confidence = resolve_status(
        db,
        route.id,
        trend.direction,
        price_vs_avg_pct,
        latest_price=latest_price,
        target_price=float(route.target_price) if route.target_price else None,
    )
    if route.last_flight_status == "filtered_out":
        status = "建议观望"
        ai_reason = "最新采集批次暂无符合当前过滤条件的航班"
        ai_confidence = 0.0
    _enqueue_repredict_if_stale(db, route.id, latest_price)
    prediction_text = ai_reason or trend.recommendation
    confidence = ai_confidence if ai_confidence is not None else trend.confidence

    sparkline = _compute_sparkline(price_history)
    flight_info, return_flight_info = _get_latest_itinerary_info(latest_records)

    # Keep the raw latest scrape timestamp even if that batch has zero matches;
    # the card can then distinguish "not scraped" from "no matching flight".
    latest_scraped_at = (
        iso_utc(max(fp.scraped_at for fp in latest_raw_records))
        if latest_raw_records
        else None
    )

    has_alert = (
        route.is_active
        and route.target_price is not None
        and float(route.target_price) > 0
    )

    return RouteDetailResponse(
        id=route.id,
        origin=route.origin,
        destination=route.destination,
        target_date=route.target_date,
        return_date=route.return_date,
        trip_type=route.trip_type,
        target_price=float(route.target_price),
        latest_price=latest_price,
        status=status,
        trend_direction=trend.direction,
        trend_confidence=confidence,
        trend_recommendation=trend.recommendation,
        price_vs_avg_pct=price_vs_avg_pct,
        prediction_text=prediction_text,
        sparkline=sparkline,
        flight_info=flight_info,
        return_flight_info=return_flight_info,
        days_until=days_until,
        has_alert=has_alert,
        is_active=route.is_active,
        monitoring_mode=route.monitoring_mode,
        outbound_flight_no=route.outbound_flight_no,
        inbound_flight_no=route.inbound_flight_no,
        seat_class=route.pinned_seat_class,
        last_flight_status=route.last_flight_status,
        scrape_interval=route.scrape_interval,
        latest_scraped_at=latest_scraped_at,
        dep_airport_code=route.dep_airport_code,
        arr_airport_code=route.arr_airport_code,
        dep_time_from=route.dep_time_from,
        dep_time_to=route.dep_time_to,
        arr_time_from=route.arr_time_from,
        arr_time_to=route.arr_time_to,
        ret_dep_time_from=route.ret_dep_time_from,
        ret_dep_time_to=route.ret_dep_time_to,
        ret_arr_time_from=route.ret_arr_time_from,
        ret_arr_time_to=route.ret_arr_time_to,
        max_arrival_day_offset=route.max_arrival_day_offset,
        ret_max_arrival_day_offset=route.ret_max_arrival_day_offset,
        created_at=fmt_cst(route.created_at),
    )


# ── Update route ──────────────────────────────────────────────────────────


@router.patch("/routes/{route_id}", status_code=200)
def update_route(
    route_id: int, body: UpdateRouteRequest, db: Session = Depends(get_db)
) -> dict:
    """Update route configuration fields.

    Time-window fields accept "" to clear. Arrival-day limits accept 0/1/2,
    explicit null to clear, or omission to leave the current value unchanged.
    """
    route = (
        db.query(Route)
        .filter(Route.id == route_id, Route.deleted_at.is_(None))
        .first()
    )
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    payload = body.model_dump(exclude_unset=True)

    interval_changed = (
        "scrape_interval" in payload and payload["scrape_interval"] is not None
        and payload["scrape_interval"] != route.scrape_interval
    )
    active_changed = "is_active" in payload and payload["is_active"] is not None

    if "target_price" in payload and payload["target_price"] is not None:
        route.target_price = Decimal(str(payload["target_price"]))
    if "scrape_interval" in payload and payload["scrape_interval"] is not None:
        route.scrape_interval = payload["scrape_interval"]
    if "is_active" in payload and payload["is_active"] is not None:
        route.is_active = 1 if payload["is_active"] else 0

    # Time windows: explicit empty-string clears, HH:MM sets, omitted leaves alone.
    time_fields = (
        "dep_time_from", "dep_time_to", "arr_time_from", "arr_time_to",
        "ret_dep_time_from", "ret_dep_time_to", "ret_arr_time_from", "ret_arr_time_to",
    )
    for f in time_fields:
        if f in payload:
            v = payload[f]
            setattr(route, f, v if v else None)

    for field_name in ("max_arrival_day_offset", "ret_max_arrival_day_offset"):
        if field_name in payload:
            setattr(route, field_name, payload[field_name])

    db.commit()
    db.refresh(route)

    # 同步给调度器：间隔变了重新调度；is_active 切换时按状态注册/移除。
    monitor = _get_live_monitor()
    if monitor is not None:
        try:
            if active_changed:
                if route.is_active:
                    monitor.schedule_route(route)
                else:
                    monitor.unschedule_route(route_id)
            elif interval_changed and route.is_active:
                monitor.schedule_route(route)   # schedule_route 内部会先 remove 旧 job
        except Exception:
            import logging
            logging.getLogger(__name__).exception("PATCH 同步调度器失败 route=%s", route_id)

    return {"message": "更新成功"}


# ── Trigger scrape ────────────────────────────────────────────────────────


def _scrape_task_response(
    snapshot: dict,
    message: Optional[str] = None,
) -> ScrapeTaskResponse:
    """Validate a scheduler snapshot and attach a concise user-facing message."""
    status = snapshot["status"]
    if message is None:
        messages = {
            "queued": "采集任务已排队",
            "running": "正在并行采集各平台价格",
            "completed": "所有平台采集完成",
            "partial": "部分平台采集完成，部分平台失败",
            "failed": "采集任务失败",
        }
        message = messages[status]
    return ScrapeTaskResponse.model_validate({**snapshot, "message": message})


@router.post(
    "/routes/{route_id}/scrape",
    status_code=202,
    response_model=ScrapeTaskResponse,
)
def trigger_scrape(
    route_id: int,
    db: Session = Depends(get_db),
) -> ScrapeTaskResponse:
    """Queue an immediate scrape and return a pollable, platform-aware task."""
    import asyncio
    import logging

    service = RouteService(db)
    route = service.get_route_by_id(route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    if not route.is_active:
        raise HTTPException(status_code=400, detail="Route is inactive")

    monitor = _get_live_monitor()
    loop = getattr(monitor, "_loop", None) if monitor else None
    if monitor is None or loop is None or not loop.is_running():
        return ScrapeTaskResponse(
            route_id=route_id,
            status="failed",
            error="后台调度器未运行",
            message="后台调度器未运行，采集任务未执行",
        )

    snapshot = monitor.create_scrape_task(route_id)
    task_id = snapshot["task_id"]
    scrape_coro = monitor.scrape_route(route, task_id=task_id)
    try:
        submitted = asyncio.run_coroutine_threadsafe(
            scrape_coro,
            loop,
        )
    except Exception as exc:
        scrape_coro.close()
        logging.getLogger(__name__).exception(
            "立即采集任务提交失败 route=%s task=%s",
            route_id,
            task_id,
        )
        failed = monitor.fail_scrape_task(task_id, exc) or snapshot
        return _scrape_task_response(failed, "采集任务提交失败")

    def record_submission_failure(done) -> None:
        """Prevent a loop shutdown from leaving a task queued forever."""
        if done.cancelled():
            monitor.fail_scrape_task(task_id, "采集任务在执行前被取消")
            return
        try:
            error = done.exception()
        except BaseException as exc:  # concurrent Future may raise on inspection
            error = exc
        if error is not None:
            monitor.fail_scrape_task(task_id, error)

    submitted.add_done_callback(record_submission_failure)

    return _scrape_task_response(snapshot, "采集任务已提交到后台调度器")


@router.get(
    "/routes/{route_id}/scrape/status",
    response_model=ScrapeTaskResponse,
)
def get_latest_scrape_status(route_id: int) -> ScrapeTaskResponse:
    """Return the latest retained immediate scrape task for a route."""
    monitor = _get_live_monitor()
    if monitor is None:
        raise HTTPException(status_code=503, detail="Background scheduler unavailable")
    snapshot = monitor.get_latest_scrape_task(route_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Scrape task not found")
    return _scrape_task_response(snapshot)


@router.get(
    "/routes/{route_id}/scrape/{task_id}",
    response_model=ScrapeTaskResponse,
)
def get_scrape_status(route_id: int, task_id: str) -> ScrapeTaskResponse:
    """Return a retained immediate scrape task by its stable task ID."""
    monitor = _get_live_monitor()
    if monitor is None:
        raise HTTPException(status_code=503, detail="Background scheduler unavailable")
    snapshot = monitor.get_scrape_task(task_id)
    if snapshot is None or snapshot["route_id"] != route_id:
        raise HTTPException(status_code=404, detail="Scrape task not found")
    return _scrape_task_response(snapshot)


# ── AI Predictions for route ──────────────────────────────────────────────


@router.get("/routes/{route_id}/predictions", response_model=RoutePredictionsResponse)
def get_route_predictions(
    route_id: int, db: Session = Depends(get_db)
) -> RoutePredictionsResponse:
    """Get AI prediction history for a specific route."""
    from sqlalchemy import func

    if RouteService(db).get_route_by_id(route_id) is None:
        raise HTTPException(status_code=404, detail="Route not found")

    rows = (
        db.query(AIPredictionLog)
        .filter(AIPredictionLog.route_id == route_id)
        .order_by(AIPredictionLog.predicted_at.desc())
        .limit(50)
        .all()
    )

    predictions = [
        PredictionLogItem(
            id=row.id,
            predicted_at=fmt_cst(row.predicted_at) or "",
            price_at_prediction=float(row.price_at_prediction),
            recommended_action=row.recommended_action,
            reason=row.reason,
            confidence=float(row.confidence) if row.confidence else None,
            llm_source=row.llm_source,
            outcome_status=row.outcome_status,
            actual_min_price=float(row.actual_min_price) if row.actual_min_price else None,
            pain_index=float(row.pain_index) if row.pain_index else None,
        )
        for row in rows
    ]

    total = (
        db.query(func.count(AIPredictionLog.id))
        .filter(AIPredictionLog.route_id == route_id)
        .scalar()
        or 0
    )
    win_count = (
        db.query(func.count(AIPredictionLog.id))
        .filter(AIPredictionLog.route_id == route_id, AIPredictionLog.outcome_status == "win")
        .scalar()
        or 0
    )
    resolved = (
        db.query(func.count(AIPredictionLog.id))
        .filter(
            AIPredictionLog.route_id == route_id,
            AIPredictionLog.outcome_status.in_(["win", "loss", "neutral"]),
        )
        .scalar()
        or 0
    )
    win_rate = round(win_count / resolved * 100, 1) if resolved > 0 else None

    return RoutePredictionsResponse(
        route_id=route_id,
        predictions=predictions,
        win_rate=win_rate,
        total=total,
    )


# ── Batches & flight list ─────────────────────────────────────────────────


@router.get("/routes/{route_id}/batches", response_model=RouteBatchesResponse)
def get_route_batches(
    route_id: int, limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)
) -> RouteBatchesResponse:
    """List recent batches after applying the route's current constraints."""
    from sqlalchemy import func, tuple_
    from sqlalchemy.orm import aliased

    route = RouteService(db).get_route_by_id(route_id)
    if route is None:
        raise HTTPException(status_code=404, detail="Route not found")

    # Fetch lightweight group metadata first, then load flight rows in bounded
    # chunks until ``limit`` matching groups have been found.  Loading every
    # PriceHistory row made this endpoint grow linearly without bound (a real
    # route already has thousands of quotes), even though the client asks for
    # only the newest 20 groups.
    group_rows = (
        db.query(
            PriceHistory.batch_id,
            PriceHistory.source,
            func.max(PriceHistory.scraped_at).label("scraped_at"),
        )
        .filter(
            PriceHistory.route_id == route_id,
            PriceHistory.batch_id.isnot(None),
        )
        .group_by(PriceHistory.batch_id, PriceHistory.source)
        .order_by(func.max(PriceHistory.scraped_at).desc())
        .all()
    )

    ReturnFlight = aliased(Flight, name="batch_return_flight")
    batches: List[BatchInfo] = []
    chunk_size = max(20, min(100, limit * 2))
    for start in range(0, len(group_rows), chunk_size):
        chunk = group_rows[start:start + chunk_size]
        keys = [(row.batch_id, row.source) for row in chunk]
        rows = (
            db.query(PriceHistory, Flight, ReturnFlight)
            .join(Flight, PriceHistory.flight_id == Flight.id)
            .outerjoin(ReturnFlight, PriceHistory.return_flight_id == ReturnFlight.id)
            .filter(
                PriceHistory.route_id == route_id,
                tuple_(PriceHistory.batch_id, PriceHistory.source).in_(keys),
            )
            .all()
        )

        rows_by_group: dict[tuple[str, str], list] = {}
        for price, flight, return_flight in rows:
            if not itinerary_matches_route(route, flight, return_flight):
                continue
            rows_by_group.setdefault((price.batch_id, price.source), []).append(price)

        for group in chunk:
            matching = rows_by_group.get((group.batch_id, group.source), [])
            if not matching:
                continue
            batches.append(
                BatchInfo(
                    batch_id=group.batch_id,
                    source=group.source,
                    scraped_at=fmt_cst(
                        max(item.scraped_at for item in matching)
                    ) or "",
                    flight_count=len(matching),
                    min_price=min(float(item.price) for item in matching),
                )
            )
            if len(batches) >= limit:
                return RouteBatchesResponse(route_id=route_id, batches=batches)

    return RouteBatchesResponse(route_id=route_id, batches=batches)


@router.get("/routes/{route_id}/flights", response_model=RouteFlightsResponse)
def get_route_flights(
    route_id: int,
    batch_id: Optional[str] = Query(None, description="Specific batch, defaults to latest"),
    source: Optional[str] = Query(
        None,
        min_length=1,
        max_length=50,
        description="Platform source within the batch (for example qunar, ctrip, tongcheng)",
    ),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> RouteFlightsResponse:
    """Get top-N cheapest flights from one platform in a scrape batch.

    A scheduler run can write several platform snapshots with the same
    ``batch_id``.  ``source`` keeps an explicitly selected batch row scoped to
    the platform shown in the UI instead of silently mixing its competitors.
    """
    from sqlalchemy import func
    from sqlalchemy.orm import aliased

    ReturnFlight = aliased(Flight, name="return_flight")
    route = RouteService(db).get_route_by_id(route_id)
    if route is None:
        raise HTTPException(status_code=404, detail="Route not found")

    # Resolve batch_id: if not given, pick the latest for this route
    if not batch_id:
        latest_query = (
            db.query(PriceHistory.batch_id, func.max(PriceHistory.scraped_at).label("scraped_at"))
            .filter(
                PriceHistory.route_id == route_id,
                PriceHistory.batch_id.isnot(None),
            )
        )
        if source:
            latest_query = latest_query.filter(PriceHistory.source == source)
        latest_row = (
            latest_query
            .group_by(PriceHistory.batch_id)
            .order_by(func.max(PriceHistory.scraped_at).desc())
            .first()
        )
        if not latest_row:
            return RouteFlightsResponse(route_id=route_id, flights=[])
        batch_id = latest_row.batch_id

    rows_query = (
        db.query(PriceHistory, Flight, ReturnFlight)
        .join(Flight, PriceHistory.flight_id == Flight.id)
        .outerjoin(ReturnFlight, PriceHistory.return_flight_id == ReturnFlight.id)
        .filter(
            PriceHistory.route_id == route_id,
            PriceHistory.batch_id == batch_id,
        )
    )
    if source:
        rows_query = rows_query.filter(PriceHistory.source == source)
    rows = (
        rows_query.order_by(PriceHistory.price.asc())
        .all()
    )

    if not rows:
        return RouteFlightsResponse(route_id=route_id, batch_id=batch_id, flights=[])

    # Filter before applying the client limit.  Otherwise the cheapest N raw
    # rows can all be ineligible while a valid N+1 row is incorrectly hidden.
    rows = [
        row for row in rows if itinerary_matches_route(route, row[1], row[2])
    ][:limit]
    if not rows:
        return RouteFlightsResponse(route_id=route_id, batch_id=batch_id, flights=[])

    first_scraped = fmt_cst(rows[0][0].scraped_at)
    flights: List[FlightListItem] = []
    for ph, flight, return_flight in rows:
        resolved_arrival_date, _ = _resolved_arrival_date(flight)
        resolved_return_arrival_date, _ = _resolved_arrival_date(return_flight)
        flights.append(
            FlightListItem(
                flight_no=flight.flight_no,
                airline=flight.airline,
                departure_date=flight.departure_date,
                arrival_date=resolved_arrival_date,
                arrival_day_offset=arrival_day_offset(flight),
                departure_time=flight.departure_time,
                arrival_time=flight.arrival_time,
                duration=_compute_duration(
                    flight.departure_time,
                    flight.arrival_time,
                    flight.departure_date,
                    resolved_arrival_date,
                ),
                departure_airport_code=flight.departure_airport_code,
                arrival_airport_code=flight.arrival_airport_code,
                price=float(ph.price),
                seat_class=ph.seat_class,
                available_seats=ph.available_seats,
                source=ph.source,
                batch_id=ph.batch_id,
                return_flight_no=(
                    return_flight.flight_no if return_flight else None
                ),
                return_departure_date=(
                    return_flight.departure_date if return_flight else None
                ),
                return_arrival_date=resolved_return_arrival_date,
                return_arrival_day_offset=(
                    arrival_day_offset(return_flight) if return_flight else None
                ),
                return_departure_time=(
                    return_flight.departure_time if return_flight else None
                ),
                return_arrival_time=(
                    return_flight.arrival_time if return_flight else None
                ),
            )
        )
    return RouteFlightsResponse(
        route_id=route_id,
        batch_id=batch_id,
        scraped_at=first_scraped,
        flights=flights,
    )
