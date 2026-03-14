"""End-to-end tests for FlightScanner MVP.

These tests verify the complete workflow:
1. Scrape flight data (or use mock data)
2. Save to database
3. Analyze prices
4. Send notifications

Usage:
    pytest tests/test_e2e.py -v
"""

import asyncio
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from flightscanner.interfaces import (
    FlightDirection,
    FlightInfo,
    FlightPrice,
    PriceTrend,
    SearchParams,
)
from flightscanner.models import Flight, PriceHistory, init_db
from flightscanner.repositories import SQLAlchemyRepository
from flightscanner.analyzers import RuleBasedAnalyzer
from flightscanner.notifiers import EmailNotifier
from flightscanner.utils.config import Settings


@pytest.fixture
def db_session():
    """Create an in-memory database session for testing."""
    engine, SessionLocal = init_db("sqlite:///:memory:")
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def repository(db_session):
    """Create a repository instance."""
    return SQLAlchemyRepository(db_session)


@pytest.fixture
def analyzer():
    """Create an analyzer instance."""
    return RuleBasedAnalyzer()


@pytest.fixture
def mock_settings():
    """Create mock settings for testing."""
    settings = Mock(spec=Settings)
    settings.smtp_host = "smtp.example.com"
    settings.smtp_port = 587
    settings.smtp_user = "test@example.com"
    settings.smtp_password = "test_password"
    settings.alert_price_threshold = 700
    settings.database_url = "sqlite:///:memory:"
    return settings


@pytest.fixture
def notifier(mock_settings):
    """Create an EmailNotifier instance."""
    return EmailNotifier(mock_settings)


@pytest.fixture
def sample_flight_prices():
    """Create sample flight prices for testing."""
    prices = []
    base_date = date.today() + timedelta(days=7)
    # 使用固定时间戳（15天前），确保所有 sample 价格时间一致且
    # 早于趋势历史数据（1-10天前），以便趋势分析取到最新的历史价格
    scraped_at = datetime.now(timezone.utc) - timedelta(days=15)

    # Create multiple flights with different prices
    flight_data = [
        ("CA1234", "中国国航", "08:00", "10:30", Decimal("680.00"), "经济舱"),
        ("CA1234", "中国国航", "08:00", "10:30", Decimal("1280.00"), "商务舱"),
        ("MU5678", "东方航空", "14:00", "16:30", Decimal("650.00"), "经济舱"),
        ("MU5678", "东方航空", "14:00", "16:30", Decimal("1200.00"), "商务舱"),
    ]

    for flight_no, airline, dep_time, arr_time, price, seat_class in flight_data:
        flight_info = FlightInfo(
            flight_no=flight_no,
            airline=airline,
            departure_city="北京",
            arrival_city="上海",
            departure_time=dep_time,
            arrival_time=arr_time,
            departure_date=base_date,
            direction=FlightDirection.DEPARTURE,
        )

        fp = FlightPrice(
            flight_info=flight_info,
            price=price,
            currency="CNY",
            seat_class=seat_class,
            available_seats=15,
            scraped_at=scraped_at,
            source="ctrip",
        )
        prices.append(fp)

    return prices


class TestEndToEnd:
    """End-to-end test cases."""

    @pytest.mark.asyncio
    async def test_complete_workflow_with_alert(
        self,
        repository: SQLAlchemyRepository,
        analyzer: RuleBasedAnalyzer,
        notifier: EmailNotifier,
        sample_flight_prices: list,
    ):
        """Test complete workflow: save → analyze → alert."""
        # Step 1: Save flight prices
        price_ids = []
        for fp in sample_flight_prices:
            price_id = repository.save_price(fp)
            price_ids.append(price_id)

        assert len(price_ids) == 4
        assert all(pid > 0 for pid in price_ids)

        # Step 2: Add historical data for analysis
        # Create prices from the past 10 days
        base_date = date.today() + timedelta(days=7)
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

            # Prices trending down: 800 -> 600
            price_value = 800 - i * 20

            fp = FlightPrice(
                flight_info=flight_info,
                price=Decimal(str(price_value)),
                currency="CNY",
                seat_class="经济舱",
                available_seats=15,
                scraped_at=datetime.now(timezone.utc) - timedelta(days=10 - i),
                source="ctrip",
            )
            repository.save_price(fp)

        # Step 3: Get historical prices and analyze
        historical_prices = repository.get_history(
            departure_city="北京",
            arrival_city="上海",
            days=30,
        )

        assert len(historical_prices) > 0

        trend = analyzer.predict_trend(historical_prices, base_date)

        # Trend should be down
        assert trend.direction == "down"
        assert trend.confidence > 0

        # Step 4: Check alert conditions
        lowest_current_price = min(fp.price for fp in sample_flight_prices)
        threshold = Decimal("700")

        should_send_alert = analyzer.should_alert(lowest_current_price, trend, threshold)

        # Price (650) is below threshold (700) and trend is down
        assert should_send_alert is True

        # Step 5: Send alert
        cheapest_fp = min(sample_flight_prices, key=lambda fp: fp.price)

        with patch('smtplib.SMTP') as mock_smtp:
            mock_server = Mock()
            mock_smtp.return_value.__enter__return_value = mock_server

            alert_message = (
                f"Found flight from 北京 to 上海 for ¥{lowest_current_price}, "
                f"below your threshold of ¥{threshold}."
            )

            result = await notifier.send_alert(cheapest_fp, trend, alert_message)

            assert result is True

    @pytest.mark.asyncio
    async def test_complete_workflow_without_alert(
        self,
        repository: SQLAlchemyRepository,
        analyzer: RuleBasedAnalyzer,
        notifier: EmailNotifier,
        sample_flight_prices: list,
    ):
        """Test workflow when alert conditions are not met."""
        # Step 1: Save flight prices
        for fp in sample_flight_prices:
            repository.save_price(fp)

        # Step 2: Add historical data with stable prices
        base_date = date.today() + timedelta(days=7)
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

            # Stable prices around 700
            price_value = 700 + (i % 2) * 20

            fp = FlightPrice(
                flight_info=flight_info,
                price=Decimal(str(price_value)),
                currency="CNY",
                seat_class="经济舱",
                available_seats=15,
                scraped_at=datetime.now(timezone.utc) - timedelta(days=10 - i),
                source="ctrip",
            )
            repository.save_price(fp)

        # Step 3: Analyze
        historical_prices = repository.get_history(
            departure_city="北京",
            arrival_city="上海",
            days=30,
        )

        trend = analyzer.predict_trend(historical_prices, base_date)

        # Trend should be stable
        assert trend.direction == "stable"

        # Step 4: Check alert conditions
        lowest_current_price = min(fp.price for fp in sample_flight_prices)
        threshold = Decimal("600")  # Threshold lower than current price

        should_send_alert = analyzer.should_alert(lowest_current_price, trend, threshold)

        # Price (650) is above threshold (600), so no alert
        assert should_send_alert is False

    def test_data_persistence(
        self,
        repository: SQLAlchemyRepository,
        sample_flight_prices: list,
    ):
        """Test that data is correctly persisted in database."""
        # Save prices
        for fp in sample_flight_prices:
            repository.save_price(fp)

        # Query latest prices
        base_date = sample_flight_prices[0].flight_info.departure_date
        latest_prices = repository.get_latest_prices(
            departure_city="北京",
            arrival_city="上海",
            departure_date=base_date,
        )

        # Should have 4 unique flights (2 flights × 2 classes)
        assert len(latest_prices) == 4

        # Verify prices are sorted
        prices = [fp.price for fp in latest_prices]
        assert prices == sorted(prices)

    def test_historical_data_query(
        self,
        repository: SQLAlchemyRepository,
    ):
        """Test historical data query over time."""
        base_date = date.today() + timedelta(days=7)

        # Create prices over multiple days
        for day_offset in range(5):
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
                scraped_at=datetime.now(timezone.utc) - timedelta(days=day_offset),
                source="ctrip",
            )
            repository.save_price(fp)

        # Query last 3 days
        history = repository.get_history(
            departure_city="北京",
            arrival_city="上海",
            days=3,
        )

        # Should have 3 records (day 0, 1, 2)
        assert len(history) == 3

        # Query last 10 days
        history_all = repository.get_history(
            departure_city="北京",
            arrival_city="上海",
            days=10,
        )

        # Should have all 5 records
        assert len(history_all) == 5

    def test_multiple_routes(
        self,
        repository: SQLAlchemyRepository,
    ):
        """Test handling multiple routes."""
        base_date = date.today() + timedelta(days=7)

        # Create flights for route 1: 北京 → 上海
        for i in range(3):
            flight_info = FlightInfo(
                flight_no=f"CA{i}234",
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
            repository.save_price(fp)

        # Create flights for route 2: 上海 → 北京
        for i in range(2):
            flight_info = FlightInfo(
                flight_no=f"MU{i}567",
                airline="东方航空",
                departure_city="上海",
                arrival_city="北京",
                departure_time="14:00",
                arrival_time="16:30",
                departure_date=base_date,
                direction=FlightDirection.DEPARTURE,
            )

            fp = FlightPrice(
                flight_info=flight_info,
                price=Decimal("750.00"),
                currency="CNY",
                seat_class="经济舱",
                available_seats=15,
                scraped_at=datetime.now(timezone.utc),
                source="ctrip",
            )
            repository.save_price(fp)

        # Query route 1
        route1_prices = repository.get_latest_prices(
            departure_city="北京",
            arrival_city="上海",
            departure_date=base_date,
        )
        assert len(route1_prices) == 3

        # Query route 2
        route2_prices = repository.get_latest_prices(
            departure_city="上海",
            arrival_city="北京",
            departure_date=base_date,
        )
        assert len(route2_prices) == 2


def test_cli_search_workflow():
    """Test the CLI search command workflow (without actual scraping)."""
    # This test simulates the CLI search workflow
    # In a real test, you would invoke the CLI command

    # Step 1: Initialize database
    engine, SessionLocal = init_db("sqlite:///:memory:")
    session = SessionLocal()
    repository = SQLAlchemyRepository(session)
    analyzer = RuleBasedAnalyzer()

    # Step 2: Simulate scraped data
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
        price=Decimal("680.00"),
        currency="CNY",
        seat_class="经济舱",
        available_seats=15,
        scraped_at=datetime.now(timezone.utc),
        source="ctrip",
    )

    # Step 3: Save data
    repository.save_price(fp)

    # Step 4: Analyze
    history = repository.get_history(
        departure_city="北京",
        arrival_city="上海",
        days=30,
    )

    if history:
        trend = analyzer.predict_trend(history, base_date)
        assert trend is not None

    session.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])