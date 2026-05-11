import { AnimatePresence } from 'framer-motion';
import type { MonitorStatus, RouteResponse } from '../../types';
import { STATUS_CONFIG } from '../../lib/constants';
import { MonitorCard } from './MonitorCard';

interface MonitorCardGridProps {
  routes: RouteResponse[];
  activeFilter: MonitorStatus | '全部';
}

const GROUP_ORDER: MonitorStatus[] = ['建议购买', '建议观望', '价格偏高'];

export function MonitorCardGrid({ routes, activeFilter }: MonitorCardGridProps) {
  const filtered =
    activeFilter === '全部' ? routes : routes.filter((r) => r.status === activeFilter);

  if (filtered.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-gray-400">
        <span className="text-4xl mb-4">✈️</span>
        <p className="text-base">暂无监控数据</p>
        <p className="text-sm mt-1">添加航线监控后，数据将在此展示</p>
      </div>
    );
  }

  if (activeFilter !== '全部') {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        <AnimatePresence mode="popLayout">
          {filtered.map((route) => (
            <MonitorCard key={route.id} route={route} />
          ))}
        </AnimatePresence>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {GROUP_ORDER.map((status) => {
        const group = filtered.filter((r) => r.status === status);
        if (group.length === 0) return null;
        const config = STATUS_CONFIG[status];
        return (
          <section key={status}>
            <div className="flex items-center gap-2 mb-4">
              <span
                className="w-2.5 h-2.5 rounded-full"
                style={{ backgroundColor: config.color }}
              />
              <h2 className="text-sm font-semibold text-gray-700">
                {config.label}
              </h2>
              <span className="text-xs text-gray-400">({group.length})</span>
              <span className="text-xs text-gray-400 ml-2">{config.description}</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              <AnimatePresence mode="popLayout">
                {group.map((route) => (
                  <MonitorCard key={route.id} route={route} />
                ))}
              </AnimatePresence>
            </div>
          </section>
        );
      })}
    </div>
  );
}
