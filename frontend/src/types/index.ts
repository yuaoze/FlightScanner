export interface SparklinePoint {
  date: string;
  price: number;
}

export interface FlightBriefInfo {
  flight_no: string;
  airline: string;
  departure_time: string;
  arrival_time: string;
  duration: string | null;
  departure_airport_code: string | null;
  arrival_airport_code: string | null;
}

export type MonitorStatus = '建议购买' | '建议观望' | '价格偏高';

export interface RouteResponse {
  id: number;
  origin: string;
  destination: string;
  target_date: string;
  return_date: string | null;
  trip_type: string;
  target_price: number;
  latest_price: number | null;
  status: MonitorStatus;
  trend_direction: string;
  trend_confidence: number;
  trend_recommendation: string;
  price_vs_avg_pct: number | null;
  prediction_text: string;
  sparkline: SparklinePoint[];
  flight_info: FlightBriefInfo | null;
  days_until: number;
  has_alert: boolean;
  is_active: boolean;
  monitoring_mode: string;
  outbound_flight_no: string | null;
  inbound_flight_no: string | null;
  seat_class: string | null;
  last_flight_status: string | null;
  latest_scraped_at: string | null;
  scrape_interval: number;
}

export interface StatsResponse {
  total_monitors: number;
  buy_count: number;
  hold_count: number;
  expensive_count: number;
  average_drop_pct: number | null;
  alert_count: number;
}

// ── Route Detail types ────────────────────────────────────────────────────

export interface RouteDetailResponse extends RouteResponse {
  scrape_interval: number;
  dep_airport_code: string | null;
  arr_airport_code: string | null;
  dep_time_from: string | null;
  dep_time_to: string | null;
  arr_time_from: string | null;
  arr_time_to: string | null;
  ret_dep_time_from: string | null;
  ret_dep_time_to: string | null;
  ret_arr_time_from: string | null;
  ret_arr_time_to: string | null;
  created_at: string | null;
}

export interface PriceHistoryPoint {
  date: string;
  price: number;
  source: string;
}

export interface PriceHistoryResponse {
  route_id: number;
  points: PriceHistoryPoint[];
}

export interface PredictionLogItem {
  id: number;
  predicted_at: string;
  price_at_prediction: number;
  recommended_action: string;
  reason: string | null;
  confidence: number | null;
  llm_source: string;
  outcome_status: string;
  actual_min_price: number | null;
  pain_index: number | null;
}

export interface RoutePredictionsResponse {
  route_id: number;
  predictions: PredictionLogItem[];
  win_rate: number | null;
  total: number;
}

export interface CalendarDayPrice {
  date: string;
  min_price: number;
  max_price: number;
  avg_price: number;
  record_count: number;
}

export interface CalendarData {
  route_id: number;
  origin: string;
  destination: string;
  days: CalendarDayPrice[];
}

// ── Batches & Flight list types ───────────────────────────────────────────

export interface BatchInfo {
  batch_id: string;
  source: string;
  scraped_at: string;
  flight_count: number;
  min_price: number;
}

export interface RouteBatchesResponse {
  route_id: number;
  batches: BatchInfo[];
}

export interface FlightListItem {
  flight_no: string;
  airline: string;
  departure_time: string;
  arrival_time: string;
  duration: string | null;
  departure_airport_code: string | null;
  arrival_airport_code: string | null;
  price: number;
  seat_class: string;
  available_seats: number | null;
  source: string;
  batch_id: string | null;
  return_flight_no: string | null;
  return_departure_time: string | null;
  return_arrival_time: string | null;
}

export interface RouteFlightsResponse {
  route_id: number;
  batch_id: string | null;
  scraped_at: string | null;
  flights: FlightListItem[];
}

// ── Purchase tracking types（v2.2.0 买入闭环）──────────────────────────────

export type PlanStatus = 'pending' | 'triggered' | 'converted' | 'cancelled' | 'expired';
export type PurchaseStatus = 'holding' | 'completed';
export type BuyVerdict = 'excellent' | 'good' | 'fair' | 'poor';
export type BuyPointAnalysisStatus = 'provisional' | 'final' | 'insufficient';

export interface BuyPointAiAnalysis {
  verdict_comment?: string;
  timing_assessment?: string;
  key_lessons?: string[];
}

export interface BuyPointAnalysis {
  post_min_price: number | null;
  post_max_price: number | null;
  final_price: number | null;
  regret_cost: number | null;
  savings_vs_final: number | null;
  verdict: BuyVerdict | null;
  ai_analysis: BuyPointAiAnalysis | null;
  llm_source: string;
  auto_generated: boolean;
  pre_departure: boolean;
  analysis_status: BuyPointAnalysisStatus;
  sample_size: number;
  coverage_hours: number | null;
  data_quality: string | null;
  analyzed_at: string | null;
}

export interface PurchaseRecordItem {
  id: number;
  route_id: number;
  route_label: string;
  target_date: string | null;
  flight_no: string | null;
  airline: string | null;
  purchase_price: number;
  total_paid: number | null;
  currency: string;
  seat_class: string | null;
  passengers: number;
  purchased_at: string | null;
  purchase_type: 'instant' | 'planned';
  notes: string | null;
  status: PurchaseStatus;
  current_price: number | null;
  change_pct: number | null;
  analysis: BuyPointAnalysis | null;
  created_at: string | null;
}

export interface PurchaseSeriesPoint {
  time: string;
  price: number;
}

export interface PurchaseDetail extends PurchaseRecordItem {
  price_series: PurchaseSeriesPoint[];
}

export interface BuyPlan {
  id: number;
  route_id: number;
  route_label: string;
  plan_price: number | null;
  plan_execute_by: string | null;
  status: PlanStatus;
  triggered_at: string | null;
  trigger_price: number | null;
  currency?: string;
  trigger_reason: 'price_hit' | 'deadline' | null;
  created_at: string | null;
}

export interface ExperienceEntry {
  id: number;
  route_pattern: string;
  category: string;
  title: string;
  content: string;
  evidence_count: number;
  status: 'active' | 'archived';
  created_at: string | null;
  updated_at: string | null;
}
