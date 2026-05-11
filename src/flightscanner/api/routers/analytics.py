"""Analytics API endpoints for data analysis page."""

from datetime import date, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from flightscanner.api.deps import get_db
from flightscanner.api.time_utils import fmt_cst
from flightscanner.analyzers.rule_based_analyzer import RuleBasedAnalyzer
from flightscanner.core.services.route_service import RouteService
from flightscanner.models.database import AIPredictionLog, PriceHistory, Route

router = APIRouter()
_analyzer = RuleBasedAnalyzer()


class RouteVolatility(BaseModel):
    route_id: int
    origin: str
    destination: str
    volatility_pct: float
    price_range_low: float
    price_range_high: float
    record_count: int


class AIPredictionStats(BaseModel):
    total_predictions: int
    win_count: int
    loss_count: int
    neutral_count: int
    pending_count: int
    accuracy_pct: Optional[float] = None


class PriceTrendPoint(BaseModel):
    date: str
    price: float
    route_label: str


class AnalyticsSummaryResponse(BaseModel):
    total_routes: int
    total_price_records: int
    active_days: int
    volatility_ranking: List[RouteVolatility]
    ai_stats: AIPredictionStats
    recent_trends: List[PriceTrendPoint]


class CalendarDayPrice(BaseModel):
    date: str
    min_price: float
    max_price: float
    avg_price: float
    record_count: int


class CalendarResponse(BaseModel):
    route_id: int
    origin: str
    destination: str
    days: List[CalendarDayPrice]


@router.get("/analytics/summary", response_model=AnalyticsSummaryResponse)
def get_analytics_summary(db: Session = Depends(get_db)) -> AnalyticsSummaryResponse:
    """Get aggregate analytics for the analytics page.

    "活跃航线" = is_active AND target_date >= today, matching the strict filter
    used by /api/stats and the page's own volatility/trend sections.
    """
    today = date.today()

    # Use the same definition as /api/stats and the volatility/trend sections
    # below — strict (active flag AND not expired). Otherwise the top-line KPI
    # disagrees with the lists shown on the same page.
    service = RouteService(db)
    all_routes = service.get_all_routes()
    active_routes = [r for r in all_routes if r.is_active and r.target_date >= today]
    active_route_ids = [r.id for r in active_routes]

    total_routes = len(active_routes)

    # Total records & active days are scoped to the same active routes; otherwise
    # the page would advertise "X 路线 / Y records" where Y includes price points
    # belonging to long-deleted or expired routes.
    if active_route_ids:
        total_records = (
            db.query(func.count(PriceHistory.id))
            .filter(PriceHistory.route_id.in_(active_route_ids))
            .scalar()
            or 0
        )
        active_days_result = (
            db.query(func.count(func.distinct(func.date(PriceHistory.scraped_at))))
            .filter(PriceHistory.route_id.in_(active_route_ids))
            .scalar()
            or 0
        )
    else:
        total_records = 0
        active_days_result = 0

    volatility_list: List[RouteVolatility] = []
    for route in active_routes:
        history = service.get_route_price_history(route.id, days=30)
        if len(history) < 3:
            continue
        prices = [float(fp.price) for fp in history]
        low, high = min(prices), max(prices)
        avg = sum(prices) / len(prices)
        vol_pct = round((high - low) / avg * 100, 1) if avg > 0 else 0
        volatility_list.append(
            RouteVolatility(
                route_id=route.id,
                origin=route.origin,
                destination=route.destination,
                volatility_pct=vol_pct,
                price_range_low=low,
                price_range_high=high,
                record_count=len(history),
            )
        )
    volatility_list.sort(key=lambda v: v.volatility_pct, reverse=True)

    # AI prediction stats
    ai_stats = _get_ai_prediction_stats(db)

    # Recent trends: last 7 days daily min across all active routes (top 5)
    recent_trends: List[PriceTrendPoint] = []
    for route in active_routes[:5]:
        history = service.get_route_price_history(route.id, days=7)
        daily_min: Dict[str, float] = {}
        for fp in history:
            day = fmt_cst(fp.scraped_at, "%m-%d")
            if day is None:
                continue
            price = float(fp.price)
            if day not in daily_min or price < daily_min[day]:
                daily_min[day] = price
        label = f"{route.origin}→{route.destination}"
        for day, price in sorted(daily_min.items()):
            recent_trends.append(PriceTrendPoint(date=day, price=price, route_label=label))

    return AnalyticsSummaryResponse(
        total_routes=total_routes,
        total_price_records=total_records,
        active_days=active_days_result,
        volatility_ranking=volatility_list[:10],
        ai_stats=ai_stats,
        recent_trends=recent_trends,
    )


def _get_ai_prediction_stats(db: Session) -> AIPredictionStats:
    """Compute AI prediction accuracy from AIPredictionLog."""
    try:
        total = db.query(func.count(AIPredictionLog.id)).scalar() or 0
        if total == 0:
            return AIPredictionStats(
                total_predictions=0,
                win_count=0,
                loss_count=0,
                neutral_count=0,
                pending_count=0,
            )
        win = (
            db.query(func.count(AIPredictionLog.id))
            .filter(AIPredictionLog.outcome_status == "win")
            .scalar()
            or 0
        )
        loss = (
            db.query(func.count(AIPredictionLog.id))
            .filter(AIPredictionLog.outcome_status == "loss")
            .scalar()
            or 0
        )
        neutral = (
            db.query(func.count(AIPredictionLog.id))
            .filter(AIPredictionLog.outcome_status == "neutral")
            .scalar()
            or 0
        )
        pending = (
            db.query(func.count(AIPredictionLog.id))
            .filter(AIPredictionLog.outcome_status == "pending")
            .scalar()
            or 0
        )
        resolved = win + loss + neutral
        accuracy = round(win / resolved * 100, 1) if resolved > 0 else None
        return AIPredictionStats(
            total_predictions=total,
            win_count=win,
            loss_count=loss,
            neutral_count=neutral,
            pending_count=pending,
            accuracy_pct=accuracy,
        )
    except Exception:
        return AIPredictionStats(
            total_predictions=0,
            win_count=0,
            loss_count=0,
            neutral_count=0,
            pending_count=0,
        )


@router.get("/routes/{route_id}/calendar", response_model=CalendarResponse)
def get_route_calendar(
    route_id: int,
    month: Optional[str] = Query(None, description="YYYY-MM format, defaults to current month"),
    db: Session = Depends(get_db),
) -> CalendarResponse:
    """Get daily price aggregates for a route in a given month."""
    if month:
        year, mon = map(int, month.split("-"))
    else:
        today = date.today()
        year, mon = today.year, today.month

    start_date = date(year, mon, 1)
    if mon == 12:
        end_date = date(year + 1, 1, 1)
    else:
        end_date = date(year, mon + 1, 1)

    route = db.query(Route).filter(Route.id == route_id).first()
    if not route:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Route not found")

    rows = (
        db.query(
            func.date(PriceHistory.scraped_at).label("day"),
            func.min(PriceHistory.price).label("min_price"),
            func.max(PriceHistory.price).label("max_price"),
            func.avg(PriceHistory.price).label("avg_price"),
            func.count(PriceHistory.id).label("cnt"),
        )
        .filter(
            and_(
                PriceHistory.route_id == route_id,
                func.date(PriceHistory.scraped_at) >= start_date,
                func.date(PriceHistory.scraped_at) < end_date,
            )
        )
        .group_by(func.date(PriceHistory.scraped_at))
        .order_by(func.date(PriceHistory.scraped_at))
        .all()
    )

    days = [
        CalendarDayPrice(
            date=str(row.day),
            min_price=float(row.min_price),
            max_price=float(row.max_price),
            avg_price=round(float(row.avg_price), 1),
            record_count=row.cnt,
        )
        for row in rows
    ]

    return CalendarResponse(
        route_id=route_id,
        origin=route.origin,
        destination=route.destination,
        days=days,
    )
