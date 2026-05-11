import type { StatsResponse } from '../../types';

interface KpiCardProps {
  icon: string;
  value: string | number;
  label: string;
  color: string;
}

function KpiCard({ icon, value, label, color }: KpiCardProps) {
  return (
    <div className="bg-white rounded-xl border border-gray-100 p-4 hover:shadow-md transition-shadow">
      <div className="flex items-center gap-3">
        <div
          className="w-10 h-10 rounded-lg flex items-center justify-center text-lg"
          style={{ backgroundColor: `${color}15` }}
        >
          {icon}
        </div>
        <div>
          <p className="text-2xl font-bold text-gray-900">{value}</p>
          <p className="text-xs text-gray-500">{label}</p>
        </div>
      </div>
    </div>
  );
}

interface KpiCardsProps {
  stats: StatsResponse | undefined;
  isLoading: boolean;
}

export function KpiCards({ stats, isLoading }: KpiCardsProps) {
  if (isLoading || !stats) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4 mb-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="bg-white rounded-xl border border-gray-100 p-4 animate-pulse">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-gray-100" />
              <div className="space-y-2">
                <div className="w-8 h-6 bg-gray-100 rounded" />
                <div className="w-16 h-3 bg-gray-50 rounded" />
              </div>
            </div>
          </div>
        ))}
      </div>
    );
  }

  const cards: KpiCardProps[] = [
    { icon: '📊', value: stats.total_monitors, label: '个活跃监控', color: '#3B82F6' },
    { icon: '✓', value: stats.buy_count, label: '个低价航线', color: '#22C55E' },
    { icon: '◷', value: stats.hold_count, label: '个等待中', color: '#F59E0B' },
    { icon: '↑', value: stats.expensive_count, label: '个不推荐', color: '#EF4444' },
    {
      icon: '📉',
      value: stats.average_drop_pct ? `${stats.average_drop_pct}%` : '--',
      label: '较历史均价',
      color: '#22C55E',
    },
    { icon: '🔔', value: stats.alert_count, label: '个已设置提醒', color: '#3B82F6' },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4 mb-6">
      {cards.map((card) => (
        <KpiCard key={card.label} {...card} />
      ))}
    </div>
  );
}
