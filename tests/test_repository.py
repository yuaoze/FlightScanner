"""Unit tests for SQLAlchemyRepository."""

import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from sqlalchemy.orm import Session
from flightscanner.interfaces import FlightDirection, FlightInfo, FlightPrice
from flightscanner.models import Flight, PriceHistory, init_db
from flightscanner.repositories import SQLAlchemyRepository


@pytest.fixture
def db_session():
    """Create an in-memory database session for testing."""
    engine, SessionLocal = init_db("sqlite:///:memory:")
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def repository(db_session: Session):
    """Create a repository instance."""
    return SQLAlchemyRepository(db_session)


@pytest.fixture
def sample_flight_price():
    """Create a sample flight price for testing."""
    flight_info = FlightInfo(
        flight_no="CA1234",
        airline="中国国航",
        departure_city="北京",
        arrival_city="上海",
        departure_time="08:00",
        arrival_time="10:30",
        departure_date=date.today() + timedelta(days=7),
        direction=FlightDirection.DEPARTURE,
    )

    return FlightPrice(
        flight_info=flight_info,
        price=Decimal("680.00"),
        currency="CNY",
        seat_class="经济舱",
        available_seats=15,
        scraped_at=datetime.now(timezone.utc),
        source="ctrip",
    )


class TestSQLAlchemyRepository:
    """Test cases for SQLAlchemyRepository."""

    def test_save_price_creates_new_flight(self, repository: SQLAlchemyRepository, sample_flight_price: FlightPrice):
        """Test that saving a price creates a new flight record."""
        # Save price
        price_id = repository.save_price(sample_flight_price)

        # Verify price ID is returned
        assert price_id > 0

        # Verify flight was created
        flight = repository.session.query(Flight).filter_by(
            flight_no=sample_flight_price.flight_info.flight_no
        ).first()
        assert flight is not None
        assert flight.airline == sample_flight_price.flight_info.airline

        # Verify price history was created
        price_history = repository.session.query(PriceHistory).filter_by(
            id=price_id
        ).first()
        assert price_history is not None
        assert price_history.price == sample_flight_price.price

    def test_save_price_reuses_existing_flight(self, repository: SQLAlchemyRepository, sample_flight_price: FlightPrice):
        """Test that saving a price for an existing flight reuses the flight record."""
        # Save first price
        price_id_1 = repository.save_price(sample_flight_price)

        # Modify price and save again
        sample_flight_price.price = Decimal("750.00")
        sample_flight_price.scraped_at = datetime.now(timezone.utc)
        price_id_2 = repository.save_price(sample_flight_price)

        # Verify two different price history records
        assert price_id_1 != price_id_2

        # Verify only one flight record exists
        flights = repository.session.query(Flight).all()
        assert len(flights) == 1

        # Verify two price history records
        prices = repository.session.query(PriceHistory).all()
        assert len(prices) == 2

    def test_get_history_returns_prices(self, repository: SQLAlchemyRepository, sample_flight_price: FlightPrice):
        """Test that get_history returns historical prices."""
        # Save multiple prices
        repository.save_price(sample_flight_price)

        # Modify and save another price
        sample_flight_price.price = Decimal("720.00")
        sample_flight_price.scraped_at = datetime.now(timezone.utc)
        repository.save_price(sample_flight_price)

        # Query history
        history = repository.get_history(
            departure_city=sample_flight_price.flight_info.departure_city,
            arrival_city=sample_flight_price.flight_info.arrival_city,
            days=30,
        )

        # Verify results
        assert len(history) == 2
        assert all(isinstance(fp, FlightPrice) for fp in history)
        # Should be sorted by scraped_at descending
        assert history[0].price == Decimal("720.00")
        assert history[1].price == Decimal("680.00")

    def test_get_history_filters_by_days(self, repository: SQLAlchemyRepository, sample_flight_price: FlightPrice):
        """Test that get_history filters by number of days."""
        # Save a current price
        repository.save_price(sample_flight_price)

        # Create an old price (beyond the days filter)
        old_price = FlightPrice(
            flight_info=sample_flight_price.flight_info,
            price=Decimal("600.00"),
            currency="CNY",
            seat_class="经济舱",
            available_seats=10,
            scraped_at=datetime.now(timezone.utc) - timedelta(days=31),
            source="ctrip",
        )
        repository.save_price(old_price)

        # Query last 30 days
        history = repository.get_history(
            departure_city=sample_flight_price.flight_info.departure_city,
            arrival_city=sample_flight_price.flight_info.arrival_city,
            days=30,
        )

        # Should only return the current price
        assert len(history) == 1
        assert history[0].price == Decimal("680.00")

    def test_get_latest_prices(self, repository: SQLAlchemyRepository, sample_flight_price: FlightPrice):
        """Test that get_latest_prices returns the most recent prices."""
        # Save multiple prices for the same flight at different times
        repository.save_price(sample_flight_price)

        sample_flight_price.price = Decimal("700.00")
        sample_flight_price.scraped_at = datetime.now(timezone.utc)
        repository.save_price(sample_flight_price)

        # Query latest prices
        latest = repository.get_latest_prices(
            departure_city=sample_flight_price.flight_info.departure_city,
            arrival_city=sample_flight_price.flight_info.arrival_city,
            departure_date=sample_flight_price.flight_info.departure_date,
        )

        # Should return one price (the latest)
        assert len(latest) == 1
        assert latest[0].price == Decimal("700.00")

    def test_get_latest_prices_multiple_flights(self, repository: SQLAlchemyRepository):
        """Test that get_latest_prices handles multiple flights."""
        # Create two different flights
        flight1 = FlightInfo(
            flight_no="CA1234",
            airline="中国国航",
            departure_city="北京",
            arrival_city="上海",
            departure_time="08:00",
            arrival_time="10:30",
            departure_date=date.today() + timedelta(days=7),
            direction=FlightDirection.DEPARTURE,
        )

        flight2 = FlightInfo(
            flight_no="MU5678",
            airline="东方航空",
            departure_city="北京",
            arrival_city="上海",
            departure_time="14:00",
            arrival_time="16:30",
            departure_date=date.today() + timedelta(days=7),
            direction=FlightDirection.DEPARTURE,
        )

        # Save prices for both flights
        price1 = FlightPrice(
            flight_info=flight1,
            price=Decimal("680.00"),
            currency="CNY",
            seat_class="经济舱",
            available_seats=15,
            scraped_at=datetime.now(timezone.utc),
            source="ctrip",
        )
        repository.save_price(price1)

        price2 = FlightPrice(
            flight_info=flight2,
            price=Decimal("650.00"),
            currency="CNY",
            seat_class="经济舱",
            available_seats=20,
            scraped_at=datetime.now(timezone.utc),
            source="ctrip",
        )
        repository.save_price(price2)

        # Query latest prices
        latest = repository.get_latest_prices(
            departure_city="北京",
            arrival_city="上海",
            departure_date=date.today() + timedelta(days=7),
        )

        # Should return two prices, sorted by price ascending
        assert len(latest) == 2
        assert latest[0].price == Decimal("650.00")  # Cheaper flight first
        assert latest[1].price == Decimal("680.00")

    def test_get_history_empty_result(self, repository: SQLAlchemyRepository):
        """Test that get_history returns empty list for non-existent route."""
        history = repository.get_history(
            departure_city="不存在",
            arrival_city="不存在",
            days=30,
        )
        assert history == []

    def test_get_latest_prices_empty_result(self, repository: SQLAlchemyRepository):
        """Test that get_latest_prices returns empty list for non-existent route."""
        latest = repository.get_latest_prices(
            departure_city="不存在",
            arrival_city="不存在",
            departure_date=date.today(),
        )
        assert latest == []
