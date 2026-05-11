"""Pydantic response schemas for the FlightScanner API."""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field


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
