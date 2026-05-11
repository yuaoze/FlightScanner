import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../api/client';
import type { RouteResponse } from '../types';
import { formatPrice, formatDate, daysUntilText } from '../lib/utils';

function useExpiredRoutes() {
  return useQuery({
    queryKey: ['routes', 'expired'],
    queryFn: async (): Promise<RouteResponse[]> => {
      const { data } = await apiClient.get<RouteResponse[]>('/routes', {
        params: { only_expired: true },
      });
      return data;
    },
  });
}

export function HistoryPage() {
  const { data: routes, isLoading } = useExpiredRoutes();

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-bold text-gray-900">历史监控</h1>
        <p className="text-sm text-gray-400 mt-0.5">
          已过期的监控路线，可一键再次监控
        </p>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="bg-white rounded-xl border border-gray-100 p-5 animate-pulse">
              <div className="h-4 bg-gray-100 rounded w-1/3 mb-2" />
              <div className="h-3 bg-gray-50 rounded w-1/4" />
            </div>
          ))}
        </div>
      ) : !routes || routes.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-gray-400">
          <span className="text-4xl mb-4">📭</span>
          <p>暂无历史监控</p>
        </div>
      ) : (
        <div className="space-y-3">
          {routes.map((route) => (
            <div
              key={route.id}
              className="bg-white rounded-xl border border-gray-100 p-5 hover:shadow-sm transition-shadow"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <div>
                    <h3 className="text-sm font-semibold text-gray-900">
                      {route.origin} → {route.destination}
                    </h3>
                    <p className="text-xs text-gray-400 mt-0.5">
                      {formatDate(route.target_date)}
                      {route.return_date && ` - ${formatDate(route.return_date)}`}
                      <span className="ml-2 text-red-400">
                        ({daysUntilText(route.days_until)})
                      </span>
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-base font-bold text-gray-700">
                      {formatPrice(route.latest_price)}
                    </p>
                    <p className="text-xs text-gray-400">最终价格</p>
                  </div>
                </div>
                <button
                  className="px-4 py-2 text-sm font-medium text-blue-600 border border-blue-200 rounded-lg hover:bg-blue-50 transition-colors"
                  onClick={() => {
                    // TODO: navigate to /add with prefilled data
                  }}
                >
                  再次监控
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
