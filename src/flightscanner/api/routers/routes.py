"""Routes API endpoints for Dashboard data."""

from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from flightscanner.api.deps import get_db
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
    RouteResponse,
    RoutePredictionsResponse,
    SparklinePoint,
    UpdateRouteRequest,
)
from flightscanner.analyzers.rule_based_analyzer import RuleBasedAnalyzer
from flightscanner.api.route_filter import filter_history_by_route
from flightscanner.api.status_resolver import STATUS_MAP, STATUS_PRIORITY, resolve_status
from flightscanner.api.time_utils import fmt_cst, iso_utc
from flightscanner.core.services.route_service import RouteService
from flightscanner.interfaces import FlightPrice
from flightscanner.models.database import AIPredictionLog, Flight, PriceHistory, Route

router = APIRouter()
_analyzer = RuleBasedAnalyzer()


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


def _compute_duration(dep_time: str, arr_time: str) -> Optional[str]:
    """Compute flight duration from HH:MM strings."""
    try:
        dh, dm = map(int, dep_time.split(":"))
        ah, am = map(int, arr_time.split(":"))
        dep_mins = dh * 60 + dm
        arr_mins = ah * 60 + am
        if arr_mins < dep_mins:
            arr_mins += 24 * 60
        diff = arr_mins - dep_mins
        return f"{diff // 60}h{diff % 60:02d}m"
    except (ValueError, AttributeError):
        return None


def _get_latest_flight_info(
    price_history: List[FlightPrice],
) -> Optional[FlightBriefInfo]:
    """Get flight info from the cheapest record in the latest batch."""
    if not price_history:
        return None
    latest_batch_id = price_history[0].batch_id
    if not latest_batch_id:
        latest = price_history[0]
    else:
        batch_records = [fp for fp in price_history if fp.batch_id == latest_batch_id]
        latest = min(batch_records, key=lambda fp: fp.price)
    fi = latest.flight_info
    return FlightBriefInfo(
        flight_no=fi.flight_no,
        airline=fi.airline,
        departure_time=fi.departure_time,
        arrival_time=fi.arrival_time,
        duration=_compute_duration(fi.departure_time, fi.arrival_time),
        departure_airport_code=fi.departure_airport_code,
        arrival_airport_code=fi.arrival_airport_code,
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

        price_history = service.get_route_price_history(route.id, days=14)
        # Apply route time-window/airport filter so the dashboard reflects the
        # currently configured constraints (existing data is filtered, future
        # scrapes are also constrained by the same fields).
        price_history = filter_history_by_route(route, price_history)
        trend = _analyzer.predict_trend(price_history, route.target_date)

        price_vs_avg_pct: Optional[float] = None
        if price_history and route.latest_price is not None:
            prices = [float(fp.price) for fp in price_history]
            avg_price = sum(prices) / len(prices)
            if avg_price > 0:
                price_vs_avg_pct = round(
                    (float(route.latest_price) - avg_price) / avg_price * 100, 1
                )

        status, ai_reason, ai_confidence = resolve_status(
            db, route.id, trend.direction, price_vs_avg_pct
        )
        prediction_text = ai_reason or trend.recommendation
        confidence = ai_confidence if ai_confidence is not None else trend.confidence

        sparkline = _compute_sparkline(price_history)
        flight_info = _get_latest_flight_info(price_history)

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
                latest_price=(
                    float(route.latest_price) if route.latest_price else None
                ),
                status=status,
                trend_direction=trend.direction,
                trend_confidence=confidence,
                trend_recommendation=trend.recommendation,
                price_vs_avg_pct=price_vs_avg_pct,
                prediction_text=prediction_text,
                sparkline=sparkline,
                flight_info=flight_info,
                days_until=days_until,
                has_alert=has_alert,
                is_active=route.is_active,
                monitoring_mode=route.monitoring_mode,
                outbound_flight_no=route.outbound_flight_no,
                seat_class=route.pinned_seat_class,
                latest_scraped_at=iso_utc(route.latest_scraped_at),
                scrape_interval=route.scrape_interval,
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
    max_results: int = 20
    monitoring_mode: str = "route"
    outbound_flight_no: Optional[str] = None
    inbound_flight_no: Optional[str] = None
    pinned_seat_class: Optional[str] = None


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
    history = service.get_route_price_history(route_id, days=days)
    # Filter by the route's currently configured time windows so the trend
    # chart and aggregate stats stay consistent with the active constraints.
    route = db.query(Route).filter(Route.id == route_id).first()
    if route is not None:
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

    price_history = service.get_route_price_history(route.id, days=14)
    price_history = filter_history_by_route(route, price_history)
    trend = _analyzer.predict_trend(price_history, route.target_date)

    # Compute latest_price from history
    latest_price: Optional[float] = None
    if price_history:
        latest_price = float(min(fp.price for fp in price_history[:20]))

    price_vs_avg_pct: Optional[float] = None
    if price_history and latest_price is not None:
        prices = [float(fp.price) for fp in price_history]
        avg_price = sum(prices) / len(prices)
        if avg_price > 0:
            price_vs_avg_pct = round(
                (latest_price - avg_price) / avg_price * 100, 1
            )

    status, ai_reason, ai_confidence = resolve_status(
        db, route.id, trend.direction, price_vs_avg_pct
    )
    prediction_text = ai_reason or trend.recommendation
    confidence = ai_confidence if ai_confidence is not None else trend.confidence

    sparkline = _compute_sparkline(price_history)
    flight_info = _get_latest_flight_info(price_history)

    # Latest scrape timestamp (price_history is sorted desc)
    latest_scraped_at = iso_utc(price_history[0].scraped_at) if price_history else None

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
        days_until=days_until,
        has_alert=has_alert,
        is_active=route.is_active,
        monitoring_mode=route.monitoring_mode,
        outbound_flight_no=route.outbound_flight_no,
        seat_class=route.pinned_seat_class,
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
        created_at=fmt_cst(route.created_at),
    )


# ── Update route ──────────────────────────────────────────────────────────


@router.patch("/routes/{route_id}", status_code=200)
def update_route(
    route_id: int, body: UpdateRouteRequest, db: Session = Depends(get_db)
) -> dict:
    """Update route configuration fields.

    For time-window fields (dep_/arr_/ret_dep_/ret_arr_*), pass the empty
    string "" to clear the field, "HH:MM" to set a value, or omit it entirely
    to leave it unchanged.
    """
    route = db.query(Route).filter(Route.id == route_id).first()
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


@router.post("/routes/{route_id}/scrape", status_code=202)
def trigger_scrape(route_id: int, db: Session = Depends(get_db)) -> dict:
    """Trigger an immediate scrape for a route (fire-and-forget)."""
    import asyncio

    service = RouteService(db)
    route = service.get_route_by_id(route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    if not route.is_active:
        raise HTTPException(status_code=400, detail="Route is inactive")

    try:
        from flightscanner.api import main as api_main

        monitor = api_main._monitor
        loop = getattr(monitor, "_loop", None) if monitor else None
        if monitor and loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(monitor.scrape_route(route), loop)
            return {"message": "采集任务已提交到后台调度器", "status": "queued"}
    except Exception:
        pass

    return {
        "message": "采集任务已记录，但后台调度器未运行，暂未执行",
        "status": "scheduler_unavailable",
    }


# ── AI Predictions for route ──────────────────────────────────────────────


@router.get("/routes/{route_id}/predictions", response_model=RoutePredictionsResponse)
def get_route_predictions(
    route_id: int, db: Session = Depends(get_db)
) -> RoutePredictionsResponse:
    """Get AI prediction history for a specific route."""
    from sqlalchemy import func

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
    """List recent scrape batches for a route."""
    from sqlalchemy import func

    rows = (
        db.query(
            PriceHistory.batch_id,
            PriceHistory.source,
            func.max(PriceHistory.scraped_at).label("scraped_at"),
            func.count(PriceHistory.id).label("cnt"),
            func.min(PriceHistory.price).label("min_price"),
        )
        .filter(
            PriceHistory.route_id == route_id,
            PriceHistory.batch_id.isnot(None),
        )
        .group_by(PriceHistory.batch_id, PriceHistory.source)
        .order_by(func.max(PriceHistory.scraped_at).desc())
        .limit(limit)
        .all()
    )

    batches = [
        BatchInfo(
            batch_id=row.batch_id,
            source=row.source,
            scraped_at=fmt_cst(row.scraped_at) or "",
            flight_count=row.cnt,
            min_price=float(row.min_price),
        )
        for row in rows
    ]
    return RouteBatchesResponse(route_id=route_id, batches=batches)


@router.get("/routes/{route_id}/flights", response_model=RouteFlightsResponse)
def get_route_flights(
    route_id: int,
    batch_id: Optional[str] = Query(None, description="Specific batch, defaults to latest"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> RouteFlightsResponse:
    """Get top-N cheapest flights from a batch (or the latest batch if not specified)."""
    from sqlalchemy import func
    from sqlalchemy.orm import aliased

    ReturnFlight = aliased(Flight, name="return_flight")

    # Resolve batch_id: if not given, pick the latest for this route
    if not batch_id:
        latest_row = (
            db.query(PriceHistory.batch_id, func.max(PriceHistory.scraped_at).label("scraped_at"))
            .filter(
                PriceHistory.route_id == route_id,
                PriceHistory.batch_id.isnot(None),
            )
            .group_by(PriceHistory.batch_id)
            .order_by(func.max(PriceHistory.scraped_at).desc())
            .first()
        )
        if not latest_row:
            return RouteFlightsResponse(route_id=route_id, flights=[])
        batch_id = latest_row.batch_id

    rows = (
        db.query(PriceHistory, Flight, ReturnFlight)
        .join(Flight, PriceHistory.flight_id == Flight.id)
        .outerjoin(ReturnFlight, PriceHistory.return_flight_id == ReturnFlight.id)
        .filter(
            PriceHistory.route_id == route_id,
            PriceHistory.batch_id == batch_id,
        )
        .order_by(PriceHistory.price.asc())
        .limit(limit)
        .all()
    )

    if not rows:
        return RouteFlightsResponse(route_id=route_id, batch_id=batch_id, flights=[])

    # Apply route time-window/airport filter to mirror the filter applied at
    # scrape time, so existing batches reflect the user's current settings.
    from flightscanner.api.route_filter import _hhmm_to_minutes, _in_window

    route = db.query(Route).filter(Route.id == route_id).first()
    if route is not None:
        dep_airport = route.dep_airport_code
        arr_airport = route.arr_airport_code
        dep_from = _hhmm_to_minutes(route.dep_time_from)
        dep_to = _hhmm_to_minutes(route.dep_time_to)
        arr_from = _hhmm_to_minutes(route.arr_time_from)
        arr_to = _hhmm_to_minutes(route.arr_time_to)
        ret_dep_from = _hhmm_to_minutes(route.ret_dep_time_from)
        ret_dep_to = _hhmm_to_minutes(route.ret_dep_time_to)
        ret_arr_from = _hhmm_to_minutes(route.ret_arr_time_from)
        ret_arr_to = _hhmm_to_minutes(route.ret_arr_time_to)

        def _row_passes(flight: Flight, return_flight: Optional[Flight]) -> bool:
            if dep_airport and flight.departure_airport_code and flight.departure_airport_code != dep_airport:
                return False
            if arr_airport and flight.arrival_airport_code and flight.arrival_airport_code != arr_airport:
                return False
            if not _in_window(flight.departure_time, dep_from, dep_to):
                return False
            if not _in_window(flight.arrival_time, arr_from, arr_to):
                return False
            if return_flight is not None and getattr(return_flight, "flight_no", "") != "VIRTUAL_RETURN":
                # Return leg airports reverse
                if arr_airport and return_flight.departure_airport_code and return_flight.departure_airport_code != arr_airport:
                    return False
                if dep_airport and return_flight.arrival_airport_code and return_flight.arrival_airport_code != dep_airport:
                    return False
                if not _in_window(return_flight.departure_time, ret_dep_from, ret_dep_to):
                    return False
                if not _in_window(return_flight.arrival_time, ret_arr_from, ret_arr_to):
                    return False
            return True

        rows = [r for r in rows if _row_passes(r[1], r[2])]
        if not rows:
            return RouteFlightsResponse(route_id=route_id, batch_id=batch_id, flights=[])

    first_scraped = fmt_cst(rows[0][0].scraped_at)
    flights = [
        FlightListItem(
            flight_no=flight.flight_no,
            airline=flight.airline,
            departure_time=flight.departure_time,
            arrival_time=flight.arrival_time,
            duration=_compute_duration(flight.departure_time, flight.arrival_time),
            departure_airport_code=flight.departure_airport_code,
            arrival_airport_code=flight.arrival_airport_code,
            price=float(ph.price),
            seat_class=ph.seat_class,
            available_seats=ph.available_seats,
            source=ph.source,
            batch_id=ph.batch_id,
            return_flight_no=return_flight.flight_no if return_flight else None,
            return_departure_time=return_flight.departure_time if return_flight else None,
            return_arrival_time=return_flight.arrival_time if return_flight else None,
        )
        for ph, flight, return_flight in rows
    ]
    return RouteFlightsResponse(
        route_id=route_id,
        batch_id=batch_id,
        scraped_at=first_scraped,
        flights=flights,
    )
