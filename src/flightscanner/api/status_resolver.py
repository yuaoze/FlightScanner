"""Shared status resolver for Dashboard / Stats / Analytics.

Single source of truth for "is this route 建议购买 / 建议观望 / 价格偏高".
Prefers the latest AI prediction when present; falls back to the rule-based
trend direction. All three pages must agree, otherwise the KPI counts
disagree with what the cards show.
"""

from typing import Optional, Tuple

from sqlalchemy.orm import Session

from flightscanner.models.database import AIPredictionLog


# Rule-based fallback mapping. Kept in sync with the analyzer's direction names.
STATUS_MAP = {
    "down": "建议购买",
    "up": "价格偏高",
    "stable": "建议观望",
}

STATUS_PRIORITY = {"建议购买": 0, "建议观望": 1, "价格偏高": 2}


def get_latest_ai_prediction(db: Session, route_id: int) -> Optional[AIPredictionLog]:
    """Most recent AIPredictionLog row for a route, or None."""
    return (
        db.query(AIPredictionLog)
        .filter(AIPredictionLog.route_id == route_id)
        .order_by(AIPredictionLog.predicted_at.desc())
        .first()
    )


def resolve_status(
    db: Session,
    route_id: int,
    trend_direction: str,
    price_vs_avg_pct: Optional[float],
) -> Tuple[str, str, Optional[float]]:
    """Pick the dashboard status string, recommendation text, and confidence.

    Returns (status, recommendation_text, confidence_override).
    - AI Buy → 建议购买
    - AI Wait + price_vs_avg > 5 → 价格偏高
    - AI Wait otherwise → 建议观望
    - No AI record → rule-based STATUS_MAP[direction]
    """
    ai = get_latest_ai_prediction(db, route_id)
    if ai and ai.recommended_action:
        confidence = float(ai.confidence) if ai.confidence else 0.6
        if ai.recommended_action == "Buy":
            return ("建议购买", ai.reason or "AI 建议购买", confidence)
        if price_vs_avg_pct is not None and price_vs_avg_pct > 5:
            return ("价格偏高", ai.reason or "当前价格偏高，建议等待回落", confidence)
        return ("建议观望", ai.reason or "AI 建议继续观察", confidence)
    return (STATUS_MAP.get(trend_direction, "建议观望"), "", None)
