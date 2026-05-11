import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { AnimatePresence } from 'framer-motion';
import { apiClient } from '../api/client';
import { RadarHero } from '../components/radar/RadarHero';
import { WeekendPicker } from '../components/radar/WeekendPicker';
import { FilterBar } from '../components/radar/FilterBar';
import { DealCard } from '../components/radar/DealCard';
import type { RadarDealsResponse } from '../types/radar';

export function RadarPage() {
  const queryClient = useQueryClient();
  const [selectedWeekend, setSelectedWeekend] = useState<string | null>(null);
  const [maxBudget, setMaxBudget] = useState(2000);
  const [visaFreeOnly, setVisaFreeOnly] = useState(false);
  const [excludeRedEye, setExcludeRedEye] = useState(false);
  const [scanMessage, setScanMessage] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['radar', 'deals', selectedWeekend, maxBudget, visaFreeOnly, excludeRedEye],
    queryFn: async (): Promise<RadarDealsResponse> => {
      const params: Record<string, unknown> = {
        max_budget: maxBudget,
        visa_free_only: visaFreeOnly,
        exclude_red_eye: excludeRedEye,
      };
      if (selectedWeekend) params.outbound_date = selectedWeekend;
      const { data } = await apiClient.get<RadarDealsResponse>('/radar/deals', { params });
      return data;
    },
  });

  const scanMutation = useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post('/radar/scan');
      return data as { message: string; status: string };
    },
    onSuccess: (res) => {
      setScanMessage(res.message);
      setTimeout(() => setScanMessage(null), 6000);
      queryClient.invalidateQueries({ queryKey: ['radar'] });
    },
    onError: () => {
      setScanMessage('扫描请求失败，请稍后重试');
      setTimeout(() => setScanMessage(null), 6000);
    },
  });

  const deals = data?.deals ?? [];
  const weekends = data?.weekends ?? [];

  return (
    <div>
      <RadarHero
        total={deals.length}
        latestScanAt={data?.latest_scan_at ?? null}
        isScanning={scanMutation.isPending}
        onScan={() => scanMutation.mutate()}
      />

      {scanMessage && (
        <div className="mb-4 bg-purple-50 border border-purple-200 rounded-lg px-4 py-2.5 text-xs text-purple-800">
          {scanMessage}
        </div>
      )}

      <WeekendPicker
        weekends={weekends}
        selected={selectedWeekend}
        onSelect={setSelectedWeekend}
      />

      <FilterBar
        maxBudget={maxBudget}
        onBudgetChange={setMaxBudget}
        visaFreeOnly={visaFreeOnly}
        onToggleVisaFree={setVisaFreeOnly}
        excludeRedEye={excludeRedEye}
        onToggleRedEye={setExcludeRedEye}
      />

      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="bg-white rounded-2xl border border-gray-100 overflow-hidden animate-pulse"
            >
              <div className="h-36 bg-gray-100" />
              <div className="p-5 space-y-3">
                <div className="h-4 bg-gray-100 rounded w-3/4" />
                <div className="h-3 bg-gray-50 rounded w-full" />
                <div className="h-3 bg-gray-50 rounded w-2/3" />
              </div>
            </div>
          ))}
        </div>
      ) : deals.length === 0 ? (
        <div className="bg-white rounded-2xl border border-gray-100 py-20 text-center">
          <span className="text-5xl">🛰️</span>
          <p className="text-sm text-gray-600 mt-3 font-medium">这个条件下暂无灵感</p>
          <p className="text-xs text-gray-400 mt-1">试试调高预算或切换周末</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <AnimatePresence mode="popLayout">
            {deals.map((deal, i) => (
              <DealCard key={deal.id} deal={deal} index={i} />
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
