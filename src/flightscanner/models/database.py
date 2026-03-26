"""Database models for FlightScanner using SQLAlchemy ORM.

This module defines the core database tables for storing flight information
and price history.
"""

from datetime import datetime, date, timezone
from decimal import Decimal

from sqlalchemy import (
    create_engine,
    text,
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

    # 机场信息（通过迁移添加，可空）
    departure_airport = Column(String(100), nullable=True)       # 出发机场全称
    arrival_airport = Column(String(100), nullable=True)         # 到达机场全称
    departure_airport_code = Column(String(10), nullable=True)   # IATA 代码，如 "PEK"
    arrival_airport_code = Column(String(10), nullable=True)     # IATA 代码，如 "HND"

    # 实际到达日期（通过迁移添加，可空）——跨日/多日航班与 departure_date 不同
    arrival_date = Column(Date, nullable=True)

    # Relationship（仅跟踪以本航班为去程/单程的价格记录）
    price_histories = relationship(
        "PriceHistory",
        foreign_keys="[PriceHistory.flight_id]",
        back_populates="flight",
        cascade="all, delete-orphan",
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

    # 往返程 + 国际标记（通过迁移添加）
    return_date = Column(Date, nullable=True)                              # 回程日期，单程=None
    trip_type = Column(String(20), default="oneway")                      # "oneway"/"roundtrip"
    is_international = Column(Integer, default=0, nullable=False)         # 1=国际，0=国内

    # 机场过滤（可空，NULL=不限机场）
    dep_airport_code = Column(String(10), nullable=True)   # 出发机场 IATA 代码，如 "PEK"
    arr_airport_code = Column(String(10), nullable=True)   # 到达机场 IATA 代码，如 "HND"

    # 时间段过滤（可空，NULL=不限时间，格式 "HH:MM"）
    dep_time_from = Column(String(10), nullable=True)   # 起飞时间段开始，如 "06:00"
    dep_time_to   = Column(String(10), nullable=True)   # 起飞时间段结束，如 "12:00"
    arr_time_from = Column(String(10), nullable=True)   # 落地时间段开始
    arr_time_to   = Column(String(10), nullable=True)   # 落地时间段结束

    # 通知防骚扰字段（通过迁移添加）
    last_notified_at    = Column(DateTime, nullable=True)           # 上次通知时间（UTC）
    last_notified_price = Column(Numeric(10, 2), nullable=True)     # 上次通知时的价格
    notify_threshold_pct = Column(Numeric(5, 2), nullable=True)    # 用户自定义低于均价 N% 时通知（None=使用全局默认）
    max_results = Column(Integer, default=20, nullable=False)

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
    # 往返程回程航班（单程时为 NULL）
    return_flight_id = Column(Integer, ForeignKey("flights.id"), nullable=True, index=True)
    price = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(10), nullable=False, default="CNY")
    seat_class = Column(String(50), nullable=False)
    available_seats = Column(Integer, nullable=True)
    source = Column(String(50), nullable=False, index=True)
    scraped_at = Column(DateTime, default=utcnow, nullable=False, index=True)
    # 采集批次 ID：同一次采集批次的所有记录使用相同的 batch_id
    # 用于替代按 scraped_at 精确匹配，解决同秒内多条记录的问题
    batch_id = Column(String(100), nullable=True, index=True)  # 格式: "source_date_timestamp_hash"

    # Relationships
    flight = relationship("Flight", foreign_keys=[flight_id], back_populates="price_histories")
    return_flight = relationship("Flight", foreign_keys=[return_flight_id])
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
        # 基于批次 ID 的索引（新增）
        Index(
            "ix_price_history_route_batch",
            "route_id",
            "batch_id",
        ),
        Index(
            "ix_price_history_source_batch",
            "source",
            "batch_id",
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


def _apply_migrations(engine) -> None:
    """幂等地为已存在的表添加新列（SQLite 不支持 IF NOT EXISTS，用 try/except 跳过已存在列）。"""
    stmts = [
        "ALTER TABLE flights ADD COLUMN departure_airport TEXT",
        "ALTER TABLE flights ADD COLUMN arrival_airport TEXT",
        "ALTER TABLE flights ADD COLUMN departure_airport_code TEXT",
        "ALTER TABLE flights ADD COLUMN arrival_airport_code TEXT",
        "ALTER TABLE routes ADD COLUMN return_date DATE",
        "ALTER TABLE routes ADD COLUMN trip_type TEXT NOT NULL DEFAULT 'oneway'",
        "ALTER TABLE routes ADD COLUMN is_international INTEGER NOT NULL DEFAULT 0",
        # 往返程回程航班 FK（单程时为 NULL）
        "ALTER TABLE price_history ADD COLUMN return_flight_id INTEGER REFERENCES flights(id)",
        # 机场过滤字段
        "ALTER TABLE routes ADD COLUMN dep_airport_code TEXT",
        "ALTER TABLE routes ADD COLUMN arr_airport_code TEXT",
        # 时间段过滤字段
        "ALTER TABLE routes ADD COLUMN dep_time_from TEXT",
        "ALTER TABLE routes ADD COLUMN dep_time_to TEXT",
        "ALTER TABLE routes ADD COLUMN arr_time_from TEXT",
        "ALTER TABLE routes ADD COLUMN arr_time_to TEXT",
        # 通知防骚扰字段
        "ALTER TABLE routes ADD COLUMN last_notified_at DATETIME",
        "ALTER TABLE routes ADD COLUMN last_notified_price NUMERIC",
        "ALTER TABLE routes ADD COLUMN notify_threshold_pct NUMERIC",
        # batch_id：用于标记同一次采集会话的所有记录（解决同秒多条记录取最低价错误问题）
        "ALTER TABLE price_history ADD COLUMN batch_id TEXT",
        # arrival_date：实际到达日期（跨日/多日航班的到达日期，可为 NULL）
        "ALTER TABLE flights ADD COLUMN arrival_date DATE",
        # max_results：每路线每平台最多采集的航班条数
        "ALTER TABLE routes ADD COLUMN max_results INTEGER NOT NULL DEFAULT 20",
    ]
    with engine.connect() as conn:
        for stmt in stmts:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # 列已存在时 SQLite 报错，直接跳过


def init_db(db_url: str = "sqlite:///flightscanner.db"):
    """Initialize database and create all tables.

    Args:
        db_url: Database connection URL. Defaults to SQLite file database.

    Returns:
        Tuple of (engine, SessionLocal) for database operations.
    """
    # SQLite 需要 check_same_thread=False，因为 APScheduler 采集线程和 Streamlit
    # 主线程会共享同一个 engine；同时开启 WAL 模式提升并发读写性能
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    engine = create_engine(db_url, echo=False, pool_pre_ping=True, connect_args=connect_args)

    # 对 SQLite 开启 WAL 模式（Write-Ahead Logging），允许读写并发
    if db_url.startswith("sqlite"):
        with engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.commit()

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    _apply_migrations(engine)
    return engine, SessionLocal
