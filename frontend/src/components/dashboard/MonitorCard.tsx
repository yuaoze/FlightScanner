import { motion } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import type { RouteResponse } from '../../types';
import { DecisionBadge } from './DecisionBadge';
import { MiniTrendChart } from './MiniTrendChart';
import { formatPrice, formatDateRange, daysUntilText, nextScrapeCountdown } from '../../lib/utils';
import { useTicker } from '../../hooks/useTicker';

interface MonitorCardProps {
  route: RouteResponse;
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
    <motion.div
      layout
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95 }}
      whileHover={{ y: -2 }}
      transition={{ duration: 0.2 }}
      className="bg-white rounded-xl border border-gray-100 p-5 hover:shadow-lg hover:border-gray-200 transition-shadow cursor-pointer flex flex-col"
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="text-base font-semibold text-gray-900">
            {route.origin} → {route.destination}
          </h3>
          <p className="text-xs text-gray-400 mt-0.5">
            {formatDateRange(route.target_date, route.return_date)}
            {' '}({daysUntilText(route.days_until)})
            {route.trip_type === 'roundtrip' && <span className="ml-1">· 往返</span>}
            {route.seat_class && <span className="ml-1">· {route.seat_class}</span>}
          </p>
        </div>
        <DecisionBadge status={route.status} />
      </div>

      {/* Price & Trend */}
      <div className="flex items-baseline gap-3 mb-2">
        <span className={`text-2xl font-bold ${priceColor}`}>
          {formatPrice(route.latest_price)}
        </span>
        {trendText && (
          <span className="text-xs text-gray-500">{trendText}</span>
        )}
      </div>
      {route.prediction_text && (
        <p className="text-xs text-gray-400 mb-3">{route.prediction_text}</p>
      )}

      {/* Sparkline */}
      <div className="mb-3">
        <MiniTrendChart data={route.sparkline} direction={route.trend_direction} />
      </div>

      {/* Flight Info */}
      {route.flight_info && (
        <div className="flex items-center gap-3 text-xs text-gray-500 mb-4 pb-3 border-t border-gray-50 pt-3">
          <span className="font-medium text-gray-700">{route.flight_info.flight_no}</span>
          <span>{route.flight_info.airline}</span>
          <span>
            {route.flight_info.departure_time} → {route.flight_info.arrival_time}
          </span>
          {route.flight_info.duration && <span>{route.flight_info.duration}</span>}
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2 mt-auto pt-3">
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
        <div className="flex gap-2 ml-auto">
          <button
            onClick={() => navigate(`/route/${route.id}`)}
            className="px-3 py-1.5 bg-blue-600 text-white text-xs rounded-lg hover:bg-blue-700 transition-colors font-medium"
          >
            查看详情
          </button>
        </div>
      </div>
    </motion.div>
  );
}
