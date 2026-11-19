import { useEffect, useState } from 'react';
import { keepPreviousData, useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { AnimatePresence } from 'framer-motion';
import { apiClient } from '../api/client';
import { RadarHero } from '../components/radar/RadarHero';
import { WeekendPicker } from '../components/radar/WeekendPicker';
import { FilterBar } from '../components/radar/FilterBar';
import { DealCard } from '../components/radar/DealCard';
import type { RadarDealsResponse } from '../types/radar';

type SortOrder = 'recommended' | 'price' | 'savings';

export function RadarPage() {
  const queryClient = useQueryClient();
  const [selectedWeekend, setSelectedWeekend] = useState<string | null>(null);
  const [maxBudget, setMaxBudget] = useState(2000);
  const [visaFreeOnly, setVisaFreeOnly] = useState(false);
  const [excludeRedEye, setExcludeRedEye] = useState(false);
  const [sortOrder, setSortOrder] = useState<SortOrder>('recommended');
  const [scanMessage, setScanMessage] = useState<string | null>(null);
  const [awaitingScan, setAwaitingScan] = useState(false);
  const [scanBaseline, setScanBaseline] = useState<string | null>(null);
  const [scanTimedOut, setScanTimedOut] = useState(false);

  const { data, isLoading, isFetching, isError, refetch } = useQuery({
    queryKey: ['radar', 'deals', selectedWeekend, maxBudget, visaFreeOnly, excludeRedEye],
    queryFn: async ({ signal }): Promise<RadarDealsResponse> => {
      const params: Record<string, unknown> = {
        max_budget: maxBudget,
        visa_free_only: visaFreeOnly,
        exclude_red_eye: excludeRedEye,
      };
      if (selectedWeekend) params.outbound_date = selectedWeekend;
      const { data } = await apiClient.get<RadarDealsResponse>('/radar/deals', { params, signal });
      return data;
    },
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      awaitingScan &&
      !scanTimedOut &&
      (query.state.data?.latest_scan_at ?? null) === scanBaseline
        ? 5000
        : false,
    refetchIntervalInBackground: true,
  });

  const scanCompleted =
    awaitingScan && data?.latest_scan_at != null && data.latest_scan_at !== scanBaseline;
  const pollingScan = awaitingScan && !scanCompleted && !scanTimedOut;

  useEffect(() => {
    if (!pollingScan) return;
    const timeout = window.setTimeout(() => setScanTimedOut(true), 120000);
    return () => window.clearTimeout(timeout);
  }, [pollingScan]);

  useEffect(() => {
    if (!scanCompleted) return;
    const timeout = window.setTimeout(() => setAwaitingScan(false), 6000);
    return () => window.clearTimeout(timeout);
  }, [scanCompleted]);

  const scanMutation = useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post('/radar/scan');
      return data as { message: string; status: string };
    },
    onSuccess: (res) => {
      setScanMessage(res.message);
      if (res.status === 'queued') {
        setScanBaseline(data?.latest_scan_at ?? null);
        setScanTimedOut(false);
        setAwaitingScan(true);
        queryClient.invalidateQueries({ queryKey: ['radar'] });
      } else {
        setAwaitingScan(false);
      }
      setTimeout(() => setScanMessage(null), 6000);
    },
    onError: () => {
      setAwaitingScan(false);
      setScanMessage('扫描请求失败，请稍后重试');
      setTimeout(() => setScanMessage(null), 6000);
    },
  });

  const deals = data?.deals ?? [];
  const displayedDeals = sortOrder === 'recommended' ? deals : [...deals].sort((a, b) => {
    if (sortOrder === 'price') return a.total_price - b.total_price;
    const aSavings = Math.max(0, (a.historical_avg ?? a.total_price) - a.total_price);
    const bSavings = Math.max(0, (b.historical_avg ?? b.total_price) - b.total_price);
    return bSavings - aSavings;
  });
  const visibleScanMessage = scanCompleted
    ? '扫描结果已更新'
    : scanTimedOut
      ? '扫描仍在后台执行，请稍后手动刷新'
      : scanMessage;

  function resetFilters() {
    setSelectedWeekend(null);
    setMaxBudget(2000);
    setVisaFreeOnly(false);
    setExcludeRedEye(false);
  }

  return (
    <div>
      <RadarHero
        total={deals.length}
        latestScanAt={data?.latest_scan_at ?? null}
        isScanning={scanMutation.isPending || pollingScan}
        onScan={() => scanMutation.mutate()}
      />

      {visibleScanMessage && (
        <div className="mb-4 rounded-lg border border-purple-200 bg-purple-50 px-4 py-2.5 text-xs text-purple-800" role="status">
          {visibleScanMessage}
        </div>
      )}
      {isError && data && (
        <div className="mb-4 flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800" role="alert">
          <span>更新失败，正在显示上次结果。</span>
          <button type="button" className="font-medium underline" onClick={() => void refetch()} disabled={isFetching}>
            重试更新
          </button>
        </div>
      )}

      <WeekendPicker
        weekends={data?.weekends ?? []}
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

      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="text-xs text-gray-400" aria-live="polite">
          {isFetching ? '正在更新…' : `${deals.length} 条周末灵感`}
        </p>
        <label className="flex items-center gap-2 text-xs text-gray-500">
          <span>排序</span>
          <select
            value={sortOrder}
            onChange={(event) => setSortOrder(event.target.value as SortOrder)}
            className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs text-gray-700 outline-none transition-colors focus:border-purple-400"
          >
            <option value="recommended">推荐排序</option>
            <option value="price">价格从低到高</option>
            <option value="savings">节省最多</option>
          </select>
        </label>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3" role="status" aria-label="正在加载周末行程">
          {Array.from({ length: 6 }).map((_, index) => (
            <div key={index} className="animate-pulse overflow-hidden rounded-2xl border border-gray-100 bg-white">
              <div className="h-36 bg-gray-100" />
              <div className="space-y-3 p-5">
                <div className="h-4 w-3/4 rounded bg-gray-100" />
                <div className="h-3 w-full rounded bg-gray-50" />
                <div className="h-3 w-2/3 rounded bg-gray-50" />
              </div>
            </div>
          ))}
        </div>
      ) : isError && !data ? (
        <div className="rounded-2xl border border-gray-100 bg-white py-20 text-center" role="alert">
          <span className="text-5xl">🛰️</span>
          <p className="mt-3 text-sm font-medium text-gray-600">暂时无法加载周末行程</p>
          <p className="mt-1 text-xs text-gray-400">请检查网络连接，然后重试</p>
          <button type="button" className="mt-4 rounded-lg bg-gray-900 px-4 py-2 text-xs text-white" onClick={() => void refetch()} disabled={isFetching}>
            重新加载
          </button>
        </div>
      ) : deals.length === 0 ? (
        <div className="rounded-2xl border border-gray-100 bg-white py-20 text-center" role="status">
          <span className="text-5xl">🛰️</span>
          <p className="mt-3 text-sm font-medium text-gray-600">这个条件下暂无灵感</p>
          <p className="mt-1 text-xs text-gray-400">试试调高预算或切换周末</p>
          <button type="button" className="mt-4 rounded-lg border border-gray-200 bg-white px-4 py-2 text-xs text-gray-600 hover:border-purple-300" onClick={resetFilters}>
            重置筛选
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3" aria-busy={isFetching}>
          <AnimatePresence mode="popLayout">
            {displayedDeals.map((deal, index) => (
              <DealCard key={deal.id} deal={deal} index={index} />
            ))}
          </AnimatePresence>
        </div>
      )}

      <footer className="mt-6 space-y-1 text-center text-[10px] text-gray-400">
        <p>均为往返合计 / 人 · 起降时间为当地时间 · 最终票价以平台为准</p>
        <p>加入监控仅追踪价格变化，不锁定或购买机票。</p>
      </footer>
    </div>
  );
}
