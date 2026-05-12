"""Stats API endpoint for Dashboard KPI cards."""

from datetime import date
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from flightscanner.api.deps import get_db
from flightscanner.api.route_filter import filter_history_by_route
from flightscanner.api.schemas import StatsResponse
from flightscanner.api.status_resolver import resolve_status
from flightscanner.analyzers.rule_based_analyzer import RuleBasedAnalyzer
from flightscanner.core.services.route_service import RouteService

router = APIRouter()
_analyzer = RuleBasedAnalyzer()


@router.get("/stats", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db)) -> StatsResponse:
    """Get aggregate statistics for the dashboard KPI cards.

    Uses the shared status resolver so this matches the cards' own labels:
    AI prediction first, rule-based fallback. Counts only `is_active AND
    target_date >= today` routes — same definition as Analytics's KPI.
    """
    service = RouteService(db)
    all_routes = service.get_all_routes()
    today = date.today()

    active_routes = [r for r in all_routes if r.is_active and r.target_date >= today]

    buy_count = 0
    hold_count = 0
    expensive_count = 0
    drop_pcts: List[float] = []
    alert_count = 0

    for route in active_routes:
        price_history = service.get_route_price_history(route.id, days=14)
        # Mirror routes.py — apply the route's time-window filter so the KPI
        # status reflects the same data the cards display.
        price_history = filter_history_by_route(route, price_history)
        trend = _analyzer.predict_trend(price_history, route.target_date)

        price_vs_avg_pct = None
        if price_history and route.latest_price is not None:
            prices = [float(fp.price) for fp in price_history]
            avg_price = sum(prices) / len(prices)
            if avg_price > 0:
                price_vs_avg_pct = round(
                    (float(route.latest_price) - avg_price) / avg_price * 100, 1
                )

        status, _reason, _conf = resolve_status(
            db,
            route.id,
            trend.direction,
            price_vs_avg_pct,
            latest_price=float(route.latest_price) if route.latest_price else None,
            target_price=float(route.target_price) if route.target_price else None,
        )

        if status == "建议购买":
            buy_count += 1
            if price_history and route.latest_price is not None:
                prices = [float(fp.price) for fp in price_history]
                avg_price = sum(prices) / len(prices)
                if avg_price > 0:
                    pct = (avg_price - float(route.latest_price)) / avg_price * 100
                    if pct > 0:
                        drop_pcts.append(pct)
        elif status == "建议观望":
            hold_count += 1
        else:
            expensive_count += 1

        if route.target_price and float(route.target_price) > 0:
            alert_count += 1

    average_drop_pct = round(sum(drop_pcts) / len(drop_pcts), 1) if drop_pcts else None

    return StatsResponse(
        total_monitors=len(active_routes),
        buy_count=buy_count,
        hold_count=hold_count,
        expensive_count=expensive_count,
        average_drop_pct=average_drop_pct,
        alert_count=alert_count,
    )
