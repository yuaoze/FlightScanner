"""Pydantic response schemas for the FlightScanner API."""

from datetime import date
from typing import List, Literal, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class SparklinePoint(BaseModel):
    date: str
    price: float


class FlightBriefInfo(BaseModel):
    flight_no: str
    airline: str
    departure_time: str
    arrival_time: str
    duration: Optional[str] = None
    departure_airport_code: Optional[str] = None
    arrival_airport_code: Optional[str] = None


class RouteResponse(BaseModel):
    id: int
    origin: str
    destination: str
    target_date: date
    return_date: Optional[date] = None
    trip_type: str
    target_price: float
    latest_price: Optional[float] = None
    status: str
    trend_direction: str
    trend_confidence: float
    trend_recommendation: str
    price_vs_avg_pct: Optional[float] = None
    prediction_text: str
    sparkline: List[SparklinePoint]
    flight_info: Optional[FlightBriefInfo] = None
    days_until: int
    has_alert: bool
    is_active: bool
    monitoring_mode: str
    outbound_flight_no: Optional[str] = None
    seat_class: Optional[str] = None
    latest_scraped_at: Optional[str] = None
    scrape_interval: int = 6


class StatsResponse(BaseModel):
    total_monitors: int
    buy_count: int
    hold_count: int
    expensive_count: int
    average_drop_pct: Optional[float] = None
    alert_count: int


class PriceHistoryPoint(BaseModel):
    date: str
    price: float
    source: str


class PriceHistoryResponse(BaseModel):
    route_id: int
    points: List[PriceHistoryPoint]


# ── Route Detail schemas ──────────────────────────────────────────────────


class RouteDetailResponse(RouteResponse):
    scrape_interval: int
    dep_airport_code: Optional[str] = None
    arr_airport_code: Optional[str] = None
    dep_time_from: Optional[str] = None
    dep_time_to: Optional[str] = None
    arr_time_from: Optional[str] = None
    arr_time_to: Optional[str] = None
    ret_dep_time_from: Optional[str] = None
    ret_dep_time_to: Optional[str] = None
    ret_arr_time_from: Optional[str] = None
    ret_arr_time_to: Optional[str] = None
    created_at: Optional[str] = None


class PredictionLogItem(BaseModel):
    id: int
    predicted_at: str
    price_at_prediction: float
    recommended_action: str
    reason: Optional[str] = None
    confidence: Optional[float] = None
    llm_source: str
    outcome_status: str
    actual_min_price: Optional[float] = None
    pain_index: Optional[float] = None


class RoutePredictionsResponse(BaseModel):
    route_id: int
    predictions: List[PredictionLogItem]
    win_rate: Optional[float] = None
    total: int


class UpdateRouteRequest(BaseModel):
    target_price: Optional[float] = None
    scrape_interval: Optional[int] = None
    is_active: Optional[bool] = None
    # Time windows: pass empty string "" to clear, or "HH:MM" to set.
    # Field is treated as "not provided" only when omitted (model_fields_set).
    dep_time_from: Optional[str] = None
    dep_time_to: Optional[str] = None
    arr_time_from: Optional[str] = None
    arr_time_to: Optional[str] = None
    ret_dep_time_from: Optional[str] = None
    ret_dep_time_to: Optional[str] = None
    ret_arr_time_from: Optional[str] = None
    ret_arr_time_to: Optional[str] = None


# ── Flight batch / listing schemas ────────────────────────────────────────


class BatchInfo(BaseModel):
    batch_id: str
    source: str
    scraped_at: str
    flight_count: int
    min_price: float


class RouteBatchesResponse(BaseModel):
    route_id: int
    batches: List[BatchInfo]


class FlightListItem(BaseModel):
    flight_no: str
    airline: str
    departure_time: str
    arrival_time: str
    duration: Optional[str] = None
    departure_airport_code: Optional[str] = None
    arrival_airport_code: Optional[str] = None
    price: float
    seat_class: str
    available_seats: Optional[int] = None
    source: str
    batch_id: Optional[str] = None
    return_flight_no: Optional[str] = None
    return_departure_time: Optional[str] = None
    return_arrival_time: Optional[str] = None


class RouteFlightsResponse(BaseModel):
    route_id: int
    batch_id: Optional[str] = None
    scraped_at: Optional[str] = None
    flights: List[FlightListItem]


# ── Purchase tracking schemas（v2.2.0 买入闭环）──────────────────────────


class BuyPlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_id: int = Field(gt=0)
    plan_price: Optional[float] = Field(
        default=None, gt=0, le=1_000_000, allow_inf_nan=False
    )
    plan_execute_by: Optional[AwareDatetime] = None


class BuyPlanResponse(BaseModel):
    id: int
    route_id: int
    route_label: str
    plan_price: Optional[float] = None
    plan_execute_by: Optional[str] = None
    status: Literal["pending", "triggered", "converted", "cancelled", "expired"]
    triggered_at: Optional[str] = None
    trigger_price: Optional[float] = None
    trigger_reason: Optional[str] = None
    created_at: Optional[str] = None


class PlanConfirmRequest(BaseModel):
    # unit_price 是 v2.2.0 后的单人可比票价；actual_price/price
    # 仅作旧客户端兼容。
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    unit_price: Optional[float] = Field(default=None, gt=0, le=1_000_000, allow_inf_nan=False)
    actual_price: Optional[float] = Field(default=None, gt=0, le=1_000_000, allow_inf_nan=False)
    price: Optional[float] = Field(default=None, gt=0, le=1_000_000, allow_inf_nan=False)
    total_paid: Optional[float] = Field(default=None, gt=0, le=100_000_000, allow_inf_nan=False)
    currency: str = Field(default="CNY", pattern=r"^[A-Za-z]{3}$")
    seat_class: Optional[str] = Field(default=None, max_length=50)
    passengers: int = Field(default=1, ge=1, le=100)
    purchased_at: Optional[AwareDatetime] = None
    notes: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_price_aliases(self) -> "PlanConfirmRequest":
        values = [v for v in (self.unit_price, self.actual_price, self.price) if v is not None]
        if not values:
            raise ValueError("unit_price/actual_price/price 至少填写一个")
        if max(values) - min(values) > 0.01:
            raise ValueError("单人票价兼容字段的值不一致")
        return self


class InstantBuyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    route_id: int = Field(gt=0)
    unit_price: Optional[float] = Field(default=None, gt=0, le=1_000_000, allow_inf_nan=False)
    price: Optional[float] = Field(default=None, gt=0, le=1_000_000, allow_inf_nan=False)
    total_paid: Optional[float] = Field(default=None, gt=0, le=100_000_000, allow_inf_nan=False)
    currency: str = Field(default="CNY", pattern=r"^[A-Za-z]{3}$")
    seat_class: Optional[str] = Field(default=None, max_length=50)
    passengers: int = Field(default=1, ge=1, le=100)
    purchased_at: Optional[AwareDatetime] = None
    notes: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_price_aliases(self) -> "InstantBuyRequest":
        if (
            self.unit_price is not None
            and self.price is not None
            and abs(self.unit_price - self.price) > 0.01
        ):
            raise ValueError("unit_price 与兼容字段 price 不一致")
        return self


class BuyPointAnalysisResponse(BaseModel):
    post_min_price: Optional[float] = None
    post_max_price: Optional[float] = None
    final_price: Optional[float] = None
    regret_cost: Optional[float] = None
    savings_vs_final: Optional[float] = None
    verdict: Optional[Literal["excellent", "good", "fair", "poor"]] = None
    ai_analysis: Optional[dict] = None
    llm_source: str
    auto_generated: bool
    pre_departure: bool
    analysis_status: Literal["provisional", "final", "insufficient"]
    sample_size: int
    coverage_hours: Optional[float] = None
    data_quality: Optional[Literal["good", "limited", "insufficient"]] = None
    analyzed_at: Optional[str] = None


class PurchaseResponse(BaseModel):
    id: int
    route_id: int
    route_label: str
    target_date: Optional[str] = None
    flight_no: Optional[str] = None
    airline: Optional[str] = None
    purchase_price: float
    total_paid: Optional[float] = None
    currency: str
    seat_class: Optional[str] = None
    passengers: int
    purchased_at: Optional[str] = None
    purchase_type: Literal["instant", "planned"]
    notes: Optional[str] = None
    status: Literal["holding", "completed"]
    current_price: Optional[float] = None
    change_pct: Optional[float] = None
    analysis: Optional[BuyPointAnalysisResponse] = None
    created_at: Optional[str] = None


class PriceSeriesPoint(BaseModel):
    time: str
    price: float


class PurchaseDetailResponse(PurchaseResponse):
    price_series: List[PriceSeriesPoint] = Field(default_factory=list)


class ExperienceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=6000)
    route_pattern: str = Field(default="通用", min_length=1, max_length=120)
    category: Literal["timing", "route", "holiday", "general"] = "general"


class ExperienceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    content: Optional[str] = Field(default=None, min_length=1, max_length=6000)
    route_pattern: Optional[str] = Field(default=None, min_length=1, max_length=120)
    category: Optional[Literal["timing", "route", "holiday", "general"]] = None
    status: Optional[Literal["active", "archived"]] = None


class ExperienceResponse(BaseModel):
    id: int
    route_pattern: str
    category: Literal["timing", "route", "holiday", "general"]
    title: str
    content: str
    evidence_count: int
    status: Literal["active", "archived"]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
