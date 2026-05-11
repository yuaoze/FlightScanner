export type DetailTab = 'price' | 'ai' | 'flights' | 'config';

interface Props {
  activeTab: DetailTab;
  onTabChange: (tab: DetailTab) => void;
}

const TABS: { key: DetailTab; label: string }[] = [
  { key: 'price', label: '价格走势' },
  { key: 'ai', label: 'AI 洞察' },
  { key: 'flights', label: '航班' },
  { key: 'config', label: '监控设置' },
];

export function TabNavigation({ activeTab, onTabChange }: Props) {
  return (
    <div className="flex border-b border-gray-100 mb-5">
      {TABS.map((tab) => (
        <button
          key={tab.key}
          onClick={() => onTabChange(tab.key)}
          className={`px-4 py-2.5 text-sm font-medium transition-colors relative ${
            activeTab === tab.key
              ? 'text-blue-600'
              : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          {tab.label}
          {activeTab === tab.key && (
            <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-blue-600 rounded-t" />
          )}
        </button>
      ))}
    </div>
  );
}
