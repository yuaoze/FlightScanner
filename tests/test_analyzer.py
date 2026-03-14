"""Unit tests for RuleBasedAnalyzer."""

import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from flightscanner.interfaces import FlightDirection, FlightInfo, FlightPrice
from flightscanner.analyzers import RuleBasedAnalyzer


@pytest.fixture
def analyzer():
    """Create an analyzer instance."""
    return RuleBasedAnalyzer()


@pytest.fixture
def sample_flight_prices():
    """Create sample historical flight prices for testing."""
    base_date = date.today() + timedelta(days=7)
    prices = []

    # Create prices for the last 10 days with varying prices
    for i in range(10):
        flight_info = FlightInfo(
            flight_no="CA1234",
            airline="中国国航",
            departure_city="北京",
            arrival_city="上海",
            departure_time="08:00",
            arrival_time="10:30",
            departure_date=base_date,
            direction=FlightDirection.DEPARTURE,
        )

        # Price fluctuates around 700
        price_value = 700 + (i % 3 - 1) * 50  # Prices: 650, 700, 750, 650, 700, ...

        fp = FlightPrice(
            flight_info=flight_info,
            price=Decimal(str(price_value)),
            currency="CNY",
            seat_class="经济舱",
            available_seats=15,
            scraped_at=datetime.now(timezone.utc) - timedelta(days=10 - i),
            source="ctrip",
        )
        prices.append(fp)

    return prices


class TestRuleBasedAnalyzer:
    """Test cases for RuleBasedAnalyzer."""

    def test_predict_trend_with_down_trend(self, analyzer: RuleBasedAnalyzer):
        """Test trend prediction for decreasing prices."""
        # Create prices that are trending down
        # When sorted by scraped_at desc (most recent first), we want:
        # prices[0] = 600 (most recent), prices[4] = 800 (oldest)
        # This represents a DOWN trend over time
        prices = []
        base_date = date.today() + timedelta(days=7)

        for i in range(5):
            flight_info = FlightInfo(
                flight_no="CA1234",
                airline="中国国航",
                departure_city="北京",
                arrival_city="上海",
                departure_time="08:00",
                arrival_time="10:30",
                departure_date=base_date,
                direction=FlightDirection.DEPARTURE,
            )

            # Prices increasing in the list, but since most recent is first,
            # this represents a decreasing trend over time
            # i=0: 600 (now), i=4: 800 (4 days ago) -> DOWN trend
            price_value = 600 + i * 50

            fp = FlightPrice(
                flight_info=flight_info,
                price=Decimal(str(price_value)),
                currency="CNY",
                seat_class="经济舱",
                available_seats=15,
                # Most recent price first (sorted by scraped_at desc)
                scraped_at=datetime.now(timezone.utc) - timedelta(days=i),
                source="ctrip",
            )
            prices.append(fp)

        trend = analyzer.predict_trend(prices, base_date)

        assert trend.direction == "down"
        assert trend.confidence > 0
        assert "下降" in trend.recommendation

    def test_predict_trend_with_up_trend(self, analyzer: RuleBasedAnalyzer):
        """Test trend prediction for increasing prices."""
        # Create prices that are trending up
        # When sorted by scraped_at desc (most recent first), we want:
        # prices[0] = 800 (most recent), prices[4] = 600 (oldest)
        # This represents an UP trend over time
        prices = []
        base_date = date.today() + timedelta(days=7)

        for i in range(5):
            flight_info = FlightInfo(
                flight_no="CA1234",
                airline="中国国航",
                departure_city="北京",
                arrival_city="上海",
                departure_time="08:00",
                arrival_time="10:30",
                departure_date=base_date,
                direction=FlightDirection.DEPARTURE,
            )

            # Prices decreasing in the list, but since most recent is first,
            # this represents an increasing trend over time
            # i=0: 800 (now), i=4: 600 (4 days ago) -> UP trend
            price_value = 800 - i * 50

            fp = FlightPrice(
                flight_info=flight_info,
                price=Decimal(str(price_value)),
                currency="CNY",
                seat_class="经济舱",
                available_seats=15,
                # Most recent price first (sorted by scraped_at desc)
                scraped_at=datetime.now(timezone.utc) - timedelta(days=i),
                source="ctrip",
            )
            prices.append(fp)

        trend = analyzer.predict_trend(prices, base_date)

        assert trend.direction == "up"
        assert trend.confidence > 0
        assert "上升" in trend.recommendation

    def test_predict_trend_with_stable_prices(self, analyzer: RuleBasedAnalyzer):
        """Test trend prediction for stable prices."""
        # Create stable prices around 700
        prices = []
        base_date = date.today() + timedelta(days=7)

        for i in range(5):
            flight_info = FlightInfo(
                flight_no="CA1234",
                airline="中国国航",
                departure_city="北京",
                arrival_city="上海",
                departure_time="08:00",
                arrival_time="10:30",
                departure_date=base_date,
                direction=FlightDirection.DEPARTURE,
            )

            # Prices stable around 700 (within ±5%)
            price_value = 700 + (i % 2) * 20  # 700, 720, 700, 720, 700

            fp = FlightPrice(
                flight_info=flight_info,
                price=Decimal(str(price_value)),
                currency="CNY",
                seat_class="经济舱",
                available_seats=15,
                scraped_at=datetime.now(timezone.utc) - timedelta(days=5 - i),
                source="ctrip",
            )
            prices.append(fp)

        trend = analyzer.predict_trend(prices, base_date)

        assert trend.direction == "stable"
        assert trend.confidence >= 0.5
        assert "稳定" in trend.recommendation

    def test_predict_trend_with_empty_data(self, analyzer: RuleBasedAnalyzer):
        """Test trend prediction with no historical data."""
        trend = analyzer.predict_trend([], date.today() + timedelta(days=7))

        assert trend.direction == "stable"
        assert trend.confidence == 0.0
        assert "暂无历史数据" in trend.recommendation

    def test_predict_trend_with_single_price(self, analyzer: RuleBasedAnalyzer):
        """Test trend prediction with only one price data point."""
        base_date = date.today() + timedelta(days=7)

        flight_info = FlightInfo(
            flight_no="CA1234",
            airline="中国国航",
            departure_city="北京",
            arrival_city="上海",
            departure_time="08:00",
            arrival_time="10:30",
            departure_date=base_date,
            direction=FlightDirection.DEPARTURE,
        )

        fp = FlightPrice(
            flight_info=flight_info,
            price=Decimal("700.00"),
            currency="CNY",
            seat_class="经济舱",
            available_seats=15,
            scraped_at=datetime.now(timezone.utc),
            source="ctrip",
        )

        trend = analyzer.predict_trend([fp], base_date)

        # With single data point, should be stable
        assert trend.direction == "stable"
        assert trend.confidence == 0.5

    def test_should_alert_when_price_below_threshold(self, analyzer: RuleBasedAnalyzer):
        """Test alert condition when price is below threshold."""
        base_date = date.today() + timedelta(days=7)

        # Create a strong down trend with high confidence (>0.5)
        # Current price (first in list) should be much lower than average
        prices = []
        for i in range(10):
            flight_info = FlightInfo(
                flight_no="CA1234",
                airline="中国国航",
                departure_city="北京",
                arrival_city="上海",
                departure_time="08:00",
                arrival_time="10:30",
                departure_date=base_date,
                direction=FlightDirection.DEPARTURE,
            )

            # Create prices: i=0 (now): 500, i=9: 950
            # Average = 725, current = 500, diff = 225, confidence = 225/725 = 0.31
            # Need bigger difference: let's use 400 to 900 range
            # Average = 650, current = 400, diff = 250, confidence = 250/650 = 0.38
            # Still not enough. Use 350 to 1000 range
            # Average = 675, current = 350, diff = 325, confidence = 325/675 = 0.48
            # Use 300 to 1000: avg = 650, current = 300, diff = 350, conf = 350/650 = 0.54 ✓
            price_value = 300 + i * 70  # 300, 370, 440, ... 930

            fp = FlightPrice(
                flight_info=flight_info,
                price=Decimal(str(price_value)),
                currency="CNY",
                seat_class="经济舱",
                available_seats=15,
                scraped_at=datetime.now(timezone.utc) - timedelta(days=i),
                source="ctrip",
            )
            prices.append(fp)

        trend = analyzer.predict_trend(prices, base_date)
        current_price = Decimal("280.00")  # Below threshold and below current
        threshold = Decimal("700.00")

        # Price is below threshold and trend is down with good confidence
        assert trend.direction == "down"
        assert trend.confidence >= 0.5
        assert analyzer.should_alert(current_price, trend, threshold) is True

    def test_should_alert_when_price_above_threshold(self, analyzer: RuleBasedAnalyzer):
        """Test alert condition when price is above threshold."""
        base_date = date.today() + timedelta(days=7)

        # Create prices
        prices = []
        for i in range(5):
            flight_info = FlightInfo(
                flight_no="CA1234",
                airline="中国国航",
                departure_city="北京",
                arrival_city="上海",
                departure_time="08:00",
                arrival_time="10:30",
                departure_date=base_date,
                direction=FlightDirection.DEPARTURE,
            )

            price_value = 600 + i * 50

            fp = FlightPrice(
                flight_info=flight_info,
                price=Decimal(str(price_value)),
                currency="CNY",
                seat_class="经济舱",
                available_seats=15,
                scraped_at=datetime.now(timezone.utc) - timedelta(days=5 - i),
                source="ctrip",
            )
            prices.append(fp)

        trend = analyzer.predict_trend(prices, base_date)
        current_price = Decimal("800.00")
        threshold = Decimal("700.00")

        # Price is above threshold
        assert analyzer.should_alert(current_price, trend, threshold) is False

    def test_should_alert_when_trend_is_up(self, analyzer: RuleBasedAnalyzer):
        """Test that alert is not sent when price trend is up."""
        base_date = date.today() + timedelta(days=7)

        # Create up trend
        prices = []
        for i in range(5):
            flight_info = FlightInfo(
                flight_no="CA1234",
                airline="中国国航",
                departure_city="北京",
                arrival_city="上海",
                departure_time="08:00",
                arrival_time="10:30",
                departure_date=base_date,
                direction=FlightDirection.DEPARTURE,
            )

            price_value = 600 + i * 50

            fp = FlightPrice(
                flight_info=flight_info,
                price=Decimal(str(price_value)),
                currency="CNY",
                seat_class="经济舱",
                available_seats=15,
                scraped_at=datetime.now(timezone.utc) - timedelta(days=5 - i),
                source="ctrip",
            )
            prices.append(fp)

        trend = analyzer.predict_trend(prices, base_date)
        current_price = Decimal("600.00")
        threshold = Decimal("700.00")

        # Even though price is below threshold, trend is up
        assert analyzer.should_alert(current_price, trend, threshold) is False

    def test_should_alert_with_low_confidence(self, analyzer: RuleBasedAnalyzer):
        """Test that alert is not sent with low confidence."""
        base_date = date.today() + timedelta(days=7)

        # Create a stable trend with low confidence
        prices = []
        for i in range(5):
            flight_info = FlightInfo(
                flight_no="CA1234",
                airline="中国国航",
                departure_city="北京",
                arrival_city="上海",
                departure_time="08:00",
                arrival_time="10:30",
                departure_date=base_date,
                direction=FlightDirection.DEPARTURE,
            )

            price_value = 700 + (i % 2) * 20

            fp = FlightPrice(
                flight_info=flight_info,
                price=Decimal(str(price_value)),
                currency="CNY",
                seat_class="经济舱",
                available_seats=15,
                scraped_at=datetime.now(timezone.utc) - timedelta(days=5 - i),
                source="ctrip",
            )
            prices.append(fp)

        trend = analyzer.predict_trend(prices, base_date)

        # Manually set low confidence for testing
        trend.confidence = 0.05

        current_price = Decimal("650.00")
        threshold = Decimal("700.00")

        # Confidence is below 0.1
        assert analyzer.should_alert(current_price, trend, threshold) is False

    def test_predict_trend_returns_all_fields(self, analyzer: RuleBasedAnalyzer, sample_flight_prices):
        """Test that predict_trend returns all required fields."""
        base_date = date.today() + timedelta(days=7)
        trend = analyzer.predict_trend(sample_flight_prices, base_date)

        assert hasattr(trend, 'direction')
        assert hasattr(trend, 'confidence')
        assert hasattr(trend, 'recommendation')
        assert hasattr(trend, 'predicted_lowest_price')
        assert hasattr(trend, 'best_booking_time')

        assert trend.direction in ['down', 'up', 'stable']
        assert 0 <= trend.confidence <= 1
        assert isinstance(trend.recommendation, str)
        assert len(trend.recommendation) > 0
