import { useState } from 'react';
import type { RouteDetailResponse } from '../../types';
import { useRouteBatches, useRouteFlights } from '../../hooks/useRouteDetail';

interface Props {
  routeId: number;
  route: RouteDetailResponse;
}

const SOURCE_LABELS: Record<string, string> = {
  qunar: '去哪儿',
  ctrip: '携程',
  trip: 'Trip',
};

const SOURCE_COLORS: Record<string, string> = {
  qunar: 'bg-amber-50 text-amber-700',
  ctrip: 'bg-blue-50 text-blue-700',
  trip: 'bg-green-50 text-green-700',
};

export function FlightsTab({ routeId, route }: Props) {
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null);
  const [topN, setTopN] = useState(10);

  const { data: batchesData, isLoading: batchesLoading } = useRouteBatches(routeId, 30);
  const { data: flightsData, isLoading: flightsLoading } = useRouteFlights(
    routeId,
    selectedBatchId,
    topN
  );

  const batches = batchesData?.batches ?? [];

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="bg-white rounded-xl border border-gray-100 p-5">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-gray-500 block mb-1.5">采集批次</label>
            <select
              value={selectedBatchId ?? ''}
              onChange={(e) => setSelectedBatchId(e.target.value || null)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100"
              disabled={batchesLoading}
            >
              <option value="">最新批次</option>
              {batches.map((b) => (
                <option key={`${b.batch_id}-${b.source}`} value={b.batch_id}>
                  {b.scraped_at} · {SOURCE_LABELS[b.source] || b.source} · ¥{Math.round(b.min_price)}起 ({b.flight_count}班)
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="text-xs text-gray-500 block mb-1.5">展示前 N 条最便宜</label>
            <div className="flex gap-2">
              {[5, 10, 20, 50].map((n) => (
                <button
                  key={n}
                  onClick={() => setTopN(n)}
                  className={`flex-1 px-3 py-2 text-sm rounded-lg border transition-colors ${
                    topN === n
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
                  }`}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>
        </div>

        {flightsData?.scraped_at && (
          <p className="text-xs text-gray-400 mt-3">
            采集时间：{flightsData.scraped_at} · 共返回 {flightsData.flights.length} 条
          </p>
        )}
      </div>

      {/* Flight list */}
      {flightsLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="bg-white rounded-xl border border-gray-100 p-4 h-24 animate-pulse">
              <div className="h-full bg-gray-50 rounded" />
            </div>
          ))}
        </div>
      ) : !flightsData || flightsData.flights.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-100 p-5">
          <div className="flex flex-col items-center justify-center py-12 text-gray-400">
            <span className="text-3xl mb-3">✈️</span>
            <p className="text-sm">暂无航班数据</p>
            <p className="text-xs mt-1">等待下次采集后将显示最新航班信息</p>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          {flightsData.flights.map((f, idx) => (
            <div
              key={`${f.flight_no}-${idx}`}
              className="bg-white rounded-xl border border-gray-100 p-4 hover:shadow-sm transition-shadow"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3 flex-1">
                  <span className="text-xs text-gray-400 w-6">#{idx + 1}</span>

                  {/* Outbound */}
                  <div className="flex items-center gap-3 flex-1 min-w-0">
                    <div className="text-center">
                      <p className="text-base font-bold text-gray-900">{f.departure_time}</p>
                      <p className="text-[10px] text-gray-500">
                        {f.departure_airport_code || route.origin}
                      </p>
                    </div>
                    <div className="flex-1 flex flex-col items-center min-w-0">
                      <p className="text-[10px] text-gray-400">{f.duration || '--'}</p>
                      <div className="w-full flex items-center my-0.5">
                        <div className="flex-1 h-px bg-gray-200" />
                        <span className="mx-1 text-xs text-gray-400">✈</span>
                        <div className="flex-1 h-px bg-gray-200" />
                      </div>
                      <p className="text-[10px] text-gray-600 truncate w-full text-center">
                        {f.flight_no} · {f.airline}
                      </p>
                    </div>
                    <div className="text-center">
                      <p className="text-base font-bold text-gray-900">{f.arrival_time}</p>
                      <p className="text-[10px] text-gray-500">
                        {f.arrival_airport_code || route.destination}
                      </p>
                    </div>
                  </div>
                </div>

                {/* Price + Source */}
                <div className="text-right ml-4 flex-shrink-0">
                  <p className="text-lg font-bold text-gray-900">¥{Math.round(f.price)}</p>
                  <div className="flex items-center gap-1.5 justify-end mt-1">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded ${SOURCE_COLORS[f.source] || 'bg-gray-100 text-gray-600'}`}>
                      {SOURCE_LABELS[f.source] || f.source}
                    </span>
                    <span className="text-[10px] text-gray-400">{f.seat_class}</span>
                  </div>
                </div>
              </div>

              {/* Return leg for roundtrip */}
              {f.return_flight_no && f.return_flight_no !== 'VIRTUAL_RETURN' && (
                <div className="mt-2 pt-2 border-t border-gray-50 flex items-center gap-2 text-xs text-gray-500">
                  <span className="text-gray-400">回程</span>
                  <span className="font-medium text-gray-700">{f.return_flight_no}</span>
                  <span>
                    {f.return_departure_time} → {f.return_arrival_time}
                  </span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
