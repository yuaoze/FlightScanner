"""Database models for FlightScanner using SQLAlchemy ORM.

This module defines the core database tables for storing flight information
and price history.
"""

from datetime import datetime, date, timezone
from decimal import Decimal

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Numeric,
    DateTime,
    Date,
    Text,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

Base = declarative_base()


def utcnow():
    """Return current UTC datetime with timezone info."""
    return datetime.now(timezone.utc)


class Flight(Base):
    """Flight basic information table.

    Stores static flight information that can be referenced by multiple
    price snapshots, reducing data redundancy.

    Attributes:
        id: Primary key.
        flight_no: Flight number (e.g., "CA1234").
        airline: Airline name (e.g., "中国国航").
        departure_city: Departure city name.
        arrival_city: Arrival city name.
        departure_time: Scheduled departure time (HH:MM format).
        arrival_time: Scheduled arrival time (HH:MM format).
        departure_date: Flight date.
        direction: "departure" or "return".
        created_at: Record creation timestamp.
        price_histories: Relationship to price history records.
    """

    __tablename__ = "flights"

    id = Column(Integer, primary_key=True, autoincrement=True)
    flight_no = Column(String(20), nullable=False, index=True)
    airline = Column(String(100), nullable=False)
    departure_city = Column(String(50), nullable=False, index=True)
    arrival_city = Column(String(50), nullable=False, index=True)
    departure_time = Column(String(10), nullable=False)  # HH:MM format
    arrival_time = Column(String(10), nullable=False)  # HH:MM format
    departure_date = Column(Date, nullable=False, index=True)
    direction = Column(String(20), nullable=False)  # "departure" or "return"
    created_at = Column(DateTime, default=utcnow, nullable=False)

    # Relationship
    price_histories = relationship(
        "PriceHistory", back_populates="flight", cascade="all, delete-orphan"
    )

    # Unique constraint: same flight on same date with same direction should be unique
    __table_args__ = (
        UniqueConstraint(
            "flight_no",
            "departure_date",
            "departure_city",
            "arrival_city",
            "direction",
            name="uix_flight_unique",
        ),
        Index("ix_flight_route_date", "departure_city", "arrival_city", "departure_date"),
    )

    def __repr__(self) -> str:
        return (
            f"<Flight(id={self.id}, flight_no='{self.flight_no}', "
            f"route='{self.departure_city}->{self.arrival_city}', "
            f"date={self.departure_date})>"
        )


class Route(Base):
    """Route monitoring configuration table.

    Stores user-defined routes to monitor for price changes.

    Attributes:
        id: Primary key.
        origin: Origin city name.
        destination: Destination city name.
        target_date: Target travel date.
        target_price: Target price threshold for alerts.
        scrape_interval: Scrape interval in hours (default 6).
        is_active: Whether monitoring is active (1=active, 0=inactive).
        created_at: Record creation timestamp.
        updated_at: Record last update timestamp.
        price_histories: Relationship to price history records.
    """

    __tablename__ = "routes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    origin = Column(String(50), nullable=False, index=True)
    destination = Column(String(50), nullable=False, index=True)
    target_date = Column(Date, nullable=False, index=True)
    target_price = Column(Numeric(10, 2), nullable=False)
    scrape_interval = Column(Integer, default=6, nullable=False)  # hours
    is_active = Column(Integer, default=1, nullable=False)  # 1=active, 0=inactive
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    # Relationship
    price_histories = relationship(
        "PriceHistory", back_populates="route", cascade="all, delete-orphan"
    )

    # Indexes
    __table_args__ = (
        Index("ix_route_active_date", "is_active", "target_date"),
        Index("ix_route_origin_dest", "origin", "destination"),
    )

    def __repr__(self) -> str:
        return (
            f"<Route(id={self.id}, route='{self.origin}->{self.destination}', "
            f"target_date={self.target_date}, target_price={self.target_price})>"
        )


class PriceHistory(Base):
    """Price history/snapshot table.

    Stores price snapshots for flights at different points in time,
    enabling trend analysis and price tracking.

    Attributes:
        id: Primary key.
        flight_id: Foreign key to flights table.
        route_id: Foreign key to routes table (nullable for legacy records).
        price: Flight price.
        currency: Currency code (e.g., "CNY").
        seat_class: Seat class (e.g., "经济舱", "商务舱").
        available_seats: Number of available seats (nullable).
        source: Data source platform (e.g., "ctrip").
        scraped_at: Timestamp when this price was scraped.
        flight: Relationship to flight record.
        route: Relationship to route record.
    """

    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    flight_id = Column(Integer, ForeignKey("flights.id"), nullable=False, index=True)
    route_id = Column(Integer, ForeignKey("routes.id"), nullable=True, index=True)
    price = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(10), nullable=False, default="CNY")
    seat_class = Column(String(50), nullable=False)
    available_seats = Column(Integer, nullable=True)
    source = Column(String(50), nullable=False, index=True)
    scraped_at = Column(DateTime, default=utcnow, nullable=False, index=True)

    # Relationships
    flight = relationship("Flight", back_populates="price_histories")
    route = relationship("Route", back_populates="price_histories")

    # Indexes for efficient querying
    __table_args__ = (
        Index(
            "ix_price_history_flight_scraped",
            "flight_id",
            "scraped_at",
        ),
        Index(
            "ix_price_history_scraped_source",
            "scraped_at",
            "source",
        ),
        Index(
            "ix_price_history_route_scraped",
            "route_id",
            "scraped_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<PriceHistory(id={self.id}, flight_id={self.flight_id}, "
            f"price={self.price}, scraped_at={self.scraped_at})>"
        )

    @property
    def price_decimal(self) -> Decimal:
        """Get price as Decimal for calculations."""
        return Decimal(str(self.price))


def init_db(db_url: str = "sqlite:///flightscanner.db"):
    """Initialize database and create all tables.

    Args:
        db_url: Database connection URL. Defaults to SQLite file database.

    Returns:
        Tuple of (engine, SessionLocal) for database operations.
    """
    engine = create_engine(db_url, echo=False, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return engine, SessionLocal
