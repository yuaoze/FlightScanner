import { motion } from 'framer-motion';
import type { MonitorStatus } from '../../types';

interface FilterTabsProps {
  activeTab: MonitorStatus | '全部';
  counts: { total: number; buy: number; hold: number; expensive: number };
  onTabChange: (tab: MonitorStatus | '全部') => void;
}

const TABS: { key: MonitorStatus | '全部'; label: string; countKey: keyof FilterTabsProps['counts'] }[] = [
  { key: '全部', label: '全部', countKey: 'total' },
  { key: '建议购买', label: '建议购买', countKey: 'buy' },
  { key: '建议观望', label: '建议观望', countKey: 'hold' },
  { key: '价格偏高', label: '价格偏高', countKey: 'expensive' },
];

export function FilterTabs({ activeTab, counts, onTabChange }: FilterTabsProps) {
  return (
    <div className="flex items-center gap-1 mb-6 bg-white rounded-xl border border-gray-100 p-1.5 w-fit">
      {TABS.map((tab) => {
        const isActive = activeTab === tab.key;
        return (
          <button
            key={tab.key}
            onClick={() => onTabChange(tab.key)}
            className={`relative px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              isActive ? 'text-blue-700' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            {isActive && (
              <motion.div
                layoutId="activeTab"
                className="absolute inset-0 bg-blue-50 rounded-lg"
                transition={{ type: 'spring', bounce: 0.2, duration: 0.4 }}
              />
            )}
            <span className="relative flex items-center gap-2">
              {tab.label}
              <span
                className={`px-1.5 py-0.5 rounded-full text-xs ${
                  isActive ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'
                }`}
              >
                {counts[tab.countKey]}
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
