"""Rule-based price analyzer implementation.

This module provides a simple rule-based implementation of the PriceAnalyzer
interface that uses statistical analysis of historical prices to predict trends
and generate recommendations.
"""

from datetime import date, datetime
from decimal import Decimal
from statistics import median, stdev
from typing import Dict, List, Optional

from flightscanner.interfaces import FlightPrice, PriceAnalyzer, PriceTrend


def _batch_snapshots(
    price_records: List[FlightPrice],
) -> List[tuple[Decimal, datetime]]:
    """Return (minimum price, latest scrape time) in first-seen batch order."""
    batches: Dict[tuple[str, str | datetime], tuple[Decimal, datetime]] = {}
    for fp in price_records:
        key = ("id", fp.batch_id) if fp.batch_id else ("time", fp.scraped_at)
        if key in batches:
            price, scraped_at = batches[key]
            batches[key] = (min(price, fp.price), max(scraped_at, fp.scraped_at))
        else:
            batches[key] = (fp.price, fp.scraped_at)
    return list(batches.values())


def _batch_min_prices(price_records: List[FlightPrice]) -> List[float]:
    """Return the minimum price per scrape batch.

    Records that share a ``batch_id`` belong to the same scrape session.
    Records without a ``batch_id`` (legacy data) share a batch only when
    their ``scraped_at`` timestamps are equal.

    Using per-batch minimums as the unit normalises sessions with different
    record counts and focuses analysis on the cheapest available fare.

    Args:
        price_records: Price history records, in any order.

    Returns:
        Per-batch minimum prices (floats), in first-seen batch order.
    """
    return [float(price) for price, _ in _batch_snapshots(price_records)]


def _latest_batch_snapshot(
    price_records: List[FlightPrice],
) -> Optional[tuple[Decimal, datetime]]:
    """Return the latest batch's (minimum price, latest scrape time), or None.

    Batch identity follows :func:`_batch_min_prices`. Equally recent batches
    are resolved by lowest price so the result never depends on input order.
    """
    return max(
        _batch_snapshots(price_records),
        key=lambda snapshot: (snapshot[1], -snapshot[0]),
        default=None,
    )


class RuleBasedAnalyzer(PriceAnalyzer):
    """Rule-based price trend analyzer.

    This analyzer uses statistical methods to analyze historical price data
    and predict future price trends. It implements simple rules based on
    price averages and variance.

    The analysis rules are:
    - Down trend: Current price is more than 10% below average
    - Up trend: Current price is more than 10% above average
    - Stable: Price is within ±10% of average
    """

    def predict_trend(
        self, historical_prices: List[FlightPrice], target_date: date
    ) -> PriceTrend:
        """Analyze historical prices and predict future trend.

        Args:
            historical_prices: Historical price data for analysis.
            target_date: Target departure date to analyze for.

        Returns:
            PriceTrend with direction, confidence, and recommendations.
        """
        latest_batch = _latest_batch_snapshot(historical_prices)
        if latest_batch is None:
            # No data available
            return PriceTrend(
                direction="stable",
                confidence=0.0,
                recommendation="暂无历史数据，无法分析价格趋势",
                predicted_lowest_price=None,
                best_booking_time=None,
            )

        # Extract prices; current price is the latest batch's cheapest fare.
        prices = [float(fp.price) for fp in historical_prices]
        current_price = latest_batch[0]

        # Calculate statistics
        # avg_price is the median of per-batch minimum prices, which is robust
        # against outlier promotional fares and normalises sessions by count.
        batch_mins = _batch_min_prices(historical_prices)
        avg_price = median(batch_mins)
        min_price = min(prices)
        max_price = max(prices)

        # Calculate standard deviation if we have enough data
        if len(prices) >= 2:
            price_stdev = stdev(prices)
        else:
            price_stdev = 0.0

        # Determine trend direction
        # Allow 10% deviation from average for "stable" classification
        threshold = avg_price * 0.10
        price_diff = float(current_price) - avg_price

        if price_diff < -threshold:
            direction = "down"
            confidence = min(0.9, abs(price_diff) / avg_price)
            recommendation = (
                f"价格呈下降趋势（当前价格 {current_price} 元低于平均价 "
                f"{avg_price:.0f} 元）。建议继续观察，等待更低价格。"
            )
        elif price_diff > threshold:
            direction = "up"
            confidence = min(0.9, abs(price_diff) / avg_price)
            recommendation = (
                f"价格呈上升趋势（当前价格 {current_price} 元高于平均价 "
                f"{avg_price:.0f} 元）。建议尽快购买，避免价格继续上涨。"
            )
        else:
            direction = "stable"
            confidence = 0.5
            recommendation = (
                f"价格相对稳定（当前价格 {current_price} 元接近平均价 "
                f"{avg_price:.0f} 元）。建议根据行程安排选择合适时机购买。"
            )

        # Predict lowest price (use minimum historical price as reference)
        predicted_lowest = Decimal(str(min_price * 0.95))  # Assume 5% lower than min

        # Estimate best booking time
        # Simple heuristic: if trend is down, suggest waiting; otherwise suggest soon
        if direction == "down":
            days_until_target = (target_date - date.today()).days
            if days_until_target > 14:
                best_booking_time = datetime.now().replace(
                    hour=10, minute=0, second=0, microsecond=0
                )
                recommendation += f" 预计最佳购票时间在出发前 7-14 天。"
            else:
                best_booking_time = None
                recommendation += " 出发日期临近，建议尽快关注价格变化。"
        elif direction == "up":
            best_booking_time = datetime.now()
            recommendation += " 建议立即购买。"
        else:
            # For stable trend, suggest booking 7-14 days before departure
            days_until_target = (target_date - date.today()).days
            if days_until_target > 14:
                best_booking_time = None
                recommendation += " 建议在出发前 7-14 天购买。"
            else:
                best_booking_time = datetime.now()
                recommendation += " 建议尽快购买。"

        return PriceTrend(
            direction=direction,
            confidence=round(confidence, 2),
            recommendation=recommendation,
            predicted_lowest_price=predicted_lowest,
            best_booking_time=best_booking_time,
        )

    def should_alert(
        self, current_price: Decimal, trend: PriceTrend, threshold: Decimal
    ) -> bool:
        """Determine if an alert should be sent.

        Alert conditions:
        1. Current price is below threshold
        2. Trend is down or stable (not going up)
        3. Confidence is at least 0.5

        Args:
            current_price: Current flight price.
            trend: Analyzed price trend.
            threshold: User-defined price threshold.

        Returns:
            True if an alert should be sent.
        """
        # Check if price is below threshold
        if current_price >= threshold:
            return False

        # Check trend direction (don't alert if price is going up)
        if trend.direction == "up":
            return False

        # Check confidence
        if trend.confidence < 0.1:
            return False

        return True
