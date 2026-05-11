import { useState } from 'react';
import type { MonitorStatus } from '../types';
import { useRoutes } from '../hooks/useRoutes';
import { useStats } from '../hooks/useStats';
import { KpiCards } from '../components/dashboard/KpiCards';
import { FilterTabs } from '../components/dashboard/FilterTabs';
import { MonitorCardGrid } from '../components/dashboard/MonitorCardGrid';

export function DashboardPage() {
  const [activeTab, setActiveTab] = useState<MonitorStatus | '全部'>('全部');
  const { data: routes, isLoading: routesLoading } = useRoutes();
  const { data: stats, isLoading: statsLoading } = useStats();

  const counts = {
    total: routes?.length ?? 0,
    buy: routes?.filter((r) => r.status === '建议购买').length ?? 0,
    hold: routes?.filter((r) => r.status === '建议观望').length ?? 0,
    expensive: routes?.filter((r) => r.status === '价格偏高').length ?? 0,
  };

  return (
    <div>
      {/* Page Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-gray-900">监控总览</h1>
          <p className="text-sm text-gray-400 mt-0.5">
            共 {counts.total} 个活跃监控
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 transition-colors font-medium">
            ＋ 添加监控
          </button>
        </div>
      </div>

      {/* KPI Cards */}
      <KpiCards stats={stats} isLoading={statsLoading} />

      {/* Filter Tabs */}
      <FilterTabs activeTab={activeTab} counts={counts} onTabChange={setActiveTab} />

      {/* Monitor Cards */}
      {routesLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="bg-white rounded-xl border border-gray-100 p-5 animate-pulse">
              <div className="h-4 bg-gray-100 rounded w-2/3 mb-3" />
              <div className="h-3 bg-gray-50 rounded w-1/2 mb-4" />
              <div className="h-8 bg-gray-100 rounded w-1/3 mb-3" />
              <div className="h-[60px] bg-gray-50 rounded mb-3" />
              <div className="h-3 bg-gray-50 rounded w-3/4 mb-4" />
              <div className="flex gap-2">
                <div className="h-9 bg-gray-100 rounded flex-1" />
                <div className="h-9 bg-gray-50 rounded flex-1" />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <MonitorCardGrid routes={routes ?? []} activeFilter={activeTab} />
      )}
    </div>
  );
}
