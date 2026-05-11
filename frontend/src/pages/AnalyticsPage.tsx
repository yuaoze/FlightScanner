import { useQuery } from '@tanstack/react-query';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import { apiClient } from '../api/client';

interface RouteVolatility {
  route_id: number;
  origin: string;
  destination: string;
  volatility_pct: number;
  price_range_low: number;
  price_range_high: number;
  record_count: number;
}

interface AIPredictionStats {
  total_predictions: number;
  win_count: number;
  loss_count: number;
  neutral_count: number;
  pending_count: number;
  accuracy_pct: number | null;
}

interface PriceTrendPoint {
  date: string;
  price: number;
  route_label: string;
}

interface AnalyticsSummary {
  total_routes: number;
  total_price_records: number;
  active_days: number;
  volatility_ranking: RouteVolatility[];
  ai_stats: AIPredictionStats;
  recent_trends: PriceTrendPoint[];
}

function useAnalytics() {
  return useQuery({
    queryKey: ['analytics'],
    queryFn: async (): Promise<AnalyticsSummary> => {
      const { data } = await apiClient.get<AnalyticsSummary>('/analytics/summary');
      return data;
    },
    staleTime: 5 * 60 * 1000,
  });
}

function StatCard({ value, label }: { value: string | number; label: string }) {
  return (
    <div className="bg-white rounded-xl border border-gray-100 p-4">
      <p className="text-2xl font-bold text-gray-900">{value}</p>
      <p className="text-xs text-gray-500 mt-1">{label}</p>
    </div>
  );
}

export function AnalyticsPage() {
  const { data, isLoading } = useAnalytics();

  if (isLoading || !data) {
    return (
      <div>
        <h1 className="text-xl font-bold text-gray-900 mb-6">数据分析</h1>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="bg-white rounded-xl border border-gray-100 p-4 animate-pulse">
              <div className="h-6 bg-gray-100 rounded w-1/2 mb-2" />
              <div className="h-3 bg-gray-50 rounded w-2/3" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  // Transform trend data for recharts (pivot by route_label)
  const trendByDate = new Map<string, Record<string, string | number>>();
  const routeLabels = new Set<string>();
  for (const pt of data.recent_trends) {
    routeLabels.add(pt.route_label);
    const existing = trendByDate.get(pt.date) || { date: pt.date };
    existing[pt.route_label] = pt.price;
    trendByDate.set(pt.date, existing);
  }
  const chartData = Array.from(trendByDate.values());
  const colors = ['#3B82F6', '#22C55E', '#F59E0B', '#EF4444', '#8B5CF6'];

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-bold text-gray-900">数据分析</h1>
        <p className="text-sm text-gray-400 mt-0.5">全局数据洞察与 AI 预测统计</p>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <StatCard value={data.total_routes} label="活跃航线" />
        <StatCard value={data.total_price_records.toLocaleString()} label="总价格记录" />
        <StatCard value={data.active_days} label="活跃采集天数" />
        <StatCard
          value={data.ai_stats.accuracy_pct !== null ? `${data.ai_stats.accuracy_pct}%` : '--'}
          label="AI 预测准确率"
        />
      </div>

      {/* Price Trends Chart */}
      {chartData.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-100 p-5 mb-6">
          <h3 className="text-sm font-semibold text-gray-700 mb-4">近 7 天价格走势</h3>
          <div className="h-[250px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                {Array.from(routeLabels).map((label, idx) => (
                  <Line
                    key={label}
                    type="monotone"
                    dataKey={label}
                    stroke={colors[idx % colors.length]}
                    strokeWidth={2}
                    dot={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Volatility Ranking */}
        <div className="bg-white rounded-xl border border-gray-100 p-5">
          <h3 className="text-sm font-semibold text-gray-700 mb-4">价格波动率排名</h3>
          {data.volatility_ranking.length === 0 ? (
            <p className="text-xs text-gray-400">数据不足</p>
          ) : (
            <div className="space-y-3">
              {data.volatility_ranking.map((v, idx) => (
                <div key={v.route_id} className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-gray-400 w-4">{idx + 1}</span>
                    <span className="text-sm text-gray-700">
                      {v.origin} → {v.destination}
                    </span>
                  </div>
                  <div className="text-right">
                    <span className="text-sm font-bold text-orange-600">
                      {v.volatility_pct}%
                    </span>
                    <span className="text-xs text-gray-400 ml-2">
                      ¥{Math.round(v.price_range_low)}-{Math.round(v.price_range_high)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* AI Prediction Stats */}
        <div className="bg-white rounded-xl border border-gray-100 p-5">
          <h3 className="text-sm font-semibold text-gray-700 mb-4">AI 预测统计</h3>
          <div className="grid grid-cols-2 gap-4">
            <div className="text-center p-3 bg-green-50 rounded-lg">
              <p className="text-xl font-bold text-green-700">{data.ai_stats.win_count}</p>
              <p className="text-xs text-green-600">预测正确</p>
            </div>
            <div className="text-center p-3 bg-red-50 rounded-lg">
              <p className="text-xl font-bold text-red-700">{data.ai_stats.loss_count}</p>
              <p className="text-xs text-red-600">预测错误</p>
            </div>
            <div className="text-center p-3 bg-gray-50 rounded-lg">
              <p className="text-xl font-bold text-gray-700">{data.ai_stats.neutral_count}</p>
              <p className="text-xs text-gray-600">中性结果</p>
            </div>
            <div className="text-center p-3 bg-blue-50 rounded-lg">
              <p className="text-xl font-bold text-blue-700">{data.ai_stats.pending_count}</p>
              <p className="text-xs text-blue-600">待验证</p>
            </div>
          </div>
          <div className="mt-4 pt-3 border-t border-gray-50 text-center">
            <p className="text-xs text-gray-400">
              总预测 {data.ai_stats.total_predictions} 次
              {data.ai_stats.accuracy_pct !== null && (
                <span className="ml-2 font-medium text-gray-600">
                  准确率 {data.ai_stats.accuracy_pct}%
                </span>
              )}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
