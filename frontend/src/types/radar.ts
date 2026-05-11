export interface AIBrief {
  headline: string | null;
  body: string | null;
  visa_note: string | null;
  tags: string[];
}

export interface WeekendDealItem {
  id: number;
  destination: string;
  emoji: string;
  gradient: string;
  image_url: string | null;
  is_international: boolean;
  visa_status: string | null;
  visa_label: string | null;
  outbound_date: string;
  return_date: string;
  outbound_flight_no: string | null;
  outbound_airline: string | null;
  outbound_dep_time: string | null;
  outbound_arr_time: string | null;
  outbound_dep_airport: string | null;
  return_flight_no: string | null;
  return_airline: string | null;
  return_dep_time: string | null;
  return_arr_time: string | null;
  total_price: number;
  historical_avg: number | null;
  beat_pct: number | null;
  source: string;
  scan_type: string;
  scanned_at: string | null;
  red_eye: boolean;
  ai_brief: AIBrief | null;
}

export interface WeekendOption {
  outbound_date: string;
  return_date: string;
  label: string;
  deal_count: number;
}

export interface RadarDealsResponse {
  deals: WeekendDealItem[];
  weekends: WeekendOption[];
  total: number;
  latest_scan_at: string | null;
}
