import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../api/client';
import type { RouteResponse } from '../types';

interface CalendarDayPrice {
  date: string;
  min_price: number;
  max_price: number;
  avg_price: number;
  record_count: number;
}

interface CalendarData {
  route_id: number;
  origin: string;
  destination: string;
  days: CalendarDayPrice[];
}

function useRoutesList() {
  return useQuery({
    queryKey: ['routes'],
    queryFn: async (): Promise<RouteResponse[]> => {
      const { data } = await apiClient.get<RouteResponse[]>('/routes');
      return data;
    },
  });
}

function useCalendar(routeId: number | null, month: string) {
  return useQuery({
    queryKey: ['calendar', routeId, month],
    queryFn: async (): Promise<CalendarData> => {
      const { data } = await apiClient.get<CalendarData>(
        `/routes/${routeId}/calendar`,
        { params: { month } }
      );
      return data;
    },
    enabled: routeId !== null,
  });
}

function getPriceColor(price: number, min: number, max: number): string {
  if (max === min) return 'bg-blue-50 text-blue-700';
  const ratio = (price - min) / (max - min);
  if (ratio < 0.33) return 'bg-green-50 text-green-700';
  if (ratio < 0.66) return 'bg-amber-50 text-amber-700';
  return 'bg-red-50 text-red-700';
}

export function CalendarPage() {
  const { data: routes } = useRoutesList();
  const [selectedRoute, setSelectedRoute] = useState<number | null>(null);

  const today = new Date();
  const [month, setMonth] = useState(
    `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`
  );

  const { data: calendar, isLoading } = useCalendar(selectedRoute, month);

  // Compute global min/max for color scale
  const allPrices = calendar?.days.map((d) => d.min_price) ?? [];
  const globalMin = allPrices.length > 0 ? Math.min(...allPrices) : 0;
  const globalMax = allPrices.length > 0 ? Math.max(...allPrices) : 0;

  // Build day map for quick lookup
  const dayMap = new Map(calendar?.days.map((d) => [d.date, d]));

  // Generate calendar grid
  const [yearNum, monthNum] = month.split('-').map(Number);
  const firstDay = new Date(yearNum, monthNum - 1, 1).getDay();
  const daysInMonth = new Date(yearNum, monthNum, 0).getDate();

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-bold text-gray-900">价格日历</h1>
        <p className="text-sm text-gray-400 mt-0.5">以日历视图查看每天的最低价格</p>
      </div>

      {/* Controls */}
      <div className="flex items-center gap-4 mb-6">
        <select
          value={selectedRoute ?? ''}
          onChange={(e) => setSelectedRoute(e.target.value ? Number(e.target.value) : null)}
          className="px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100"
        >
          <option value="">选择航线</option>
          {routes?.map((r) => (
            <option key={r.id} value={r.id}>
              {r.origin} → {r.destination} ({r.target_date})
            </option>
          ))}
        </select>

        <input
          type="month"
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          className="px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100"
        />
      </div>

      {/* Calendar Grid */}
      {!selectedRoute ? (
        <div className="flex flex-col items-center justify-center py-20 text-gray-400">
          <span className="text-4xl mb-4">📅</span>
          <p>请先选择一条航线</p>
        </div>
      ) : isLoading ? (
        <div className="bg-white rounded-xl border border-gray-100 p-6 animate-pulse">
          <div className="grid grid-cols-7 gap-2">
            {Array.from({ length: 35 }).map((_, i) => (
              <div key={i} className="h-16 bg-gray-50 rounded" />
            ))}
          </div>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-100 p-6">
          {calendar && (
            <p className="text-sm font-medium text-gray-700 mb-4">
              {calendar.origin} → {calendar.destination}
            </p>
          )}

          {/* Week header */}
          <div className="grid grid-cols-7 gap-2 mb-2">
            {['日', '一', '二', '三', '四', '五', '六'].map((d) => (
              <div key={d} className="text-center text-xs text-gray-400 font-medium py-1">
                {d}
              </div>
            ))}
          </div>

          {/* Day cells */}
          <div className="grid grid-cols-7 gap-2">
            {/* Empty cells for offset */}
            {Array.from({ length: firstDay }).map((_, i) => (
              <div key={`empty-${i}`} className="h-18" />
            ))}

            {Array.from({ length: daysInMonth }).map((_, i) => {
              const dayNum = i + 1;
              const dateStr = `${month}-${String(dayNum).padStart(2, '0')}`;
              const dayData = dayMap.get(dateStr);

              return (
                <div
                  key={dayNum}
                  className={`h-18 rounded-lg border p-1.5 flex flex-col items-center justify-center ${
                    dayData
                      ? `${getPriceColor(dayData.min_price, globalMin, globalMax)} border-transparent`
                      : 'border-gray-100 bg-gray-25'
                  }`}
                >
                  <span className="text-xs text-gray-500">{dayNum}</span>
                  {dayData && (
                    <span className="text-xs font-bold mt-0.5">
                      ¥{Math.round(dayData.min_price)}
                    </span>
                  )}
                </div>
              );
            })}
          </div>

          {/* Legend */}
          {allPrices.length > 0 && (
            <div className="flex items-center gap-4 mt-4 pt-4 border-t border-gray-50">
              <span className="text-xs text-gray-400">价格区间：</span>
              <div className="flex items-center gap-1">
                <span className="w-3 h-3 rounded bg-green-100" />
                <span className="text-xs text-gray-500">低价</span>
              </div>
              <div className="flex items-center gap-1">
                <span className="w-3 h-3 rounded bg-amber-100" />
                <span className="text-xs text-gray-500">中等</span>
              </div>
              <div className="flex items-center gap-1">
                <span className="w-3 h-3 rounded bg-red-100" />
                <span className="text-xs text-gray-500">偏高</span>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
