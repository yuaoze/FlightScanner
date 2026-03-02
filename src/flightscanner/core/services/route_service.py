"""Route service for managing monitored routes.

This module provides business logic for route management operations.
"""

from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import func, and_
from sqlalchemy.orm import Session
from sqlalchemy.sql import literal_column

from flightscanner.models.database import Route, PriceHistory, Flight
from flightscanner.interfaces import FlightPrice


@dataclass
class RouteWithLatestPrice:
    """Data class representing a route with its latest price information.

    Attributes:
        id: Route ID.
        origin: Origin city name.
        destination: Destination city name.
        target_date: Target travel date.
        target_price: Target price threshold.
        scrape_interval: Scrape interval in hours.
        is_active: Whether monitoring is active.
        created_at: Route creation timestamp.
        latest_price: Latest scraped price (if any).
        latest_scraped_at: Timestamp of latest price scrape (if any).
        price_count: Number of price records for this route.
    """
    id: int
    origin: str
    destination: str
    target_date: date
    target_price: Decimal
    scrape_interval: int
    is_active: bool
    created_at: datetime
    latest_price: Optional[Decimal]
    latest_scraped_at: Optional[datetime]
    price_count: int


class RouteService:
    """Service for managing route monitoring operations.

    This service provides methods for CRUD operations on routes and
    related price history queries.
    """

    def __init__(self, session: Session):
        """Initialize the service with a database session.

        Args:
            session: SQLAlchemy session for database operations.
        """
        self.session = session

    def add_route(
        self,
        origin: str,
        destination: str,
        target_date: date,
        target_price: Decimal,
        scrape_interval: int = 6
    ) -> Route:
        """Add a new route to monitor.

        Args:
            origin: Origin city name.
            destination: Destination city name.
            target_date: Target travel date.
            target_price: Target price threshold for alerts.
            scrape_interval: Scrape interval in hours (default 6).

        Returns:
            The created Route object.

        Raises:
            ValueError: If a route with the same parameters already exists.
        """
        # Check for existing route with same parameters
        existing = self.session.query(Route).filter(
            and_(
                Route.origin == origin,
                Route.destination == destination,
                Route.target_date == target_date,
            )
        ).first()

        if existing:
            raise ValueError(
                f"Route already exists: {origin} -> {destination} on {target_date}"
            )

        route = Route(
            origin=origin,
            destination=destination,
            target_date=target_date,
            target_price=target_price,
            scrape_interval=scrape_interval,
            is_active=1,
        )

        self.session.add(route)
        self.session.commit()
        self.session.refresh(route)

        return route

    def get_all_routes(self) -> List[RouteWithLatestPrice]:
        """Get all routes with their latest price information.

        Returns:
            List of RouteWithLatestPrice objects containing route data
            and aggregated price information.
        """
        # Subquery to get latest price info per route
        latest_price_subq = (
            self.session.query(
                PriceHistory.route_id,
                func.max(PriceHistory.scraped_at).label("latest_scraped_at"),
            )
            .filter(PriceHistory.route_id.isnot(None))
            .group_by(PriceHistory.route_id)
            .subquery()
        )

        # Subquery to get price count per route
        price_count_subq = (
            self.session.query(
                PriceHistory.route_id,
                func.count(PriceHistory.id).label("price_count"),
            )
            .filter(PriceHistory.route_id.isnot(None))
            .group_by(PriceHistory.route_id)
            .subquery()
        )

        # Main query joining route with latest price
        results = (
            self.session.query(
                Route.id,
                Route.origin,
                Route.destination,
                Route.target_date,
                Route.target_price,
                Route.scrape_interval,
                Route.is_active,
                Route.created_at,
                PriceHistory.price.label("latest_price"),
                latest_price_subq.c.latest_scraped_at,
                func.coalesce(price_count_subq.c.price_count, 0).label("price_count"),
            )
            .outerjoin(
                latest_price_subq,
                Route.id == latest_price_subq.c.route_id,
            )
            .outerjoin(
                PriceHistory,
                and_(
                    Route.id == PriceHistory.route_id,
                    PriceHistory.scraped_at == latest_price_subq.c.latest_scraped_at,
                ),
            )
            .outerjoin(
                price_count_subq,
                Route.id == price_count_subq.c.route_id,
            )
            .order_by(Route.created_at.desc())
            .all()
        )

        # Convert to dataclass objects
        routes = []
        for row in results:
            routes.append(
                RouteWithLatestPrice(
                    id=row.id,
                    origin=row.origin,
                    destination=row.destination,
                    target_date=row.target_date,
                    target_price=row.target_price,
                    scrape_interval=row.scrape_interval,
                    is_active=bool(row.is_active),
                    created_at=row.created_at,
                    latest_price=row.latest_price,
                    latest_scraped_at=row.latest_scraped_at,
                    price_count=row.price_count or 0,
                )
            )

        return routes

    def get_active_routes(self) -> List[Route]:
        """Get all active routes that should be monitored.

        Returns routes where:
        - is_active = 1
        - target_date >= today

        Returns:
            List of active Route objects.
        """
        today = date.today()

        routes = (
            self.session.query(Route)
            .filter(
                and_(
                    Route.is_active == 1,
                    Route.target_date >= today,
                )
            )
            .order_by(Route.target_date)
            .all()
        )

        return routes

    def get_route_by_id(self, route_id: int) -> Optional[Route]:
        """Get a route by its ID.

        Args:
            route_id: The route ID.

        Returns:
            Route object if found, None otherwise.
        """
        return self.session.query(Route).filter(Route.id == route_id).first()

    def delete_route(self, route_id: int) -> bool:
        """Delete a route by its ID.

        Args:
            route_id: The route ID to delete.

        Returns:
            True if route was deleted, False if not found.
        """
        route = self.session.query(Route).filter(Route.id == route_id).first()

        if not route:
            return False

        self.session.delete(route)
        self.session.commit()

        return True

    def toggle_route_status(self, route_id: int) -> Optional[Route]:
        """Toggle a route's active status.

        Args:
            route_id: The route ID to toggle.

        Returns:
            Updated Route object if found, None otherwise.
        """
        route = self.session.query(Route).filter(Route.id == route_id).first()

        if not route:
            return None

        route.is_active = 0 if route.is_active == 1 else 1
        self.session.commit()
        self.session.refresh(route)

        return route

    def update_route_interval(self, route_id: int, new_interval: int) -> Optional[Route]:
        """Update a route's scrape interval.

        Args:
            route_id: The route ID to update.
            new_interval: New scrape interval in hours.

        Returns:
            Updated Route object if found, None otherwise.
        """
        route = self.session.query(Route).filter(Route.id == route_id).first()

        if not route:
            return None

        route.scrape_interval = new_interval
        self.session.commit()
        self.session.refresh(route)

        return route

    def get_route_price_history(
        self,
        route_id: int,
        days: int = 30
    ) -> List[FlightPrice]:
        """Get price history for a specific route.

        Args:
            route_id: The route ID.
            days: Number of days to look back (default 30).

        Returns:
            List of FlightPrice objects with historical data.
        """
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(days=days)

        results = (
            self.session.query(PriceHistory, Flight)
            .join(Flight, PriceHistory.flight_id == Flight.id)
            .filter(
                and_(
                    PriceHistory.route_id == route_id,
                    PriceHistory.scraped_at >= cutoff,
                )
            )
            .order_by(PriceHistory.scraped_at.desc())
            .all()
        )

        from flightscanner.interfaces import FlightInfo, FlightDirection

        flight_prices = []
        for price_history, flight in results:
            flight_info = FlightInfo(
                flight_no=flight.flight_no,
                airline=flight.airline,
                departure_city=flight.departure_city,
                arrival_city=flight.arrival_city,
                departure_time=flight.departure_time,
                arrival_time=flight.arrival_time,
                departure_date=flight.departure_date,
                direction=FlightDirection(flight.direction),
            )

            flight_price = FlightPrice(
                flight_info=flight_info,
                price=Decimal(str(price_history.price)),
                currency=price_history.currency,
                seat_class=price_history.seat_class,
                available_seats=price_history.available_seats,
                scraped_at=price_history.scraped_at,
                source=price_history.source,
            )

            flight_prices.append(flight_price)

        return flight_prices

    def save_price_for_route(
        self,
        route_id: int,
        flight_price: FlightPrice
    ) -> int:
        """Save a price snapshot linked to a route.

        This method creates or finds the Flight record and creates
        a PriceHistory record linked to both the flight and route.

        Args:
            route_id: The route ID to link the price to.
            flight_price: Flight price data to save.

        Returns:
            The ID of the created PriceHistory record.
        """
        from flightscanner.repositories.sqlalchemy_repo import SQLAlchemyRepository

        # Use the repository to save the flight and get the flight ID
        repo = SQLAlchemyRepository(self.session)

        # Find or create flight record
        flight = (
            self.session.query(Flight)
            .filter(
                and_(
                    Flight.flight_no == flight_price.flight_info.flight_no,
                    Flight.departure_date == flight_price.flight_info.departure_date,
                    Flight.departure_city == flight_price.flight_info.departure_city,
                    Flight.arrival_city == flight_price.flight_info.arrival_city,
                    Flight.direction == flight_price.flight_info.direction.value,
                )
            )
            .first()
        )

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
            self.session.flush()

        # Create price history with route_id
        price_history = PriceHistory(
            flight_id=flight.id,
            route_id=route_id,
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
