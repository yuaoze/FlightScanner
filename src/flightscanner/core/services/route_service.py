"""Route service for managing monitored routes.

This module provides business logic for route management operations.
"""

from dataclasses import dataclass
from datetime import datetime, date, timezone
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from flightscanner.models.database import Route, PriceHistory, Flight
from flightscanner.interfaces import FlightPrice
from flightscanner.utils.city_codes import is_international_route


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
        return_date: Return date for round-trip flights (None for one-way).
        trip_type: "oneway" or "roundtrip".
        is_international: Whether this is an international flight.
        dep_airport_code: Departure airport IATA code filter (None = any airport).
        arr_airport_code: Arrival airport IATA code filter (None = any airport).
        dep_time_from: Departure time window start (HH:MM, None = no limit).
        dep_time_to: Departure time window end (HH:MM, None = no limit).
        arr_time_from: Arrival time window start (HH:MM, None = no limit).
        arr_time_to: Arrival time window end (HH:MM, None = no limit).
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
    return_date: Optional[date] = None
    trip_type: str = "oneway"
    is_international: bool = False
    dep_airport_code: Optional[str] = None
    arr_airport_code: Optional[str] = None
    dep_time_from: Optional[str] = None
    dep_time_to: Optional[str] = None
    arr_time_from: Optional[str] = None
    arr_time_to: Optional[str] = None
    last_notified_at: Optional[datetime] = None
    last_notified_price: Optional[Decimal] = None
    max_results: int = 20


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
        scrape_interval: int = 6,
        return_date: Optional[date] = None,
        trip_type: str = "oneway",
        is_international: Optional[bool] = None,
        dep_airport_code: Optional[str] = None,
        arr_airport_code: Optional[str] = None,
        dep_time_from: Optional[str] = None,
        dep_time_to: Optional[str] = None,
        arr_time_from: Optional[str] = None,
        arr_time_to: Optional[str] = None,
        max_results: int = 20,
    ) -> Route:
        """Add a new route to monitor.

        Args:
            origin: Origin city name.
            destination: Destination city name.
            target_date: Target travel date.
            target_price: Target price threshold for alerts.
            scrape_interval: Scrape interval in hours (default 6).
            return_date: Return date for round-trip flights (optional).
            trip_type: "oneway" or "roundtrip" (default "oneway").
            is_international: Whether this is an international route. If None,
                auto-inferred from city names via is_international_route().
            dep_airport_code: Departure airport IATA code filter (None = any airport).
            arr_airport_code: Arrival airport IATA code filter (None = any airport).
            dep_time_from: Departure time window start "HH:MM" (None = no limit).
            dep_time_to: Departure time window end "HH:MM" (None = no limit).
            arr_time_from: Arrival time window start "HH:MM" (None = no limit).
            arr_time_to: Arrival time window end "HH:MM" (None = no limit).

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

        # 自动推断是否国际航班
        if is_international is None:
            is_international = is_international_route(origin, destination)

        route = Route(
            origin=origin,
            destination=destination,
            target_date=target_date,
            target_price=target_price,
            scrape_interval=scrape_interval,
            is_active=1,
            return_date=return_date,
            trip_type=trip_type,
            is_international=int(is_international),
            dep_airport_code=dep_airport_code or None,
            arr_airport_code=arr_airport_code or None,
            dep_time_from=dep_time_from or None,
            dep_time_to=dep_time_to or None,
            arr_time_from=arr_time_from or None,
            arr_time_to=arr_time_to or None,
            max_results=max_results,
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
        # Subquery 1: per-route latest scraped_at (for display)
        latest_time_subq = (
            self.session.query(
                PriceHistory.route_id,
                func.max(PriceHistory.scraped_at).label("latest_scraped_at"),
            )
            .filter(PriceHistory.route_id.isnot(None))
            .group_by(PriceHistory.route_id)
            .subquery()
        )

        # Subquery 2: latest batch_id per (route_id, source) pair
        # Uses batch_id to group all records from a single scrape session,
        # avoiding the issue where multiple records at the same second would
        # have the same scraped_at timestamp but different prices
        latest_batch_per_source_subq = (
            self.session.query(
                PriceHistory.route_id,
                PriceHistory.source,
                func.max(PriceHistory.batch_id).label("latest_batch_id"),
            )
            .filter(
                and_(
                    PriceHistory.route_id.isnot(None),
                    PriceHistory.batch_id.isnot(None),
                )
            )
            .group_by(PriceHistory.route_id, PriceHistory.source)
            .subquery()
        )

        # Subquery 3: minimum price per source within their latest batch
        # This ensures we get the minimum price from all records in the latest
        # collection session for each platform, not just records at exact same timestamp
        min_price_per_source_subq = (
            self.session.query(
                PriceHistory.route_id,
                func.min(PriceHistory.price).label("source_min_price"),
            )
            .join(
                latest_batch_per_source_subq,
                and_(
                    PriceHistory.route_id == latest_batch_per_source_subq.c.route_id,
                    PriceHistory.source == latest_batch_per_source_subq.c.source,
                    PriceHistory.batch_id == latest_batch_per_source_subq.c.latest_batch_id,
                ),
            )
            .group_by(PriceHistory.route_id, PriceHistory.source)
            .subquery()
        )

        # Subquery 4: overall minimum across all sources (各平台最新采集的最低价的最小值)
        latest_price_subq = (
            self.session.query(
                min_price_per_source_subq.c.route_id,
                func.min(min_price_per_source_subq.c.source_min_price).label("latest_price"),
                latest_time_subq.c.latest_scraped_at,
            )
            .join(
                latest_time_subq,
                min_price_per_source_subq.c.route_id == latest_time_subq.c.route_id,
            )
            .group_by(min_price_per_source_subq.c.route_id)
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

        # Main query: one row per route, no PriceHistory join needed
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
                Route.return_date,
                Route.trip_type,
                Route.is_international,
                Route.dep_airport_code,
                Route.arr_airport_code,
                Route.dep_time_from,
                Route.dep_time_to,
                Route.arr_time_from,
                Route.arr_time_to,
                Route.last_notified_at,
                Route.last_notified_price,
                Route.max_results,
                latest_price_subq.c.latest_price,
                latest_price_subq.c.latest_scraped_at,
                func.coalesce(price_count_subq.c.price_count, 0).label("price_count"),
            )
            .outerjoin(
                latest_price_subq,
                Route.id == latest_price_subq.c.route_id,
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
                    return_date=row.return_date,
                    trip_type=row.trip_type or "oneway",
                    is_international=bool(row.is_international),
                    dep_airport_code=row.dep_airport_code,
                    arr_airport_code=row.arr_airport_code,
                    dep_time_from=row.dep_time_from,
                    dep_time_to=row.dep_time_to,
                    arr_time_from=row.arr_time_from,
                    arr_time_to=row.arr_time_to,
                    last_notified_at=row.last_notified_at,
                    last_notified_price=(
                        Decimal(str(row.last_notified_price))
                        if row.last_notified_price is not None
                        else None
                    ),
                    max_results=row.max_results if row.max_results is not None else 20,
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

        For round-trip routes each returned FlightPrice contains both
        ``flight_info`` (outbound leg) and ``return_flight_info`` (return leg).

        Args:
            route_id: The route ID.
            days: Number of days to look back (default 30).

        Returns:
            List of FlightPrice objects with historical data.
        """
        from datetime import timedelta
        from sqlalchemy.orm import aliased
        from flightscanner.interfaces import FlightInfo, FlightDirection

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # 使用 aliased 区分去程航班和回程航班两个 Flight 关联
        ReturnFlight = aliased(Flight, name="return_flight")

        results = (
            self.session.query(PriceHistory, Flight, ReturnFlight)
            .join(Flight, PriceHistory.flight_id == Flight.id)
            .outerjoin(ReturnFlight, PriceHistory.return_flight_id == ReturnFlight.id)
            .filter(
                and_(
                    PriceHistory.route_id == route_id,
                    PriceHistory.scraped_at >= cutoff,
                )
            )
            .order_by(PriceHistory.scraped_at.desc())
            .all()
        )

        flight_prices = []
        for price_history, flight, return_flight in results:
            flight_info = FlightInfo(
                flight_no=flight.flight_no,
                airline=flight.airline,
                departure_city=flight.departure_city,
                arrival_city=flight.arrival_city,
                departure_time=flight.departure_time,
                arrival_time=flight.arrival_time,
                departure_date=flight.departure_date,
                direction=FlightDirection(flight.direction),
                departure_airport=flight.departure_airport,
                arrival_airport=flight.arrival_airport,
                departure_airport_code=flight.departure_airport_code,
                arrival_airport_code=flight.arrival_airport_code,
                arrival_date=flight.arrival_date,
            )

            # 往返程：组装回程航班信息
            return_flight_info = None
            if return_flight is not None:
                return_flight_info = FlightInfo(
                    flight_no=return_flight.flight_no,
                    airline=return_flight.airline,
                    departure_city=return_flight.departure_city,
                    arrival_city=return_flight.arrival_city,
                    departure_time=return_flight.departure_time,
                    arrival_time=return_flight.arrival_time,
                    departure_date=return_flight.departure_date,
                    direction=FlightDirection(return_flight.direction),
                    departure_airport=return_flight.departure_airport,
                    arrival_airport=return_flight.arrival_airport,
                    departure_airport_code=return_flight.departure_airport_code,
                    arrival_airport_code=return_flight.arrival_airport_code,
                    arrival_date=return_flight.arrival_date,
                )

            flight_price = FlightPrice(
                flight_info=flight_info,
                price=Decimal(str(price_history.price)),
                currency=price_history.currency,
                seat_class=price_history.seat_class,
                available_seats=price_history.available_seats,
                scraped_at=price_history.scraped_at,
                source=price_history.source,
                return_flight_info=return_flight_info,
                batch_id=price_history.batch_id,
            )

            flight_prices.append(flight_price)

        return flight_prices

    def _find_or_create_flight(self, flight_info: "FlightInfo") -> Flight:
        """查找或创建 Flight 记录（内部辅助方法）。

        Args:
            flight_info: 航班基本信息。

        Returns:
            对应的 Flight ORM 对象（已 flush 到 session）。
        """
        from flightscanner.interfaces import FlightInfo  # 避免循环导入

        flight = (
            self.session.query(Flight)
            .filter(
                and_(
                    Flight.flight_no == flight_info.flight_no,
                    Flight.departure_date == flight_info.departure_date,
                    Flight.departure_city == flight_info.departure_city,
                    Flight.arrival_city == flight_info.arrival_city,
                    Flight.direction == flight_info.direction.value,
                )
            )
            .first()
        )

        if not flight:
            flight = Flight(
                flight_no=flight_info.flight_no,
                airline=flight_info.airline,
                departure_city=flight_info.departure_city,
                arrival_city=flight_info.arrival_city,
                departure_time=flight_info.departure_time,
                arrival_time=flight_info.arrival_time,
                departure_date=flight_info.departure_date,
                direction=flight_info.direction.value,
                departure_airport=flight_info.departure_airport,
                arrival_airport=flight_info.arrival_airport,
                departure_airport_code=flight_info.departure_airport_code,
                arrival_airport_code=flight_info.arrival_airport_code,
                arrival_date=flight_info.arrival_date,
            )
            self.session.add(flight)
            self.session.flush()

        return flight

    def save_price_for_route(
        self,
        route_id: int,
        flight_price: FlightPrice
    ) -> int:
        """Save a price snapshot linked to a route.

        For round-trip combined records (``flight_price.return_flight_info``
        is not None) both the outbound and return Flight records are created
        and ``PriceHistory.return_flight_id`` is set.

        Args:
            route_id: The route ID to link the price to.
            flight_price: Flight price data to save.

        Returns:
            The ID of the created PriceHistory record.
        """
        # ── 去程（或单程）航班 ────────────────────────────────────────────
        flight = self._find_or_create_flight(flight_price.flight_info)

        # ── 往返程：回程航班 ──────────────────────────────────────────────
        return_flight_id: Optional[int] = None
        if flight_price.return_flight_info is not None:
            return_flight = self._find_or_create_flight(flight_price.return_flight_info)
            return_flight_id = return_flight.id

        # ── 创建价格快照 ──────────────────────────────────────────────────
        price_history = PriceHistory(
            flight_id=flight.id,
            return_flight_id=return_flight_id,
            route_id=route_id,
            price=flight_price.price,
            currency=flight_price.currency,
            seat_class=flight_price.seat_class,
            available_seats=flight_price.available_seats,
            source=flight_price.source,
            scraped_at=flight_price.scraped_at,
            batch_id=flight_price.batch_id,
        )

        self.session.add(price_history)
        self.session.commit()

        return price_history.id

    def update_notification_state(
        self,
        route_id: int,
        notified_at: datetime,
        price: Decimal,
    ) -> None:
        """Update the route's last notification timestamp and price.

        Used by the anti-spam cooldown mechanism to track when and at what
        price the last notification was sent.

        Args:
            route_id: The route ID to update.
            notified_at: UTC timestamp of the notification.
            price: Price at the time of notification.
        """
        route = self.session.query(Route).filter(Route.id == route_id).first()
        if route:
            route.last_notified_at = notified_at
            route.last_notified_price = price
            self.session.commit()
