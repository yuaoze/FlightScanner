import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../api/client';
import type { RouteResponse } from '../types';

interface NotificationItem {
  id: number;
  route_id: number;
  origin: string;
  destination: string;
  notified_at: string;
  price: number;
  trigger_reason: string;
  channel: string;
  status: string;
}

interface NotificationsData {
  total: number;
  items: NotificationItem[];
}

const REASON_LABELS: Record<string, string> = {
  target_hit: '达到目标价',
  below_avg: '低于均价',
  near_30d_low: '接近30天低点',
  rebound_warning: '反弹预警',
  trend_down: '持续下降',
  departure_approaching: '临近出发',
};

const CHANNEL_LABELS: Record<string, string> = {
  email: '邮件',
  telegram: 'Telegram',
  wecom: '企业微信',
  feishu: '飞书',
};

function useNotifications(routeId: number | null) {
  return useQuery({
    queryKey: ['notifications', routeId],
    queryFn: async (): Promise<NotificationsData> => {
      const params: Record<string, unknown> = { limit: 100 };
      if (routeId) params.route_id = routeId;
      const { data } = await apiClient.get<NotificationsData>('/notifications', { params });
      return data;
    },
  });
}

function useRoutesList() {
  return useQuery({
    queryKey: ['routes', 'all-for-filter'],
    queryFn: async (): Promise<RouteResponse[]> => {
      const { data } = await apiClient.get<RouteResponse[]>('/routes');
      return data;
    },
  });
}

export function AlertsPage() {
  const [filterRoute, setFilterRoute] = useState<number | null>(null);
  const { data: routes } = useRoutesList();
  const { data, isLoading } = useNotifications(filterRoute);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-bold text-gray-900">提醒记录</h1>
        <p className="text-sm text-gray-400 mt-0.5">所有通知发送记录</p>
      </div>

      {/* Filter */}
      <div className="mb-6">
        <select
          value={filterRoute ?? ''}
          onChange={(e) => setFilterRoute(e.target.value ? Number(e.target.value) : null)}
          className="px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100"
        >
          <option value="">全部航线</option>
          {routes?.map((r) => (
            <option key={r.id} value={r.id}>
              {r.origin} → {r.destination}
            </option>
          ))}
        </select>
      </div>

      {/* Timeline */}
      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="bg-white rounded-xl border border-gray-100 p-4 animate-pulse">
              <div className="h-4 bg-gray-100 rounded w-1/3 mb-2" />
              <div className="h-3 bg-gray-50 rounded w-2/3" />
            </div>
          ))}
        </div>
      ) : !data || data.items.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-gray-400">
          <span className="text-4xl mb-4">🔔</span>
          <p className="text-base">暂无通知记录</p>
          <p className="text-sm mt-1">当价格触发提醒条件时，通知记录将在此展示</p>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-gray-400 mb-2">共 {data.total} 条记录</p>
          {data.items.map((item) => (
            <div
              key={item.id}
              className="bg-white rounded-xl border border-gray-100 p-4 hover:shadow-sm transition-shadow"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div
                    className={`w-2 h-2 rounded-full ${
                      item.status === 'success' ? 'bg-green-500' : 'bg-red-400'
                    }`}
                  />
                  <div>
                    <p className="text-sm font-medium text-gray-800">
                      {item.origin} → {item.destination}
                    </p>
                    <p className="text-xs text-gray-400 mt-0.5">
                      {REASON_LABELS[item.trigger_reason] || item.trigger_reason}
                      {' · '}
                      {CHANNEL_LABELS[item.channel] || item.channel}
                      {item.status === 'failed' && (
                        <span className="text-red-400 ml-1">发送失败</span>
                      )}
                    </p>
                  </div>
                </div>
                <div className="text-right">
                  <p className="text-sm font-bold text-gray-700">¥{Math.round(item.price)}</p>
                  <p className="text-xs text-gray-400">{item.notified_at}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
