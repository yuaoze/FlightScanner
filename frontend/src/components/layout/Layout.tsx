import type { ReactNode } from 'react';
import { NavLink, useLocation } from 'react-router-dom';

const NAV_ITEMS = [
  { icon: '📊', label: '监控总览', path: '/' },
  { icon: '💰', label: '买入记录', path: '/purchases' },
  { icon: '🛰️', label: '周末雷达', path: '/radar', accent: true },
  { icon: '📅', label: '价格日历', path: '/calendar' },
  { icon: '🔔', label: '提醒记录', path: '/alerts' },
  { icon: '📈', label: '数据分析', path: '/analytics' },
  { icon: '⏱️', label: '历史监控', path: '/history' },
  { icon: '⚙️', label: '设置中心', path: '/settings' },
];

export function Sidebar() {
  const location = useLocation();

  return (
    <aside className="fixed left-0 top-0 z-30 flex h-screen w-60 flex-col border-r border-gray-100 bg-white">
      <div className="border-b border-gray-50 px-5 py-6">
        <div className="flex items-center gap-2">
          <span className="text-2xl">✈</span>
          <div>
            <h1 className="text-base font-semibold leading-tight text-gray-900">FlightScanner</h1>
            <p className="text-xs text-gray-400">机票价格监控 & 智能预测</p>
          </div>
        </div>
      </div>

      <nav aria-label="FlightScanner 主导航" className="flex-1 space-y-1 px-3 py-4">
        {NAV_ITEMS.map((item) => {
          const isActive = (location.pathname.replace(/\/+$/, '') || '/') === item.path;
          const accent = 'accent' in item && item.accent;
          return (
            <NavLink
              key={item.path}
              to={item.path}
              className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${
                isActive
                  ? accent
                    ? 'bg-gradient-to-r from-purple-50 to-pink-50 font-medium text-purple-700'
                    : 'bg-blue-50 font-medium text-blue-700'
                  : 'text-gray-500 hover:bg-gray-50 hover:text-gray-700'
              }`}
            >
              <span className="text-base">{item.icon}</span>
              <span className="flex-1">{item.label}</span>
              {accent && !isActive && (
                <span className="rounded bg-purple-50 px-1.5 py-0.5 text-[9px] font-medium text-purple-500">
                  NEW
                </span>
              )}
            </NavLink>
          );
        })}
      </nav>

      <div className="border-t border-gray-50 px-5 py-4">
        <p className="text-xs text-gray-300">v2.2.1</p>
      </div>
    </aside>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-slate-50">
      <Sidebar />
      <main className="ml-60 flex-1 overflow-y-auto px-8 py-6">
        {children}
      </main>
    </div>
  );
}
