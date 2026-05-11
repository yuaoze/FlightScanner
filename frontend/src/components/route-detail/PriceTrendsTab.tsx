import { useState } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  CartesianGrid,
  Legend,
} from 'recharts';
import { useRouteHistory, useRouteCalendar } from '../../hooks/useRouteDetail';
import { formatPrice } from '../../lib/utils';

interface Props {
  routeId: number;
  targetPrice: number;
}

type Granularity = 'hour' | 'day';

const SOURCE_COLORS: Record<string, string> = {
  qunar: '#F59E0B',
  ctrip: '#3B82F6',
  trip: '#22C55E',
};

const SOURCE_LABELS: Record<string, string> = {
  qunar: '去哪儿',
  ctrip: '携程',
  trip: 'Trip',
};

function StatCard({ value, label }: { value: string; label: string }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3 text-center">
      <p className="text-base font-bold text-gray-800">{value}</p>
      <p className="text-xs text-gray-500 mt-0.5">{label}</p>
    </div>
  );
}

export function PriceTrendsTab({ routeId, targetPrice }: Props) {
  const [granularity, setGranularity] = useState<Granularity>('hour');
  const { data: history, isLoading } = useRouteHistory(routeId, 30);

  const today = new Date();
  const month = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`;
  const { data: calendar } = useRouteCalendar(routeId, month);

  if (isLoading || !history) {
    return (
      <div className="bg-white rounded-xl border border-gray-100 p-5 h-[320px] animate-pulse">
        <div className="h-full bg-gray-50 rounded" />
      </div>
    );
  }

  // Bucket points by (timestamp_key, source) → min price
  const sources = Array.from(new Set(history.points.map((p) => p.source)));
  const bucketMap = new Map<string, Record<string, number | string>>();

  for (const pt of history.points) {
    const key =
      granularity === 'hour'
        ? pt.date.slice(5, 16).replace('T', ' ') // MM-DD HH:MM
        : pt.date.slice(5, 10); // MM-DD
    const existing = bucketMap.get(key) || { time: key };
    const prev = existing[pt.source];
    if (typeof prev !== 'number' || pt.price < prev) {
      existing[pt.source] = pt.price;
    }
    bucketMap.set(key, existing);
  }

  const chartData = Array.from(bucketMap.values()).sort((a, b) =>
    String(a.time).localeCompare(String(b.time))
  );

  // Aggregate stats across all points
  const allPrices = history.points.map((p) => p.price);
  const avg = allPrices.length > 0 ? allPrices.reduce((a, b) => a + b, 0) / allPrices.length : 0;
  const min = allPrices.length > 0 ? Math.min(...allPrices) : 0;
  const max = allPrices.length > 0 ? Math.max(...allPrices) : 0;
  const volatility = avg > 0 ? ((max - min) / avg) * 100 : 0;

  return (
    <div className="space-y-4">
      {/* Main Chart */}
      <div className="bg-white rounded-xl border border-gray-100 p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-gray-700">
            {granularity === 'hour' ? '近 30 天小时级价格走势' : '近 30 天每日价格走势'}
          </h3>
          <div className="flex bg-gray-100 rounded-lg p-0.5">
            <button
              onClick={() => setGranularity('hour')}
              className={`px-2.5 py-1 text-xs font-medium rounded transition-colors ${
                granularity === 'hour' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500'
              }`}
            >
              小时
            </button>
            <button
              onClick={() => setGranularity('day')}
              className={`px-2.5 py-1 text-xs font-medium rounded transition-colors ${
                granularity === 'day' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500'
              }`}
            >
              天
            </button>
          </div>
        </div>
        <div className="h-[280px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 5, right: 10, bottom: 5, left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
              <XAxis
                dataKey="time"
                tick={{ fontSize: 10 }}
                minTickGap={20}
              />
              <YAxis
                tick={{ fontSize: 11 }}
                tickFormatter={(v: number) => `¥${v}`}
                domain={['dataMin - 100', 'dataMax + 100']}
              />
              <Tooltip
                contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #e5e7eb' }}
                formatter={(value, name) => [
                  `¥${Math.round(Number(value))}`,
                  SOURCE_LABELS[String(name)] || String(name),
                ]}
                labelFormatter={(label) => `时间: ${label}`}
              />
              <Legend
                wrapperStyle={{ fontSize: 12 }}
                formatter={(value) => SOURCE_LABELS[value] || value}
              />
              <ReferenceLine
                y={targetPrice}
                stroke="#3B82F6"
                strokeDasharray="4 4"
                label={{
                  value: `目标 ¥${targetPrice}`,
                  position: 'right',
                  fontSize: 10,
                  fill: '#3B82F6',
                }}
              />
              {sources.map((src) => (
                <Line
                  key={src}
                  type="monotone"
                  dataKey={src}
                  stroke={SOURCE_COLORS[src] || '#6B7280'}
                  strokeWidth={2}
                  dot={false}
                  connectNulls
                  name={src}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard value={formatPrice(avg)} label="平均价格" />
        <StatCard value={formatPrice(min)} label="最低价" />
        <StatCard value={formatPrice(max)} label="最高价" />
        <StatCard value={`${volatility.toFixed(1)}%`} label="波动率" />
      </div>

      {/* Mini Calendar */}
      {calendar && calendar.days.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-100 p-5">
          <h3 className="text-sm font-semibold text-gray-700 mb-3">本月每日最低价</h3>
          <div className="grid grid-cols-7 gap-1.5">
            {['日', '一', '二', '三', '四', '五', '六'].map((d) => (
              <div key={d} className="text-center text-xs text-gray-400 font-medium py-1">
                {d}
              </div>
            ))}
            {(() => {
              const [y, m] = month.split('-').map(Number);
              const firstDay = new Date(y, m - 1, 1).getDay();
              const daysInMonth = new Date(y, m, 0).getDate();
              const dayMap = new Map(calendar.days.map((d) => [d.date, d]));
              const calMin = Math.min(...calendar.days.map((d) => d.min_price));
              const calMax = Math.max(...calendar.days.map((d) => d.min_price));

              const cells = [];
              for (let i = 0; i < firstDay; i++) {
                cells.push(<div key={`e-${i}`} className="h-10" />);
              }
              for (let day = 1; day <= daysInMonth; day++) {
                const dateStr = `${month}-${String(day).padStart(2, '0')}`;
                const dayData = dayMap.get(dateStr);
                let bgColor = 'bg-gray-25';
                if (dayData && calMax > calMin) {
                  const ratio = (dayData.min_price - calMin) / (calMax - calMin);
                  bgColor =
                    ratio < 0.33 ? 'bg-green-50' : ratio < 0.66 ? 'bg-amber-50' : 'bg-red-50';
                } else if (dayData) {
                  bgColor = 'bg-blue-50';
                }
                cells.push(
                  <div
                    key={day}
                    className={`h-10 rounded flex flex-col items-center justify-center ${bgColor}`}
                  >
                    <span className="text-[10px] text-gray-500">{day}</span>
                    {dayData && (
                      <span className="text-[10px] font-bold text-gray-700">
                        ¥{Math.round(dayData.min_price)}
                      </span>
                    )}
                  </div>
                );
              }
              return cells;
            })()}
          </div>
        </div>
      )}
    </div>
  );
}
