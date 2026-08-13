import { useEffect, useId, useState } from 'react';
import type { BuyPlan, PurchaseRecordItem } from '../../types';
import { useCancelPlan, useConfirmPlan, usePlans, usePurchases } from '../../hooks/usePurchases';
import { VerdictBadge } from './VerdictBadge';
import {
  dataQualityLabel,
  formatCurrency,
  getAnalysisStatus,
  toLocalDateTimeInput,
} from './purchaseFormat';

function useEscapeClose(onClose: () => void) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

const VERDICT_SCORE: Record<string, number> = { excellent: 4, good: 3, fair: 2, poor: 1 };

// ── KPI 卡片 ────────────────────────────────────────────────────────────────

function KpiCards({ purchases }: { purchases: PurchaseRecordItem[] }) {
  const finalAnalyses = purchases.filter(
    (p) => getAnalysisStatus(p.analysis) === 'final' && p.analysis?.data_quality === 'good',
  );
  const savingsByCurrency = finalAnalyses.reduce<Record<string, number>>((totals, purchase) => {
    const amount = (purchase.analysis?.savings_vs_final ?? 0) * purchase.passengers;
    totals[purchase.currency] = (totals[purchase.currency] ?? 0) + amount;
    return totals;
  }, {});
  const savingsEntries = Object.entries(savingsByCurrency);
  const scored = finalAnalyses.filter((p) => p.analysis?.verdict);
  const avgScore =
    scored.length > 0
      ? scored.reduce((sum, p) => sum + (VERDICT_SCORE[p.analysis!.verdict!] ?? 0), 0) /
        scored.length
      : null;
  const holding = purchases.filter((p) => p.status === 'holding').length;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
      <div className="bg-white rounded-xl border border-gray-100 p-4">
        <p className="text-xs text-gray-400 mb-1">订单累计节省（相对最终价）</p>
        {savingsEntries.length > 0 ? (
          <div className="space-y-0.5">
            {savingsEntries.map(([currency, amount]) => (
              <p
                key={currency}
                className={`text-2xl font-bold tabular-nums ${
                  amount >= 0 ? 'text-emerald-600' : 'text-red-500'
                }`}
              >
                {formatCurrency(amount, currency, { sign: true })}
              </p>
            ))}
          </div>
        ) : (
          <p className="text-2xl font-bold text-gray-900">—</p>
        )}
        <p className="text-xs text-gray-300 mt-1">仅统计 {finalAnalyses.length} 条高质量终评</p>
      </div>
      <div className="bg-white rounded-xl border border-gray-100 p-4">
        <p className="text-xs text-gray-400 mb-1">平均买点评分</p>
        <p className="text-2xl font-bold text-gray-900 tabular-nums">
          {avgScore != null ? avgScore.toFixed(1) : '—'}
          <span className="text-sm font-normal text-gray-400"> / 4.0</span>
        </p>
        <p className="text-xs text-gray-300 mt-1">临时分析与数据不足不计入</p>
      </div>
      <div className="bg-white rounded-xl border border-gray-100 p-4">
        <p className="text-xs text-gray-400 mb-1">持有中</p>
        <p className="text-2xl font-bold text-amber-600 tabular-nums">{holding}</p>
        <p className="text-xs text-gray-300 mt-1">起飞后自动生成买点分析</p>
      </div>
    </div>
  );
}

// ── 计划确认 Dialog ─────────────────────────────────────────────────────────

function ConfirmPlanDialog({
  plan,
  onClose,
}: {
  plan: BuyPlan;
  onClose: () => void;
}) {
  const confirmMutation = useConfirmPlan();
  const titleId = useId();
  useEscapeClose(onClose);
  const [unitPrice, setUnitPrice] = useState(
    plan.trigger_price != null ? plan.trigger_price.toFixed(0) : '',
  );
  const [passengers, setPassengers] = useState('1');
  const [seatClass, setSeatClass] = useState('');
  const [totalPaid, setTotalPaid] = useState(
    plan.trigger_price != null ? plan.trigger_price.toFixed(0) : '',
  );
  const [totalEdited, setTotalEdited] = useState(false);
  const [purchasedAt, setPurchasedAt] = useState(toLocalDateTimeInput());
  const [notes, setNotes] = useState('');

  const passengerCount = Math.max(1, Number(passengers) || 1);
  const canSubmit =
    Number(unitPrice) > 0 &&
    Number(totalPaid) > 0 &&
    Number(passengers) >= 1 &&
    purchasedAt.length > 0 &&
    !Number.isNaN(new Date(purchasedAt).getTime()) &&
    !confirmMutation.isPending;

  const changeUnitPrice = (value: string) => {
    setUnitPrice(value);
    if (!totalEdited) {
      const suggested = Number(value) * passengerCount;
      setTotalPaid(suggested > 0 ? String(suggested) : '');
    }
  };

  const changePassengers = (value: string) => {
    setPassengers(value);
    if (!totalEdited) {
      const count = Math.max(1, Number(value) || 1);
      const suggested = Number(unitPrice) * count;
      setTotalPaid(suggested > 0 ? String(suggested) : '');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 max-h-[calc(100vh-2rem)] overflow-y-auto"
      >
        <div className="px-6 py-4 border-b border-gray-100">
          <h2 id={titleId} className="text-lg font-semibold text-gray-900">确认成交</h2>
          <p className="text-sm text-gray-400 mt-0.5">
            {plan.route_label} · 触发价 {formatCurrency(plan.trigger_price, plan.currency)}
          </p>
        </div>
        <div className="px-6 py-5 space-y-4">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1.5">单人可比票价 *</label>
            <input
              type="number"
              min="0.01"
              step="0.01"
              value={unitPrice}
              onChange={(e) => changeUnitPrice(e.target.value)}
              placeholder="每位乘客用于和监控报价比较的票价"
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm tabular-nums focus:outline-none focus:ring-2 focus:ring-amber-100 focus:border-amber-400"
            />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1.5">舱位</label>
              <input
                type="text"
                value={seatClass}
                onChange={(e) => setSeatClass(e.target.value)}
                placeholder="如：经济舱"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-amber-100 focus:border-amber-400"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1.5">乘客人数</label>
              <input
                type="number"
                min={1}
                step={1}
                value={passengers}
                onChange={(e) => changePassengers(e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm tabular-nums focus:outline-none focus:ring-2 focus:ring-amber-100 focus:border-amber-400"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1.5">订单实付总额 *</label>
            <input
              type="number"
              min="0.01"
              step="0.01"
              value={totalPaid}
              onChange={(e) => {
                setTotalPaid(e.target.value);
                setTotalEdited(true);
              }}
              placeholder="含全部乘客、税费与优惠后的实际支付总额"
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm tabular-nums focus:outline-none focus:ring-2 focus:ring-amber-100 focus:border-amber-400"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1.5">真实成交时间 *</label>
            <input
              type="datetime-local"
              value={purchasedAt}
              onChange={(e) => setPurchasedAt(e.target.value)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-amber-100 focus:border-amber-400"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1.5">备注</label>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="可选"
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-amber-100 focus:border-amber-400"
            />
          </div>
          {confirmMutation.isError && (
            <p className="text-xs text-red-500">确认失败，请检查价格后重试</p>
          )}
        </div>
        <div className="px-6 py-4 border-t border-gray-100 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-500 hover:bg-gray-50 rounded-lg"
          >
            取消
          </button>
          <button
            type="button"
            disabled={!canSubmit}
            onClick={() =>
              confirmMutation.mutate(
                {
                  id: plan.id,
                  body: {
                    unit_price: Number(unitPrice),
                    price: Number(unitPrice),
                    actual_price: Number(unitPrice),
                    total_paid: Number(totalPaid),
                    ...(seatClass.trim() ? { seat_class: seatClass.trim() } : {}),
                    passengers: passengerCount,
                    purchased_at: new Date(purchasedAt).toISOString(),
                    ...(notes.trim() ? { notes: notes.trim() } : {}),
                  },
                },
                { onSuccess: onClose },
              )
            }
            className="px-4 py-2 text-sm font-medium text-white bg-amber-600 hover:bg-amber-700 rounded-lg disabled:opacity-40"
          >
            {confirmMutation.isPending ? '提交中…' : '确认成交'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── 计划区块 ────────────────────────────────────────────────────────────────

function PlansSection() {
  const { data: plans, isLoading, isError, isFetching, refetch } = usePlans();
  const cancelMutation = useCancelPlan();
  const [confirming, setConfirming] = useState<BuyPlan | null>(null);

  if (isError) {
    return (
      <div className="mb-6 rounded-xl border border-red-100 bg-red-50 p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-red-700">买入计划加载失败</p>
          <p className="text-xs text-red-500 mt-0.5">计划仍然保留在服务端，请重试获取。</p>
        </div>
        <button
          type="button"
          onClick={() => void refetch()}
          disabled={isFetching}
          className="self-start sm:self-auto px-3 py-1.5 text-xs font-medium text-red-700 bg-white border border-red-200 hover:bg-red-100 rounded-lg disabled:opacity-50"
        >
          {isFetching ? '重试中…' : '重试'}
        </button>
      </div>
    );
  }

  if (isLoading) {
    return <div className="mb-6 h-16 rounded-xl border border-gray-100 bg-white animate-pulse" />;
  }

  const active = (plans ?? []).filter((p) => p.status === 'pending' || p.status === 'triggered');
  if (active.length === 0) return null;

  return (
    <div className="mb-6">
      <h3 className="text-sm font-semibold text-gray-700 mb-3">买入计划</h3>
      <div className="space-y-2">
        {active.map((plan) => (
          <div
            key={plan.id}
            className={`flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-xl border p-3.5 ${
              plan.status === 'triggered'
                ? 'bg-amber-50 border-amber-200'
                : 'bg-white border-gray-100'
            }`}
          >
            <div className="flex items-start sm:items-center gap-3 min-w-0">
              <span className="text-lg">{plan.status === 'triggered' ? '🔔' : '⏳'}</span>
              <div>
                <p className="text-sm font-medium text-gray-800">{plan.route_label}</p>
                <p className="text-xs text-gray-400">
                  {plan.plan_price != null && `目标价 ${formatCurrency(plan.plan_price, plan.currency)}`}
                  {plan.plan_price != null && plan.plan_execute_by != null && ' · '}
                  {plan.plan_execute_by != null && `截止 ${fmtDateTime(plan.plan_execute_by)}`}
                  {plan.status === 'triggered' &&
                    ` · 已触发（${plan.trigger_reason === 'price_hit' ? '价格达标' : '到达截止时间'} ${formatCurrency(plan.trigger_price, plan.currency)}）`}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2 self-end sm:self-auto shrink-0">
              {plan.status === 'triggered' && (
                <button
                  type="button"
                  onClick={() => setConfirming(plan)}
                  className="px-3 py-1.5 text-xs font-medium text-white bg-amber-600 hover:bg-amber-700 rounded-lg"
                >
                  确认成交
                </button>
              )}
              <button
                type="button"
                onClick={() => cancelMutation.mutate(plan.id)}
                disabled={cancelMutation.isPending}
                className="px-3 py-1.5 text-xs text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg"
              >
                取消计划
              </button>
            </div>
          </div>
        ))}
      </div>
      {cancelMutation.isError && (
        <p className="text-xs text-red-500 mt-2">取消计划失败，请稍后重试。</p>
      )}
      {confirming && <ConfirmPlanDialog plan={confirming} onClose={() => setConfirming(null)} />}
    </div>
  );
}

// ── 记录表格 ────────────────────────────────────────────────────────────────

export function RecordsTab({ onSelect }: { onSelect: (id: number) => void }) {
  const { data: purchases, isLoading, isError, isFetching, refetch } = usePurchases();

  if (isLoading) {
    return (
      <div className="space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="bg-white rounded-xl border border-gray-100 p-4 animate-pulse">
              <div className="h-3 bg-gray-100 rounded w-1/2 mb-2" />
              <div className="h-6 bg-gray-50 rounded w-2/3" />
            </div>
          ))}
        </div>
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="bg-white rounded-xl border border-gray-100 p-4 animate-pulse">
            <div className="h-4 bg-gray-100 rounded w-2/3" />
          </div>
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div className="rounded-xl border border-red-100 bg-red-50 px-5 py-8 text-center">
        <p className="text-sm font-medium text-red-700">买入记录加载失败</p>
        <p className="text-xs text-red-500 mt-1">这不是空记录，请检查服务状态后重试。</p>
        <button
          type="button"
          onClick={() => void refetch()}
          disabled={isFetching}
          className="mt-4 px-4 py-2 text-sm font-medium text-red-700 bg-white border border-red-200 hover:bg-red-100 rounded-lg disabled:opacity-50"
        >
          {isFetching ? '重试中…' : '重新加载'}
        </button>
      </div>
    );
  }

  const items = purchases ?? [];

  return (
    <div>
      <KpiCards purchases={items} />
      <PlansSection />

      {items.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-gray-400">
          <span className="text-4xl mb-4">💰</span>
          <p className="text-base">暂无买入记录</p>
          <p className="text-sm mt-1">在路线详情页点击「记录买入」，成交后在此追踪买点表现</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-100 overflow-x-auto">
          <table className="w-full min-w-[920px] text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-xs text-gray-400">
                <th className="text-left font-medium px-4 py-3">路线 / 航班</th>
                <th className="text-right font-medium px-4 py-3">单人价 / 订单总额</th>
                <th className="text-left font-medium px-4 py-3">买入时间</th>
                <th className="text-right font-medium px-4 py-3">当前 / 最终价</th>
                <th className="text-right font-medium px-4 py-3">涨跌</th>
                <th className="text-left font-medium px-4 py-3">买点评级</th>
                <th className="text-left font-medium px-4 py-3">状态</th>
              </tr>
            </thead>
            <tbody>
              {items.map((p) => (
                <tr
                  key={p.id}
                  onClick={() => onSelect(p.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      onSelect(p.id);
                    }
                  }}
                  tabIndex={0}
                  aria-label={`查看 ${p.route_label} 买入详情`}
                  className="border-b border-gray-50 last:border-0 hover:bg-amber-50/40 cursor-pointer transition-colors"
                >
                  <td className="px-4 py-3">
                    <p className="font-medium text-gray-800">{p.route_label}</p>
                    <p className="text-xs text-gray-400">
                      {p.flight_no ? `${p.flight_no} · ${p.airline ?? ''}` : '未关联航班'}
                      {p.target_date && ` · ${p.target_date}`}
                    </p>
                  </td>
                  <td className="px-4 py-3 text-right font-semibold text-gray-900 tabular-nums">
                    <p>{formatCurrency(p.purchase_price, p.currency)}</p>
                    <p className="text-xs font-normal text-gray-400">
                      总额 {formatCurrency(p.total_paid ?? p.purchase_price * p.passengers, p.currency)}
                    </p>
                  </td>
                  <td className="px-4 py-3 text-gray-500 tabular-nums">
                    {fmtDateTime(p.purchased_at)}
                  </td>
                  <td className="px-4 py-3 text-right text-gray-700 tabular-nums">
                    {p.status === 'completed'
                      ? formatCurrency(p.analysis?.final_price, p.currency)
                      : formatCurrency(p.current_price, p.currency)}
                    <p className="text-[11px] text-gray-300">
                      {p.status === 'completed' ? '起飞前最终' : '最新监控'}
                    </p>
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {p.change_pct != null ? (
                      <span
                        className={`font-medium ${
                          p.change_pct > 0
                            ? 'text-red-500'
                            : p.change_pct < 0
                              ? 'text-emerald-600'
                              : 'text-gray-400'
                        }`}
                      >
                        {p.change_pct > 0 ? '+' : ''}
                        {p.change_pct.toFixed(1)}%
                      </span>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <VerdictBadge
                      verdict={p.analysis?.verdict}
                      analysisStatus={getAnalysisStatus(p.analysis)}
                    />
                    {p.analysis && (
                      <p className="text-[11px] text-gray-300 mt-1 whitespace-nowrap">
                        {p.analysis.sample_size ?? 0} 批 · 质量{dataQualityLabel(p.analysis.data_quality)}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {p.status === 'holding' ? (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-amber-50 text-amber-700 border border-amber-200">
                        持有中
                      </span>
                    ) : (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-50 text-gray-500 border border-gray-200">
                        已完成
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
