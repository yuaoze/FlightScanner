"""Integration test for complete data pipeline.

This test verifies the complete data flow:
1. Scrape flight data (or use mock data)
2. Parse and validate the data
3. Store in database
4. Retrieve and verify

Usage:
    pytest tests/test_integration.py -v
"""

import sys
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from sqlalchemy.orm import Session
from flightscanner.interfaces import (
    FlightDirection,
    FlightInfo,
    FlightPrice,
    SearchParams,
)
from flightscanner.models import Flight, PriceHistory, init_db


class MockScraper:
    """Mock scraper for testing purposes.

    This simulates a real scraper by returning predefined flight data.
    In real implementation, this would be replaced with actual scraper.
    """

    def create_mock_flight_prices(self) -> List[FlightPrice]:
        """Create mock flight price data for testing.

        Returns:
            List of mock FlightPrice objects.
        """
        # Create mock flight info for outbound flight
        outbound_flight = FlightInfo(
            flight_no="CA1234",
            airline="中国国航",
            departure_city="北京",
            arrival_city="上海",
            departure_time="08:00",
            arrival_time="10:30",
            departure_date=date.today() + timedelta(days=7),
            direction=FlightDirection.DEPARTURE,
        )

        # Create mock flight info for return flight
        return_flight = FlightInfo(
            flight_no="CA5678",
            airline="中国国航",
            departure_city="上海",
            arrival_city="北京",
            departure_time="18:00",
            arrival_time="20:30",
            departure_date=date.today() + timedelta(days=14),
            direction=FlightDirection.RETURN,
        )

        # Create mock prices
        return [
            FlightPrice(
                flight_info=outbound_flight,
                price=Decimal("680.00"),
                currency="CNY",
                seat_class="经济舱",
                available_seats=15,
                scraped_at=datetime.now(timezone.utc),
                source="ctrip",
            ),
            FlightPrice(
                flight_info=outbound_flight,
                price=Decimal("1280.00"),
                currency="CNY",
                seat_class="商务舱",
                available_seats=8,
                scraped_at=datetime.now(timezone.utc),
                source="ctrip",
            ),
            FlightPrice(
                flight_info=return_flight,
                price=Decimal("720.00"),
                currency="CNY",
                seat_class="经济舱",
                available_seats=20,
                scraped_at=datetime.now(timezone.utc),
                source="ctrip",
            ),
            FlightPrice(
                flight_info=return_flight,
                price=Decimal("1350.00"),
                currency="CNY",
                seat_class="商务舱",
                available_seats=5,
                scraped_at=datetime.now(timezone.utc),
                source="ctrip",
            ),
        ]


class DataPipeline:
    """Data pipeline for processing and storing flight data."""

    def __init__(self, session: Session):
        """Initialize pipeline with database session.

        Args:
            session: SQLAlchemy session for database operations.
        """
        self.session = session

    def save_flight_price(self, flight_price: FlightPrice) -> int:
        """Save a flight price to database.

        Args:
            flight_price: Flight price data to save.

        Returns:
            ID of the created price history record.
        """
        # Check if flight already exists
        flight = (
            self.session.query(Flight)
            .filter_by(
                flight_no=flight_price.flight_info.flight_no,
                departure_date=flight_price.flight_info.departure_date,
                departure_city=flight_price.flight_info.departure_city,
                arrival_city=flight_price.flight_info.arrival_city,
                direction=flight_price.flight_info.direction.value,
            )
            .first()
        )

        # Create flight if not exists
        if not flight:
            flight = Flight(
                flight_no=flight_price.flight_info.flight_no,
                airline=flight_price.flight_info.airline,
                departure_city=flight_price.flight_info.departure_city,
                arrival_city=flight_price.flight_info.arrival_city,
                departure_time=flight_price.flight_info.departure_time,
                arrival_time=flight_price.flight_info.arrival_time,
                departure_date=flight_price.flight_info.departure_date,
                direction=flight_price.flight_info.direction.value,
            )
            self.session.add(flight)
            self.session.flush()  # Flush to get the ID

        # Create price history
        price_history = PriceHistory(
            flight_id=flight.id,
            price=flight_price.price,
            currency=flight_price.currency,
            seat_class=flight_price.seat_class,
            available_seats=flight_price.available_seats,
            source=flight_price.source,
            scraped_at=flight_price.scraped_at,
        )
        self.session.add(price_history)
        self.session.commit()

        return price_history.id

    def get_flight_prices_by_route(
        self,
        departure_city: str,
        arrival_city: str,
        departure_date: date,
    ) -> List[dict]:
        """Get all prices for a specific route and date.

        Args:
            departure_city: Departure city.
            arrival_city: Arrival city.
            departure_date: Departure date.

        Returns:
            List of flight price dictionaries.
        """
        results = (
            self.session.query(Flight, PriceHistory)
            .join(PriceHistory, Flight.id == PriceHistory.flight_id)
            .filter(
                Flight.departure_city == departure_city,
                Flight.arrival_city == arrival_city,
                Flight.departure_date == departure_date,
            )
            .order_by(PriceHistory.price)
            .all()
        )

        return [
            {
                "flight_no": flight.flight_no,
                "airline": flight.airline,
                "departure_time": flight.departure_time,
                "arrival_time": flight.arrival_time,
                "price": float(price.price),
                "seat_class": price.seat_class,
                "source": price.source,
            }
            for flight, price in results
        ]


@pytest.fixture
def db_session():
    """Create a database session for testing."""
    engine, SessionLocal = init_db("sqlite:///:memory:")
    session = SessionLocal()
    yield session
    session.close()


class TestDataPipeline:
    """Test cases for data pipeline."""

    def test_mock_scraper_returns_data(self):
        """Test that mock scraper returns valid data."""
        scraper = MockScraper()
        prices = scraper.create_mock_flight_prices()

        assert len(prices) == 4
        assert all(isinstance(p, FlightPrice) for p in prices)

        # Verify first price
        first_price = prices[0]
        assert first_price.flight_info.flight_no == "CA1234"
        assert first_price.flight_info.departure_city == "北京"
        assert first_price.flight_info.arrival_city == "上海"
        assert first_price.price == Decimal("680.00")

    def test_save_and_retrieve_flight_prices(self, db_session):
        """Test saving and retrieving flight prices."""
        # Create mock data
        scraper = MockScraper()
        prices = scraper.create_mock_flight_prices()

        # Save to database
        pipeline = DataPipeline(db_session)
        price_ids = []

        for price in prices:
            price_id = pipeline.save_flight_price(price)
            price_ids.append(price_id)
            assert price_id > 0

        # Verify data was saved
        assert len(price_ids) == 4

        # Query and verify
        saved_prices = pipeline.get_flight_prices_by_route(
            departure_city="北京",
            arrival_city="上海",
            departure_date=date.today() + timedelta(days=7),
        )

        assert len(saved_prices) == 2  # 经济舱 and 商务舱

        # Verify prices are sorted
        assert saved_prices[0]["price"] <= saved_prices[1]["price"]

        # Verify flight info
        assert saved_prices[0]["flight_no"] == "CA1234"
        assert saved_prices[0]["airline"] == "中国国航"

    def test_duplicate_flight_handling(self, db_session):
        """Test that duplicate flights are handled correctly."""
        scraper = MockScraper()
        prices = scraper.create_mock_flight_prices()
        pipeline = DataPipeline(db_session)

        # Save same price twice
        pipeline.save_flight_price(prices[0])
        pipeline.save_flight_price(prices[0])

        # Should have two price records but only one flight
        flights = db_session.query(Flight).all()
        assert len(flights) == 1

        price_histories = db_session.query(PriceHistory).all()
        assert len(price_histories) == 2

    def test_price_history_tracking(self, db_session):
        """Test that price history is tracked over time."""
        scraper = MockScraper()
        pipeline = DataPipeline(db_session)

        # Create price at time T1
        price_t1 = scraper.create_mock_flight_prices()[0]
        pipeline.save_flight_price(price_t1)

        # Create same flight with different price at time T2
        from time import sleep

        sleep(0.01)  # Small delay to ensure different timestamp

        price_t2 = FlightPrice(
            flight_info=price_t1.flight_info,
            price=Decimal("750.00"),  # Price increased
            currency="CNY",
            seat_class="经济舱",
            available_seats=10,
            scraped_at=datetime.utcnow(),
            source="ctrip",
        )
        pipeline.save_flight_price(price_t2)

        # Verify we have two price records
        price_histories = (
            db_session.query(PriceHistory)
            .order_by(PriceHistory.scraped_at)
            .all()
        )

        assert len(price_histories) == 2
        assert price_histories[0].price == Decimal("680.00")
        assert price_histories[1].price == Decimal("750.00")


def test_complete_pipeline(db_session):
    """Integration test for complete pipeline.

    This is the main verification test that checks:
    1. Mock scraper can generate data
    2. Data can be saved to database
    3. Data can be retrieved correctly
    4. Relationships are maintained
    """
    # Step 1: Generate mock data
    scraper = MockScraper()
    prices = scraper.create_mock_flight_prices()

    assert len(prices) > 0, "Mock scraper should return data"

    # Step 2: Save to database
    pipeline = DataPipeline(db_session)
    for price in prices:
        pipeline.save_flight_price(price)

    # Step 3: Verify database has records
    flight_count = db_session.query(Flight).count()
    price_count = db_session.query(PriceHistory).count()

    assert flight_count == 2, "Should have 2 flights (outbound + return)"
    assert price_count == 4, "Should have 4 price records"

    # Step 4: Verify relationships
    flights = db_session.query(Flight).all()
    for flight in flights:
        assert len(flight.price_histories) > 0, "Each flight should have price history"

    # Step 5: Verify query functionality
    outbound_prices = pipeline.get_flight_prices_by_route(
        departure_city="北京",
        arrival_city="上海",
        departure_date=date.today() + timedelta(days=7),
    )

    assert len(outbound_prices) > 0, "Should find outbound flights"

    return_prices = pipeline.get_flight_prices_by_route(
        departure_city="上海",
        arrival_city="北京",
        departure_date=date.today() + timedelta(days=14),
    )

    assert len(return_prices) > 0, "Should find return flights"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
