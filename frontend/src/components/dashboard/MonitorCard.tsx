import { motion } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import type { FlightBriefInfo, RouteResponse } from '../../types';
import {
  formatArrivalDayOffset,
  formatFlightDate,
} from '../../lib/arrivalDay';
import { formatPrice, formatDateRange, daysUntilText, nextScrapeCountdown } from '../../lib/utils';
import { useTicker } from '../../hooks/useTicker';
import { DecisionBadge } from './DecisionBadge';
import { MiniTrendChart } from './MiniTrendChart';

interface MonitorCardProps {
  route: RouteResponse;
}

function ArrivalDayBadge({ offset }: { offset: number }) {
  const normalizedOffset = Number.isFinite(offset) ? Math.max(0, Math.trunc(offset)) : 0;
  const style =
    normalizedOffset === 0
      ? 'border-gray-200 bg-gray-50 text-gray-500'
      : normalizedOffset === 1
        ? 'border-blue-200 bg-blue-50 text-blue-700'
        : normalizedOffset === 2
          ? 'border-amber-200 bg-amber-50 text-amber-700'
          : 'border-rose-200 bg-rose-50 text-rose-700';

  return (
    <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium ${style}`}>
      {formatArrivalDayOffset(normalizedOffset)}
    </span>
  );
}

function FlightMoment({
  date,
  time,
  airport,
  align = 'left',
  arrivalDayOffset,
  arrivalDateIsEstimated = false,
}: {
  date: string | null | undefined;
  time: string | null | undefined;
  airport: string;
  align?: 'left' | 'right';
  arrivalDayOffset?: number | null;
  arrivalDateIsEstimated?: boolean;
}) {
  const dateText = formatFlightDate(date);
  const dateTime = date && time ? `${date}T${time}` : undefined;

  return (
    <div className={`min-w-0 ${align === 'right' ? 'text-right' : 'text-left'}`}>
      <div
        className={`flex flex-wrap items-center gap-1 ${
          align === 'right' ? 'justify-end' : 'justify-start'
        }`}
      >
        <time dateTime={dateTime} className="whitespace-nowrap text-[11px] text-gray-500">
          {dateText}
        </time>
        {date && arrivalDateIsEstimated && (
          <span
            title="平台未提供明确到达日期，当前日期根据起降时刻推算"
            className="rounded bg-gray-100 px-1 py-0.5 text-[9px] text-gray-400"
          >
            推算
          </span>
        )}
      </div>
      <div
        className={`mt-0.5 flex flex-wrap items-center gap-1.5 ${
          align === 'right' ? 'justify-end' : 'justify-start'
        }`}
      >
        <span className="text-base font-semibold tabular-nums text-gray-900">
          {time || '--:--'}
        </span>
        {arrivalDayOffset !== undefined && arrivalDayOffset !== null && (
          <ArrivalDayBadge offset={arrivalDayOffset} />
        )}
      </div>
      <p className="mt-0.5 truncate text-[10px] text-gray-400" title={airport}>
        {airport}
      </p>
    </div>
  );
}

function ItineraryLeg({
  label,
  flight,
  fallbackDepartureDate,
  fallbackOrigin,
  fallbackDestination,
}: {
  label: '去程' | '回程';
  flight: FlightBriefInfo;
  fallbackDepartureDate: string | null;
  fallbackOrigin: string;
  fallbackDestination: string;
}) {
  const departureDate = flight.departure_date || fallbackDepartureDate;
  const departureAirport = flight.departure_airport_code || fallbackOrigin;
  const arrivalAirport = flight.arrival_airport_code || fallbackDestination;

  return (
    <div className="rounded-lg border border-gray-100 bg-gray-50/70 p-3">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <span className="rounded bg-white px-1.5 py-0.5 text-[10px] font-medium text-gray-500 shadow-sm">
          {label}
        </span>
        <span className="font-mono text-xs font-semibold text-gray-700">{flight.flight_no}</span>
        <span className="min-w-0 truncate text-[11px] text-gray-500">{flight.airline}</span>
        {flight.duration && (
          <span className="ml-auto shrink-0 text-[10px] text-gray-400">{flight.duration}</span>
        )}
      </div>

      <div className="mt-2 grid grid-cols-[minmax(0,1fr)_32px_minmax(0,1fr)] items-center gap-1.5">
        <FlightMoment
          date={departureDate}
          time={flight.departure_time}
          airport={departureAirport}
        />
        <div className="flex items-center" aria-hidden="true">
          <span className="h-px flex-1 bg-gray-200" />
          <span className="px-1 text-[10px] text-gray-400">›</span>
          <span className="h-px flex-1 bg-gray-200" />
        </div>
        <FlightMoment
          date={flight.arrival_date}
          time={flight.arrival_time}
          airport={arrivalAirport}
          align="right"
          arrivalDayOffset={flight.arrival_day_offset}
          arrivalDateIsEstimated={flight.arrival_date_is_estimated}
        />
      </div>
    </div>
  );
}

function MissingLeg({
  label,
  departureDate,
  flightNo,
}: {
  label: '去程' | '回程';
  departureDate: string | null;
  flightNo: string | null;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-gray-200 bg-gray-50/60 px-3 py-2.5">
      <span className="rounded bg-white px-1.5 py-0.5 text-[10px] font-medium text-gray-500">
        {label}
      </span>
      <span className="text-[11px] text-gray-500">计划 {formatFlightDate(departureDate)} 出发</span>
      {flightNo && <span className="font-mono text-[11px] text-gray-600">{flightNo}</span>}
      <span className="ml-auto text-[10px] text-gray-400">行程明细待采集</span>
    </div>
  );
}

function LatestItinerary({ route }: { route: RouteResponse }) {
  if (!route.flight_info) {
    const latestScrapeHadNoMatch = route.last_flight_status === 'filtered_out';
    const emptyText = route.latest_scraped_at || latestScrapeHadNoMatch
      ? '最新批次暂无符合当前过滤条件的航班'
      : '等待首次采集，完成后将在这里展示实际到达日期';

    return (
      <div className="rounded-lg border border-dashed border-gray-200 bg-gray-50/60 px-3 py-3">
        <p className="text-xs font-medium text-gray-600">{emptyText}</p>
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-gray-400">
          <span>去程计划 {formatFlightDate(route.target_date)}</span>
          {route.trip_type === 'roundtrip' && (
            <span>回程计划 {formatFlightDate(route.return_date)}</span>
          )}
          {route.monitoring_mode === 'flight' && route.outbound_flight_no && (
            <span className="font-mono text-gray-500">{route.outbound_flight_no}</span>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <ItineraryLeg
        label="去程"
        flight={route.flight_info}
        fallbackDepartureDate={route.target_date}
        fallbackOrigin={route.origin}
        fallbackDestination={route.destination}
      />
      {route.trip_type === 'roundtrip' &&
        (route.return_flight_info ? (
          <ItineraryLeg
            label="回程"
            flight={route.return_flight_info}
            fallbackDepartureDate={route.return_date}
            fallbackOrigin={route.destination}
            fallbackDestination={route.origin}
          />
        ) : (
          <MissingLeg
            label="回程"
            departureDate={route.return_date}
            flightNo={route.inbound_flight_no}
          />
        ))}
    </div>
  );
}

export function MonitorCard({ route }: MonitorCardProps) {
  const navigate = useNavigate();
  useTicker(60_000);

  const countdown = nextScrapeCountdown(
    route.latest_scraped_at,
    route.scrape_interval,
    route.is_active,
  );

  const priceColor =
    route.trend_direction === 'down'
      ? 'text-green-600'
      : route.trend_direction === 'up'
        ? 'text-red-500'
        : 'text-gray-900';

  const trendText =
    route.price_vs_avg_pct !== null
      ? route.price_vs_avg_pct < 0
        ? `较历史均价低 ${Math.abs(route.price_vs_avg_pct)}%`
        : `较历史均价高 ${route.price_vs_avg_pct}%`
      : '';

  return (
    <motion.article
      layout
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95 }}
      whileHover={{ y: -2 }}
      transition={{ duration: 0.2 }}
      className="flex min-w-0 flex-col rounded-xl border border-gray-100 bg-white p-4 transition-shadow hover:border-gray-200 hover:shadow-lg sm:p-5"
    >
      {/* Header */}
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-base font-semibold text-gray-900">
              {route.origin} → {route.destination}
            </h3>
            {route.monitoring_mode === 'flight' && route.outbound_flight_no && (
              <span className="inline-flex items-center rounded border border-orange-200 bg-orange-50 px-2 py-0.5 font-mono text-[11px] font-medium text-orange-700">
                {route.outbound_flight_no}
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-gray-400">
            {formatDateRange(route.target_date, route.return_date)} ({daysUntilText(route.days_until)})
            {route.trip_type === 'roundtrip' && <span className="ml-1">· 往返</span>}
            {route.seat_class && <span className="ml-1">· {route.seat_class}</span>}
          </p>
        </div>
        <div className="shrink-0">
          <DecisionBadge status={route.status} />
        </div>
      </div>

      {/* Price & Trend */}
      <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className={`text-2xl font-bold ${priceColor}`}>
          {formatPrice(route.latest_price)}
        </span>
        {trendText && <span className="text-xs text-gray-500">{trendText}</span>}
      </div>
      {route.prediction_text && (
        <p className="mb-3 text-xs leading-5 text-gray-400">{route.prediction_text}</p>
      )}

      {/* Sparkline */}
      <div className="mb-3">
        <MiniTrendChart data={route.sparkline} direction={route.trend_direction} />
      </div>

      {/* Actual dates for the cheapest itinerary in the latest matching batch. */}
      <div className="mb-4 border-t border-gray-50 pt-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <p className="text-[11px] font-medium text-gray-500">最新低价行程</p>
          {route.latest_scraped_at && (
            <span className="text-[10px] text-gray-300">
              {route.flight_info ? '最新匹配结果' : '最近采集批次'}
            </span>
          )}
        </div>
        <LatestItinerary route={route} />
      </div>

      {/* Actions */}
      <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-gray-50 pt-3">
        {countdown ? (
          <span
            className={`text-[11px] font-medium ${
              countdown.overdue ? 'text-orange-500' : 'text-gray-400'
            }`}
          >
            {countdown.label}
          </span>
        ) : (
          <span className="text-[11px] text-gray-300">已暂停</span>
        )}
        <button
          type="button"
          onClick={() => navigate(`/route/${route.id}`)}
          className="ml-auto rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-700"
        >
          查看详情
        </button>
      </div>
    </motion.article>
  );
}
