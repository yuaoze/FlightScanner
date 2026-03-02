"""SQLAlchemy-based implementation of DataRepository interface.

This module provides a concrete implementation of the DataRepository interface
using SQLAlchemy ORM for database operations.
"""

from datetime import date, datetime, timedelta, timezone
from typing import List

from sqlalchemy.orm import Session

from flightscanner.interfaces import DataRepository, FlightPrice
from flightscanner.models import Flight, PriceHistory


class SQLAlchemyRepository(DataRepository):
    """SQLAlchemy-based repository for flight price data persistence.

    This class implements the DataRepository interface using SQLAlchemy ORM
    to store and retrieve flight information and price history.

    Attributes:
        session: SQLAlchemy session for database operations.
    """

    def __init__(self, session: Session):
        """Initialize the repository with a database session.

        Args:
            session: SQLAlchemy session for database operations.
        """
        self.session = session

    def save_price(self, flight_price: FlightPrice) -> int:
        """Save a price snapshot to the database.

        This method:
        1. Checks if the flight already exists in the database
        2. Creates a new Flight record if it doesn't exist
        3. Creates a new PriceHistory record linked to the flight
        4. Commits the transaction and returns the price history ID

        Args:
            flight_price: Flight price data to save.

        Returns:
            The ID of the saved price history record.

        Raises:
            Exception: When save operation fails.
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

        # Create price history record
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

    def get_history(
        self, departure_city: str, arrival_city: str, days: int = 30
    ) -> List[FlightPrice]:
        """Get historical price data for a route.

        Retrieves all price records for flights between the specified cities
        within the last N days.

        Args:
            departure_city: Departure city name.
            arrival_city: Arrival city name.
            days: Number of days to look back. Defaults to 30.

        Returns:
            List of historical flight prices, ordered by scraped_at descending.
        """
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

        results = (
            self.session.query(Flight, PriceHistory)
            .join(PriceHistory, Flight.id == PriceHistory.flight_id)
            .filter(
                Flight.departure_city == departure_city,
                Flight.arrival_city == arrival_city,
                PriceHistory.scraped_at >= cutoff_date,
            )
            .order_by(PriceHistory.scraped_at.desc())
            .all()
        )

        return [self._convert_to_flight_price(flight, price) for flight, price in results]

    def get_latest_prices(
        self, departure_city: str, arrival_city: str, departure_date: date
    ) -> List[FlightPrice]:
        """Get the latest prices for a specific route and date.

        Retrieves the most recent price records for all flights on the
        specified route and departure date.

        Args:
            departure_city: Departure city name.
            arrival_city: Arrival city name.
            departure_date: Departure date.

        Returns:
            List of latest flight prices, ordered by price ascending.
        """
        # Subquery to get the latest scraped_at for each flight
        from sqlalchemy import func

        latest_scraped_subquery = (
            self.session.query(
                PriceHistory.flight_id,
                func.max(PriceHistory.scraped_at).label("latest_scraped_at"),
            )
            .join(Flight, PriceHistory.flight_id == Flight.id)
            .filter(
                Flight.departure_city == departure_city,
                Flight.arrival_city == arrival_city,
                Flight.departure_date == departure_date,
            )
            .group_by(PriceHistory.flight_id)
            .subquery()
        )

        # Query to get the actual price records
        results = (
            self.session.query(Flight, PriceHistory)
            .join(PriceHistory, Flight.id == PriceHistory.flight_id)
            .join(
                latest_scraped_subquery,
                (PriceHistory.flight_id == latest_scraped_subquery.c.flight_id)
                & (PriceHistory.scraped_at == latest_scraped_subquery.c.latest_scraped_at),
            )
            .order_by(PriceHistory.price.asc())
            .all()
        )

        return [self._convert_to_flight_price(flight, price) for flight, price in results]

    def _convert_to_flight_price(self, flight: Flight, price: PriceHistory) -> FlightPrice:
        """Convert database models to FlightPrice dataclass.

        Args:
            flight: Flight database model.
            price: PriceHistory database model.

        Returns:
            FlightPrice dataclass instance.
        """
        from flightscanner.interfaces import FlightDirection, FlightInfo

        return FlightPrice(
            flight_info=FlightInfo(
                flight_no=flight.flight_no,
                airline=flight.airline,
                departure_city=flight.departure_city,
                arrival_city=flight.arrival_city,
                departure_time=flight.departure_time,
                arrival_time=flight.arrival_time,
                departure_date=flight.departure_date,
                direction=FlightDirection(flight.direction),
            ),
            price=price.price_decimal,
            currency=price.currency,
            seat_class=price.seat_class,
            available_seats=price.available_seats,
            scraped_at=price.scraped_at,
            source=price.source,
        )
