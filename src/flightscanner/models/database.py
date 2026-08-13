"""Database models for FlightScanner using SQLAlchemy ORM.

This module defines the core database tables for storing flight information
and price history.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    create_engine,
    event,
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
    inspect,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import backref, relationship, sessionmaker

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
    # 软删除时间。有买入计划/记录的路线不能物理删除，否则会破坏长期复盘账本。
    deleted_at = Column(DateTime, nullable=True, index=True)

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

    # 回程时间段过滤（仅 roundtrip 使用；格式 "HH:MM"）
    ret_dep_time_from = Column(String(10), nullable=True)   # 回程起飞时间段开始
    ret_dep_time_to   = Column(String(10), nullable=True)   # 回程起飞时间段结束
    ret_arr_time_from = Column(String(10), nullable=True)   # 回程落地时间段开始
    ret_arr_time_to   = Column(String(10), nullable=True)   # 回程落地时间段结束

    # 通知防骚扰字段（通过迁移添加）
    last_notified_at    = Column(DateTime, nullable=True)           # 上次通知时间（UTC）
    last_notified_price = Column(Numeric(10, 2), nullable=True)     # 上次通知时的价格
    notify_threshold_pct = Column(Numeric(5, 2), nullable=True)    # 用户自定义低于均价 N% 时通知（None=使用全局默认）
    max_results = Column(Integer, default=20, nullable=False)

    # 通知智能化字段（v1.6.0 新增）
    recent_3d_low         = Column(Numeric(10, 2), nullable=True)   # 最近3天最低价
    recent_3d_low_at      = Column(DateTime, nullable=True)          # 最近3天低点时间（UTC）
    last_notified_reason  = Column(String(50), nullable=True)        # 上次通知的触发原因

    # 精准航班号监控字段（通过迁移添加）
    monitoring_mode       = Column(String(20), default="route", nullable=False)  # 'route' | 'flight'
    outbound_flight_no    = Column(String(20), nullable=True)   # 指定去程航班号，如 "CA953"
    inbound_flight_no     = Column(String(20), nullable=True)   # 指定回程航班号（往返时使用）
    pinned_seat_class     = Column(String(50), nullable=True)   # 指定舱位，如 "经济舱"（None=不限）
    outbound_dep_time_ref = Column(String(10), nullable=True)   # 添加时的去程起飞参考时刻 "HH:MM"
    inbound_dep_time_ref  = Column(String(10), nullable=True)   # 添加时的回程起飞参考时刻 "HH:MM"
    last_flight_status    = Column(String(30), nullable=True)   # 'available'|'sold_out'|'not_found'|'schedule_changed'

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


class AIPredictionLog(Base):
    """AI 预测记录表，用于 4 齿轮自进化引擎的闭环回测。

    G1 执行器：采集后记录结构化预测。
    G2 监控器：航班出发后回测，计算 Pain Index。
    G3 诊断器：高痛失误 RCA。
    G4 进化器：动态注入历史失误上下文。
    """

    __tablename__ = "ai_prediction_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    route_id = Column(Integer, ForeignKey("routes.id"), nullable=False, index=True)

    # ── G1 执行器字段 ──────────────────────────────────────────
    predicted_at = Column(DateTime, default=utcnow, nullable=False, index=True)
    price_at_prediction = Column(Numeric(10, 2), nullable=False)   # 预测时当前最低价
    days_until_flight = Column(Integer, nullable=False)             # 预测时距出发天数
    recommended_action = Column(String(10), nullable=False)         # "Buy" | "Wait"
    reason = Column(Text, nullable=True)
    trend = Column(String(20), nullable=True)                       # 上涨|下跌|震荡|稳定
    confidence = Column(Numeric(4, 3), nullable=True)               # 0.000~1.000
    llm_source = Column(String(20), nullable=False, default="rule_based")  # "deepseek"/"rule_based"

    # ── G2 监控器字段 ──────────────────────────────────────────
    outcome_status = Column(String(20), nullable=False, default="pending")
    # pending | win | loss | neutral | skipped
    actual_min_price = Column(Numeric(10, 2), nullable=True)        # 出发前最低价
    actual_final_price = Column(Numeric(10, 2), nullable=True)      # 出发前最后采集价
    pain_index = Column(Numeric(10, 2), nullable=True)              # Regret Cost (CNY)
    catchable_low_exists = Column(Integer, nullable=True)           # 1=有可捕捉低价 0=无

    # ── G3 诊断器字段 ──────────────────────────────────────────
    rca_run_at = Column(DateTime, nullable=True)
    error_category = Column(String(50), nullable=True)
    rca_analysis = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_aiplog_route_predicted", "route_id", "predicted_at"),
        Index("ix_aiplog_outcome", "outcome_status"),
    )

    def __repr__(self) -> str:
        return (
            f"<AIPredictionLog(id={self.id}, route_id={self.route_id}, "
            f"action='{self.recommended_action}', status='{self.outcome_status}')>"
        )


class BuyPlan(Base):
    """买入计划表：用户设定的目标买入价和/或最迟买入时间。

    状态机：pending → triggered（价格达标或到期）→ converted（确认成交）
    也可转为 cancelled（手动取消）或 expired（过期未触发）。
    """

    __tablename__ = "buy_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    route_id = Column(Integer, ForeignKey("routes.id"), nullable=False, index=True)
    plan_price = Column(Numeric(10, 2), nullable=True)          # 目标买入价，NULL=仅按时间触发
    plan_execute_by = Column(DateTime, nullable=True)           # 最迟买入时间，NULL=仅按价格触发
    status = Column(String(20), nullable=False, default="pending")
    # pending | triggered | converted | cancelled | expired
    triggered_at = Column(DateTime, nullable=True)
    trigger_price = Column(Numeric(10, 2), nullable=True)       # 触发时监控到的价格
    trigger_reason = Column(String(30), nullable=True)          # "price_hit" | "deadline"
    # 通知投递状态。触发和通知分开持久化，失败后由每日任务重试。
    notification_status = Column(String(20), nullable=False, default="pending")
    # pending | sent | failed
    notification_attempts = Column(Integer, nullable=False, default=0)
    last_notification_at = Column(DateTime, nullable=True)
    notification_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    route = relationship(
        "Route",
        backref=backref("buy_plans", passive_deletes=True),
    )

    __table_args__ = (
        Index("ix_buyplan_status", "status"),
        Index("ix_buyplan_route_status", "route_id", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<BuyPlan(id={self.id}, route_id={self.route_id}, "
            f"plan_price={self.plan_price}, status='{self.status}')>"
        )


class PurchaseRecord(Base):
    """买入记录表：用户记录的一次实际成交。"""

    __tablename__ = "purchase_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    route_id = Column(Integer, ForeignKey("routes.id"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("buy_plans.id"), nullable=True)
    flight_id = Column(Integer, ForeignKey("flights.id"), nullable=True)
    # purchase_price 始终为单人、同币种的可比票价；total_paid 为订单实付总额。
    purchase_price = Column(Numeric(10, 2), nullable=False)
    total_paid = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(10), nullable=False, default="CNY")
    seat_class = Column(String(50), nullable=True)
    passengers = Column(Integer, nullable=False, default=1)
    # 成交时对应的报价快照，避免后续无法确认比较口径。
    quote_price = Column(Numeric(10, 2), nullable=True)
    quote_source = Column(String(50), nullable=True)
    quote_batch_id = Column(String(100), nullable=True)
    # 路线不可变快照：即使监控路线软删除/修改，购买账本仍可独立展示和复盘。
    route_origin = Column(String(50), nullable=True)
    route_destination = Column(String(50), nullable=True)
    route_target_date = Column(Date, nullable=True)
    route_return_date = Column(Date, nullable=True)
    route_trip_type = Column(String(20), nullable=True)
    purchased_at = Column(DateTime, default=utcnow, nullable=False)
    purchase_type = Column(String(20), nullable=False, default="instant")  # instant | planned
    notes = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="holding")  # holding | completed
    created_at = Column(DateTime, default=utcnow, nullable=False)

    route = relationship(
        "Route",
        backref=backref("purchase_records", passive_deletes=True),
    )
    plan = relationship("BuyPlan")
    flight = relationship("Flight")

    __table_args__ = (
        # 一个计划最多转换为一笔买入；NULL 仍允许多笔即时买入。
        Index("ux_purchase_plan_id", "plan_id", unique=True),
        Index("ix_purchase_status", "status"),
        Index("ix_purchase_route_time", "route_id", "purchased_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<PurchaseRecord(id={self.id}, route_id={self.route_id}, "
            f"price={self.purchase_price}, status='{self.status}')>"
        )


class BuyPointAnalysis(Base):
    """买点分析表：与买入记录 1:1，起飞后自动或手动生成。"""

    __tablename__ = "buy_point_analyses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    purchase_id = Column(
        Integer, ForeignKey("purchase_records.id"), nullable=False, unique=True, index=True
    )
    # 窗口统计（purchased_at → 起飞日）
    post_min_price = Column(Numeric(10, 2), nullable=True)      # 买后最低价
    post_max_price = Column(Numeric(10, 2), nullable=True)      # 买后最高价
    final_price = Column(Numeric(10, 2), nullable=True)         # 起飞前最后采集价
    regret_cost = Column(Numeric(10, 2), nullable=True)         # 多付金额（相对买后最低）
    savings_vs_final = Column(Numeric(10, 2), nullable=True)    # 相对最终价节省
    verdict = Column(String(20), nullable=True)                 # excellent | good | fair | poor
    ai_analysis = Column(Text, nullable=True)                   # LLM 分析文本（JSON）
    llm_source = Column(String(20), nullable=False, default="rule_based")
    auto_generated = Column(Integer, nullable=False, default=0)  # 1=自动生成 0=手动
    pre_departure = Column(Integer, nullable=False, default=0)   # 1=分析时航班未起飞
    sample_size = Column(Integer, nullable=False, default=0)     # 可比较采集批次数
    coverage_hours = Column(Numeric(10, 2), nullable=True)       # 首末有效样本覆盖时长
    data_quality = Column(String(20), nullable=True)             # good | limited | insufficient
    analysis_status = Column(String(20), nullable=False, default="provisional")
    # provisional | final | insufficient
    analyzed_at = Column(DateTime, default=utcnow, nullable=False)

    purchase = relationship(
        "PurchaseRecord",
        backref=backref("analysis", uselist=False, cascade="all, delete-orphan"),
    )

    def __repr__(self) -> str:
        return (
            f"<BuyPointAnalysis(id={self.id}, purchase_id={self.purchase_id}, "
            f"verdict='{self.verdict}')>"
        )


class ExperienceEntry(Base):
    """经验库：从买点分析沉淀的可复用经验，注入后续 AI 决策。"""

    __tablename__ = "experience_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    analysis_id = Column(Integer, ForeignKey("buy_point_analyses.id"), nullable=True)
    route_pattern = Column(String(120), nullable=False, default="通用")
    category = Column(String(20), nullable=False, default="general")  # timing|route|holiday|general
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    evidence_count = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default="active")  # active | archived
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    analysis = relationship("BuyPointAnalysis")

    __table_args__ = (
        Index("ix_experience_status", "status"),
        Index("ix_experience_pattern", "route_pattern", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<ExperienceEntry(id={self.id}, pattern='{self.route_pattern}', "
            f"title='{self.title}')>"
        )


class ExperienceEvidence(Base):
    """经验与独立买入案例的证据关联。

    同一笔购买反复分析只会对应同一条证据，避免把重新生成分析误计为
    多个独立案例。``evidence_count`` 可由该表的去重行数重算。
    """

    __tablename__ = "experience_evidence"

    id = Column(Integer, primary_key=True, autoincrement=True)
    experience_id = Column(
        Integer,
        ForeignKey("experience_entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purchase_id = Column(
        Integer,
        ForeignKey("purchase_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = Column(DateTime, default=utcnow, nullable=False)

    experience = relationship(
        "ExperienceEntry",
        backref=backref("evidence", cascade="all, delete-orphan"),
    )
    purchase = relationship("PurchaseRecord")

    __table_args__ = (
        UniqueConstraint(
            "experience_id",
            "purchase_id",
            name="uq_experience_evidence_purchase",
        ),
    )


class WeekendRadarCache(Base):
    """周末低价雷达缓存表。

    存储批量或手动扫描的周末往返航班推荐结果，
    包含去程/回程基本信息、价格、AI 文案等。
    """

    __tablename__ = "weekend_radar_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    origin = Column(String(50), nullable=False)
    destination = Column(String(50), nullable=False)
    outbound_date = Column(Date, nullable=False)   # 周五
    return_date = Column(Date, nullable=False)      # 周日

    # 去程信息
    outbound_flight_no = Column(String(30))
    outbound_airline = Column(String(50))
    outbound_dep_time = Column(String(5))    # "HH:MM"
    outbound_arr_time = Column(String(5))
    outbound_dep_airport = Column(String(10), nullable=True)

    # 回程信息
    return_flight_no = Column(String(30))
    return_airline = Column(String(50))
    return_dep_time = Column(String(5))
    return_arr_time = Column(String(5))

    # 价格信息
    total_price = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(5), default="CNY")
    historical_avg = Column(Numeric(10, 2), nullable=True)
    beat_pct = Column(Integer, nullable=True)    # 击败历史均价%

    # AI 文案（JSON 字符串）
    ai_brief = Column(Text, nullable=True)

    # 元信息
    source = Column(String(20))           # "qunar"
    scan_type = Column(String(20))        # "batch" | "manual"
    scanned_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        Index("idx_wrc_outbound", "outbound_date"),
        Index("idx_wrc_dest_date", "destination", "outbound_date"),
        Index("idx_wrc_scanned", "scanned_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<WeekendRadarCache(id={self.id}, dest='{self.destination}', "
            f"outbound={self.outbound_date}, price={self.total_price})>"
        )


class NotificationLog(Base):
    """Notification history log table.

    Records every notification sent (or failed) for audit and display purposes.
    """

    __tablename__ = "notification_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    route_id = Column(Integer, ForeignKey("routes.id"), nullable=False)
    notified_at = Column(DateTime, default=utcnow, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    trigger_reason = Column(String(50), nullable=False)
    channel = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False)
    message_summary = Column(Text, nullable=True)

    route = relationship(
        "Route",
        backref=backref("notification_logs", cascade="all, delete-orphan"),
    )

    __table_args__ = (
        Index("ix_notif_route_time", "route_id", "notified_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<NotificationLog(id={self.id}, route={self.route_id}, "
            f"reason='{self.trigger_reason}', status='{self.status}')>"
        )


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
        # 回程时间段过滤字段（仅 roundtrip 使用）
        "ALTER TABLE routes ADD COLUMN ret_dep_time_from TEXT",
        "ALTER TABLE routes ADD COLUMN ret_dep_time_to TEXT",
        "ALTER TABLE routes ADD COLUMN ret_arr_time_from TEXT",
        "ALTER TABLE routes ADD COLUMN ret_arr_time_to TEXT",
        # 通知防骚扰字段
        "ALTER TABLE routes ADD COLUMN last_notified_at DATETIME",
        "ALTER TABLE routes ADD COLUMN last_notified_price NUMERIC",
        "ALTER TABLE routes ADD COLUMN notify_threshold_pct NUMERIC",
        "ALTER TABLE routes ADD COLUMN deleted_at DATETIME",
        "CREATE INDEX IF NOT EXISTS ix_routes_deleted_at ON routes(deleted_at)",
        # batch_id：用于标记同一次采集会话的所有记录（解决同秒多条记录取最低价错误问题）
        "ALTER TABLE price_history ADD COLUMN batch_id TEXT",
        # arrival_date：实际到达日期（跨日/多日航班的到达日期，可为 NULL）
        "ALTER TABLE flights ADD COLUMN arrival_date DATE",
        # max_results：每路线每平台最多采集的航班条数
        "ALTER TABLE routes ADD COLUMN max_results INTEGER NOT NULL DEFAULT 20",
        # 精准航班号监控字段
        "ALTER TABLE routes ADD COLUMN monitoring_mode TEXT NOT NULL DEFAULT 'route'",
        "ALTER TABLE routes ADD COLUMN outbound_flight_no TEXT",
        "ALTER TABLE routes ADD COLUMN inbound_flight_no TEXT",
        "ALTER TABLE routes ADD COLUMN pinned_seat_class TEXT",
        "ALTER TABLE routes ADD COLUMN outbound_dep_time_ref TEXT",
        "ALTER TABLE routes ADD COLUMN inbound_dep_time_ref TEXT",
        "ALTER TABLE routes ADD COLUMN last_flight_status TEXT",
        # AI 进化引擎：预测记录表（使用 CREATE TABLE IF NOT EXISTS，已有表静默跳过）
        (
            "CREATE TABLE IF NOT EXISTS ai_prediction_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "route_id INTEGER NOT NULL REFERENCES routes(id), "
            "predicted_at DATETIME NOT NULL, "
            "price_at_prediction NUMERIC NOT NULL, "
            "days_until_flight INTEGER NOT NULL, "
            "recommended_action TEXT NOT NULL, "
            "reason TEXT, "
            "trend TEXT, "
            "confidence NUMERIC, "
            "llm_source TEXT NOT NULL DEFAULT 'rule_based', "
            "outcome_status TEXT NOT NULL DEFAULT 'pending', "
            "actual_min_price NUMERIC, "
            "actual_final_price NUMERIC, "
            "pain_index NUMERIC, "
            "catchable_low_exists INTEGER, "
            "rca_run_at DATETIME, "
            "error_category TEXT, "
            "rca_analysis TEXT)"
        ),
        # 周末低价雷达缓存表（使用 CREATE TABLE IF NOT EXISTS，已有表静默跳过）
        (
            "CREATE TABLE IF NOT EXISTS weekend_radar_cache ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "origin TEXT NOT NULL, "
            "destination TEXT NOT NULL, "
            "outbound_date DATE NOT NULL, "
            "return_date DATE NOT NULL, "
            "outbound_flight_no TEXT, "
            "outbound_airline TEXT, "
            "outbound_dep_time TEXT, "
            "outbound_arr_time TEXT, "
            "outbound_dep_airport TEXT, "
            "return_flight_no TEXT, "
            "return_airline TEXT, "
            "return_dep_time TEXT, "
            "return_arr_time TEXT, "
            "total_price NUMERIC NOT NULL, "
            "currency TEXT DEFAULT 'CNY', "
            "historical_avg NUMERIC, "
            "beat_pct INTEGER, "
            "ai_brief TEXT, "
            "source TEXT, "
            "scan_type TEXT, "
            "scanned_at DATETIME NOT NULL)"
        ),
        # v1.6.0 通知智能化字段
        "ALTER TABLE routes ADD COLUMN recent_3d_low NUMERIC",
        "ALTER TABLE routes ADD COLUMN recent_3d_low_at DATETIME",
        "ALTER TABLE routes ADD COLUMN last_notified_reason TEXT",
        # v2.2.0 买入闭环四表（使用 CREATE TABLE IF NOT EXISTS，已有表静默跳过）
        (
            "CREATE TABLE IF NOT EXISTS buy_plans ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "route_id INTEGER NOT NULL REFERENCES routes(id), "
            "plan_price NUMERIC, "
            "plan_execute_by DATETIME, "
            "status TEXT NOT NULL DEFAULT 'pending', "
            "triggered_at DATETIME, "
            "trigger_price NUMERIC, "
            "trigger_reason TEXT, "
            "notification_status TEXT NOT NULL DEFAULT 'pending', "
            "notification_attempts INTEGER NOT NULL DEFAULT 0, "
            "last_notification_at DATETIME, "
            "notification_error TEXT, "
            "created_at DATETIME NOT NULL)"
        ),
        "ALTER TABLE buy_plans ADD COLUMN notification_status TEXT NOT NULL DEFAULT 'pending'",
        "ALTER TABLE buy_plans ADD COLUMN notification_attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE buy_plans ADD COLUMN last_notification_at DATETIME",
        "ALTER TABLE buy_plans ADD COLUMN notification_error TEXT",
        (
            "CREATE TABLE IF NOT EXISTS purchase_records ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "route_id INTEGER NOT NULL REFERENCES routes(id), "
            "plan_id INTEGER UNIQUE REFERENCES buy_plans(id), "
            "flight_id INTEGER REFERENCES flights(id), "
            "purchase_price NUMERIC NOT NULL, "
            "total_paid NUMERIC, "
            "currency TEXT NOT NULL DEFAULT 'CNY', "
            "seat_class TEXT, "
            "passengers INTEGER NOT NULL DEFAULT 1, "
            "quote_price NUMERIC, "
            "quote_source TEXT, "
            "quote_batch_id TEXT, "
            "route_origin TEXT, "
            "route_destination TEXT, "
            "route_target_date DATE, "
            "route_return_date DATE, "
            "route_trip_type TEXT, "
            "purchased_at DATETIME NOT NULL, "
            "purchase_type TEXT NOT NULL DEFAULT 'instant', "
            "notes TEXT, "
            "status TEXT NOT NULL DEFAULT 'holding', "
            "created_at DATETIME NOT NULL)"
        ),
        "ALTER TABLE purchase_records ADD COLUMN total_paid NUMERIC",
        "ALTER TABLE purchase_records ADD COLUMN quote_price NUMERIC",
        "ALTER TABLE purchase_records ADD COLUMN quote_source TEXT",
        "ALTER TABLE purchase_records ADD COLUMN quote_batch_id TEXT",
        "ALTER TABLE purchase_records ADD COLUMN route_origin TEXT",
        "ALTER TABLE purchase_records ADD COLUMN route_destination TEXT",
        "ALTER TABLE purchase_records ADD COLUMN route_target_date DATE",
        "ALTER TABLE purchase_records ADD COLUMN route_return_date DATE",
        "ALTER TABLE purchase_records ADD COLUMN route_trip_type TEXT",
        (
            "CREATE TABLE IF NOT EXISTS buy_point_analyses ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "purchase_id INTEGER NOT NULL UNIQUE REFERENCES purchase_records(id), "
            "post_min_price NUMERIC, "
            "post_max_price NUMERIC, "
            "final_price NUMERIC, "
            "regret_cost NUMERIC, "
            "savings_vs_final NUMERIC, "
            "verdict TEXT, "
            "ai_analysis TEXT, "
            "llm_source TEXT NOT NULL DEFAULT 'rule_based', "
            "auto_generated INTEGER NOT NULL DEFAULT 0, "
            "pre_departure INTEGER NOT NULL DEFAULT 0, "
            "sample_size INTEGER NOT NULL DEFAULT 0, "
            "coverage_hours NUMERIC, "
            "data_quality TEXT, "
            "analysis_status TEXT NOT NULL DEFAULT 'provisional', "
            "analyzed_at DATETIME NOT NULL)"
        ),
        "ALTER TABLE buy_point_analyses ADD COLUMN sample_size INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE buy_point_analyses ADD COLUMN coverage_hours NUMERIC",
        "ALTER TABLE buy_point_analyses ADD COLUMN data_quality TEXT",
        "ALTER TABLE buy_point_analyses ADD COLUMN analysis_status TEXT NOT NULL DEFAULT 'provisional'",
        (
            "CREATE TABLE IF NOT EXISTS experience_entries ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "analysis_id INTEGER REFERENCES buy_point_analyses(id), "
            "route_pattern TEXT NOT NULL DEFAULT '通用', "
            "category TEXT NOT NULL DEFAULT 'general', "
            "title TEXT NOT NULL, "
            "content TEXT NOT NULL, "
            "evidence_count INTEGER NOT NULL DEFAULT 1, "
            "status TEXT NOT NULL DEFAULT 'active', "
            "created_at DATETIME NOT NULL, "
            "updated_at DATETIME NOT NULL)"
        ),
        (
            "CREATE TABLE IF NOT EXISTS experience_evidence ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "experience_id INTEGER NOT NULL REFERENCES experience_entries(id) ON DELETE CASCADE, "
            "purchase_id INTEGER NOT NULL REFERENCES purchase_records(id) ON DELETE CASCADE, "
            "created_at DATETIME NOT NULL, "
            "CONSTRAINT uq_experience_evidence_purchase "
            "UNIQUE (experience_id, purchase_id))"
        ),
    ]
    with engine.connect() as conn:
        for stmt in stmts:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception as exc:
                # ``Base.metadata.create_all`` runs before this compatibility
                # migration, so ALTER statements for an already-current DB are
                # expected to report duplicate columns.  Do not swallow lock,
                # disk, malformed SQL, or missing-table errors: continuing with
                # a half-migrated schema only turns startup failure into later
                # data loss/500 responses.
                message = str(exc).lower()
                expected_duplicate = (
                    "duplicate column name" in message
                    or "already exists" in message
                )
                if not expected_duplicate:
                    raise RuntimeError(f"数据库迁移失败：{stmt}") from exc

    if engine.dialect.name == "sqlite":
        # 旧版本在并发确认时可能为同一 plan 写入多条 purchase。保留全部账本，
        # 仅解除后续重复记录的 plan 关联，再建立唯一索引以阻止继续重复。
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE purchase_records SET plan_id = NULL "
                    "WHERE plan_id IS NOT NULL AND id NOT IN ("
                    "SELECT MIN(id) FROM purchase_records "
                    "WHERE plan_id IS NOT NULL GROUP BY plan_id)"
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_purchase_plan_id "
                    "ON purchase_records(plan_id)"
                )
            )

            # 为已有经验补建可审计证据。历史上无法证明 evidence_count 的增量
            # 来自独立购买，因此迁移后以可验证的唯一购买数为准。
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO experience_evidence "
                    "(experience_id, purchase_id, created_at) "
                    "SELECT e.id, a.purchase_id, e.created_at "
                    "FROM experience_entries e "
                    "JOIN buy_point_analyses a ON a.id = e.analysis_id "
                    "WHERE e.analysis_id IS NOT NULL"
                )
            )
            conn.execute(
                text(
                    "UPDATE experience_entries SET evidence_count = ("
                    "SELECT COUNT(*) FROM experience_evidence ee "
                    "WHERE ee.experience_id = experience_entries.id) "
                    "WHERE EXISTS (SELECT 1 FROM experience_evidence ee "
                    "WHERE ee.experience_id = experience_entries.id)"
                )
            )
            conn.execute(
                text(
                    "UPDATE experience_entries SET evidence_count = 0 "
                    "WHERE analysis_id IS NULL AND NOT EXISTS ("
                    "SELECT 1 FROM experience_evidence ee "
                    "WHERE ee.experience_id = experience_entries.id)"
                )
            )

        # Fail fast if a partially upgraded database is missing any field that
        # the v2.2 purchase lifecycle relies on.
        schema = inspect(engine)
        required_columns = {
            "routes": {"deleted_at"},
            "buy_plans": {
                "notification_status",
                "notification_attempts",
                "last_notification_at",
                "notification_error",
            },
            "purchase_records": {
                "total_paid",
                "quote_price",
                "quote_source",
                "quote_batch_id",
                "route_origin",
                "route_destination",
                "route_target_date",
                "route_return_date",
                "route_trip_type",
            },
            "buy_point_analyses": {
                "sample_size",
                "coverage_hours",
                "data_quality",
                "analysis_status",
            },
            "experience_evidence": {"experience_id", "purchase_id"},
        }
        missing: list[str] = []
        existing_tables = set(schema.get_table_names())
        for table_name, expected in required_columns.items():
            if table_name not in existing_tables:
                missing.append(f"{table_name}.*")
                continue
            actual = {column["name"] for column in schema.get_columns(table_name)}
            missing.extend(
                f"{table_name}.{column}" for column in sorted(expected - actual)
            )
        purchase_indexes = {
            index["name"]: bool(index.get("unique"))
            for index in schema.get_indexes("purchase_records")
        }
        if not purchase_indexes.get("ux_purchase_plan_id"):
            missing.append("purchase_records.ux_purchase_plan_id(unique)")
        if missing:
            raise RuntimeError(
                "数据库迁移后结构校验失败，缺少：" + ", ".join(missing)
            )


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

    # SQLite 的 connect 监听必须在任何 ``engine.connect()`` 之前注册。
    # 否则连接池中的首个连接会永久保持 foreign_keys=OFF，恰好也是初始化和
    # 大部分单元测试最常复用的连接。
    if db_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_sqlite_fk(dbapi_conn, _connection_record) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        # 首次取连接时同时验证监听器已生效，再开启 WAL 提升并发读写性能。
        # 若运行环境拒绝启用外键，宁可初始化失败，也不要静默写入孤儿数据。
        with engine.connect() as conn:
            fk_enabled = conn.execute(text("PRAGMA foreign_keys")).scalar()
            if fk_enabled != 1:
                raise RuntimeError("SQLite foreign key enforcement could not be enabled")
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.commit()

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    _apply_migrations(engine)
    return engine, SessionLocal
