import { useState } from 'react';
import { RecordsTab } from '../components/purchases/RecordsTab';
import { ExperiencesTab } from '../components/purchases/ExperiencesTab';
import { PurchaseDetailDrawer } from '../components/purchases/PurchaseDetailDrawer';

type TabKey = 'records' | 'experiences';

export function PurchasesPage() {
  const [tab, setTab] = useState<TabKey>('records');
  const [selectedId, setSelectedId] = useState<number | null>(null);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-bold text-gray-900">买入记录</h1>
        <p className="text-sm text-gray-400 mt-0.5">
          追踪已成交机票的买后价格走势，沉淀买点经验反哺 AI 决策
        </p>
      </div>

      {/* Tabs */}
      <div role="tablist" aria-label="买入记录页面" className="flex gap-1 bg-gray-100 rounded-lg p-1 w-fit mb-6">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'records'}
          onClick={() => setTab('records')}
          className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
            tab === 'records' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          买入记录
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'experiences'}
          onClick={() => setTab('experiences')}
          className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
            tab === 'experiences'
              ? 'bg-white text-gray-800 shadow-sm'
              : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          经验库
        </button>
      </div>

      {tab === 'records' ? <RecordsTab onSelect={setSelectedId} /> : <ExperiencesTab />}

      <PurchaseDetailDrawer purchaseId={selectedId} onClose={() => setSelectedId(null)} />
    </div>
  );
}
