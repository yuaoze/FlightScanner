import type { ReactNode } from 'react';
import { NavLink, useLocation } from 'react-router-dom';

const NAV_ITEMS = [
  { icon: '📊', label: '监控总览', path: '/' },
  { icon: '🛰️', label: '周末雷达', path: '/radar', accent: true },
  { icon: '＋', label: '添加监控', path: '/add' },
  { icon: '📅', label: '价格日历', path: '/calendar' },
  { icon: '🔔', label: '提醒记录', path: '/alerts' },
  { icon: '📈', label: '数据分析', path: '/analytics' },
  { icon: '⏱️', label: '历史监控', path: '/history' },
  { icon: '⚙️', label: '设置中心', path: '/settings' },
];

export function Sidebar() {
  const location = useLocation();

  return (
    <aside className="w-60 h-screen bg-white border-r border-gray-100 flex flex-col fixed left-0 top-0 z-30">
      <div className="px-5 py-6 border-b border-gray-50">
        <div className="flex items-center gap-2">
          <span className="text-2xl">✈</span>
          <div>
            <h1 className="text-base font-semibold text-gray-900 leading-tight">FlightScanner</h1>
            <p className="text-xs text-gray-400">机票价格监控 & 智能预测</p>
          </div>
        </div>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-1">
        {NAV_ITEMS.map((item) => {
          const isActive = location.pathname === item.path;
          const accent = 'accent' in item && item.accent;
          return (
            <NavLink
              key={item.path}
              to={item.path}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                isActive
                  ? accent
                    ? 'bg-gradient-to-r from-purple-50 to-pink-50 text-purple-700 font-medium'
                    : 'bg-blue-50 text-blue-700 font-medium'
                  : 'text-gray-500 hover:bg-gray-50 hover:text-gray-700'
              }`}
            >
              <span className="text-base">{item.icon}</span>
              <span className="flex-1">{item.label}</span>
              {accent && !isActive && (
                <span className="text-[9px] text-purple-500 bg-purple-50 px-1.5 py-0.5 rounded font-medium">
                  NEW
                </span>
              )}
            </NavLink>
          );
        })}
      </nav>

      <div className="px-5 py-4 border-t border-gray-50">
        <p className="text-xs text-gray-300">v2.0.0</p>
      </div>
    </aside>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-slate-50">
      <Sidebar />
      <main className="flex-1 ml-60 px-8 py-6 overflow-y-auto">
        {children}
      </main>
    </div>
  );
}
