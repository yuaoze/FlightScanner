import { useNavigate } from 'react-router-dom';
import type { RouteDetailResponse } from '../../types';
import { DecisionBadge } from '../dashboard/DecisionBadge';
import { formatPrice, formatDateRange, daysUntilText } from '../../lib/utils';

interface Props {
  route: RouteDetailResponse;
}

export function RouteDetailHeader({ route }: Props) {
  const navigate = useNavigate();

  const priceColor =
    route.trend_direction === 'down'
      ? 'text-green-600'
      : route.trend_direction === 'up'
        ? 'text-red-500'
        : 'text-gray-900';

  const trendArrow =
    route.trend_direction === 'down' ? '↓' : route.trend_direction === 'up' ? '↑' : '→';

  return (
    <div className="bg-white rounded-xl border border-gray-100 p-5 mb-4">
      <div className="flex items-center justify-between mb-3">
        <button
          onClick={() => navigate(-1)}
          className="text-sm text-gray-400 hover:text-gray-700 transition-colors"
        >
          ← 返回
        </button>
        <DecisionBadge status={route.status} />
      </div>

      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-900">
            {route.origin} → {route.destination}
          </h1>
          <p className="text-sm text-gray-400 mt-1">
            {formatDateRange(route.target_date, route.return_date)}
            {' '}({daysUntilText(route.days_until)})
            {route.trip_type === 'roundtrip' && <span className="ml-1">· 往返</span>}
            {route.seat_class && <span className="ml-1">· {route.seat_class}</span>}
            <span className="ml-1">· 目标 {formatPrice(route.target_price)}</span>
          </p>
        </div>
        <div className="text-right">
          <div className="flex items-baseline gap-1.5">
            <span className={`text-2xl font-bold ${priceColor}`}>
              {formatPrice(route.latest_price)}
            </span>
            <span className={`text-sm ${priceColor}`}>{trendArrow}</span>
          </div>
          {route.price_vs_avg_pct !== null && (
            <p className="text-xs text-gray-500 mt-0.5">
              {route.price_vs_avg_pct < 0 ? '低于' : '高于'}均价{' '}
              {Math.abs(route.price_vs_avg_pct)}%
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
