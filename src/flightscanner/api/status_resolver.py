"""Shared status resolver for Dashboard / Stats / Analytics.

Single source of truth for "is this route 建议购买 / 建议观望 / 价格偏高".

Priority order (highest first):
1. Hard rule — current price ≤ target price → 建议购买
2. Hard rule — current price ≪ historical avg (≥15% below) → 建议购买
3. AI prediction (if not stale; staleness = price moved >15% since prediction)
4. Rule-based trend direction

Stale-AI guard exists because price snapshots can shift faster than AI gets re-run.
A 12-hour-old AI prediction at ¥640 should not still surface "价格偏高" when the
current price has dropped to ¥390 — the user's target is hit, the historical
comparison flipped sign, and showing stale AI verdict is confusing/incorrect.
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

# Threshold tuning
_DEEP_BELOW_AVG_PCT = -15.0      # current ≤ avg - 15%  → strong buy signal
_AI_STALE_PRICE_DRIFT = 0.15      # |price now - price@prediction| / price@prediction > 15% → stale


def get_latest_ai_prediction(db: Session, route_id: int) -> Optional[AIPredictionLog]:
    """Most recent AIPredictionLog row for a route, or None."""
    return (
        db.query(AIPredictionLog)
        .filter(AIPredictionLog.route_id == route_id)
        .order_by(AIPredictionLog.predicted_at.desc())
        .first()
    )


def _ai_is_stale(ai: AIPredictionLog, latest_price: Optional[float]) -> bool:
    """Return True if current price diverges materially from price-at-prediction."""
    if latest_price is None or ai.price_at_prediction is None:
        return False
    try:
        paid = float(ai.price_at_prediction)
    except (TypeError, ValueError):
        return False
    if paid <= 0:
        return False
    drift = abs(latest_price - paid) / paid
    return drift > _AI_STALE_PRICE_DRIFT


def resolve_status(
    db: Session,
    route_id: int,
    trend_direction: str,
    price_vs_avg_pct: Optional[float],
    latest_price: Optional[float] = None,
    target_price: Optional[float] = None,
) -> Tuple[str, str, Optional[float]]:
    """Pick the dashboard status string, recommendation text, and confidence.

    Returns (status, recommendation_text, confidence_override).
    """

    # ── Override 1: target price hit ──────────────────────────────────────
    # User explicitly set this threshold; if reached, recommendation is unambiguous.
    if (
        latest_price is not None
        and target_price is not None
        and target_price > 0
        and latest_price <= target_price
    ):
        return (
            "建议购买",
            f"当前价 ¥{int(latest_price)} 已达/低于目标价 ¥{int(target_price)}，建议购买",
            0.95,
        )

    # ── Override 2: price deeply below 14-day average ─────────────────────
    # Even if AI was trained on stale data showing "Wait", a current −15%+ price
    # objectively is a buy signal. AI staleness is most likely cause of mismatch.
    if price_vs_avg_pct is not None and price_vs_avg_pct <= _DEEP_BELOW_AVG_PCT:
        return (
            "建议购买",
            f"当前价较 14 天均价低 {abs(price_vs_avg_pct)}%，建议把握",
            0.85,
        )

    # ── AI-based ──────────────────────────────────────────────────────────
    ai = get_latest_ai_prediction(db, route_id)
    if ai and ai.recommended_action and not _ai_is_stale(ai, latest_price):
        confidence = float(ai.confidence) if ai.confidence else 0.6
        if ai.recommended_action == "Buy":
            return ("建议购买", ai.reason or "AI 建议购买", confidence)
        if price_vs_avg_pct is not None and price_vs_avg_pct > 5:
            return ("价格偏高", ai.reason or "当前价格偏高，建议等待回落", confidence)
        return ("建议观望", ai.reason or "AI 建议继续观察", confidence)

    # ── Rule-based fallback ───────────────────────────────────────────────
    # Hit when no AI record exists, or AI is stale (price drifted >15%).
    fallback_reason = ""
    if ai and _ai_is_stale(ai, latest_price):
        fallback_reason = "AI 预测已过时（价格变化较大），按当前数据评估"
    return (STATUS_MAP.get(trend_direction, "建议观望"), fallback_reason, None)
