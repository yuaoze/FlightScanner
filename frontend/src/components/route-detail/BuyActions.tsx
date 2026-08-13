import { useEffect, useId, useState } from 'react';
import type { RouteDetailResponse } from '../../types';
import { useCreatePlan, useInstantBuy } from '../../hooks/usePurchases';
import { formatCurrency, toLocalDateTimeInput } from '../purchases/purchaseFormat';

function useEscapeClose(onClose: () => void) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);
}

// ── 记录买入 Dialog ─────────────────────────────────────────────────────────

function InstantBuyDialog({
  route,
  onClose,
}: {
  route: RouteDetailResponse;
  onClose: () => void;
}) {
  const buyMutation = useInstantBuy();
  const titleId = useId();
  useEscapeClose(onClose);
  const [unitPrice, setUnitPrice] = useState(
    route.latest_price != null ? route.latest_price.toFixed(0) : '',
  );
  const [seatClass, setSeatClass] = useState(route.seat_class ?? '');
  const [passengers, setPassengers] = useState('1');
  const [totalPaid, setTotalPaid] = useState(
    route.latest_price != null ? route.latest_price.toFixed(0) : '',
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
    !buyMutation.isPending;

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
          <h2 id={titleId} className="text-lg font-semibold text-gray-900">记录买入</h2>
          <p className="text-sm text-gray-400 mt-0.5">
            {route.origin} → {route.destination} · {route.target_date}
            {route.latest_price != null && ` · 当前监控最低 ${formatCurrency(route.latest_price)}`}
          </p>
        </div>
        <div className="px-6 py-5 space-y-4">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1.5">
              单人可比票价 *
            </label>
            <input
              type="number"
              min="0.01"
              step="0.01"
              value={unitPrice}
              onChange={(e) => changeUnitPrice(e.target.value)}
              placeholder="每位乘客用于和监控报价比较的票价"
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm tabular-nums focus:outline-none focus:ring-2 focus:ring-amber-100 focus:border-amber-400"
            />
            <p className="text-xs text-gray-400 mt-1">复盘会用这个单人价格与同口径监控价比较</p>
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
              <label className="block text-xs font-medium text-gray-500 mb-1.5">张数</label>
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
            <label className="block text-xs font-medium text-gray-500 mb-1.5">
              订单实付总额 *
            </label>
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
              placeholder="可选，如：用了优惠券 / 里程抵扣"
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-amber-100 focus:border-amber-400"
            />
          </div>
          {buyMutation.isError && (
            <p className="text-xs text-red-500">记录失败，请检查价格后重试</p>
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
              buyMutation.mutate(
                {
                  route_id: route.id,
                  unit_price: Number(unitPrice),
                  price: Number(unitPrice),
                  total_paid: Number(totalPaid),
                  ...(seatClass.trim() ? { seat_class: seatClass.trim() } : {}),
                  passengers: passengerCount,
                  purchased_at: new Date(purchasedAt).toISOString(),
                  ...(notes.trim() ? { notes: notes.trim() } : {}),
                },
                { onSuccess: onClose },
              )
            }
            className="px-4 py-2 text-sm font-medium text-white bg-amber-600 hover:bg-amber-700 rounded-lg disabled:opacity-40"
          >
            {buyMutation.isPending ? '提交中…' : '确认买入'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── 创建买入计划 Dialog ─────────────────────────────────────────────────────

function CreatePlanDialog({
  route,
  onClose,
}: {
  route: RouteDetailResponse;
  onClose: () => void;
}) {
  const planMutation = useCreatePlan();
  const titleId = useId();
  useEscapeClose(onClose);
  const [planPrice, setPlanPrice] = useState(
    route.latest_price != null ? Math.round(route.latest_price * 0.95).toFixed(0) : '',
  );
  const [executeBy, setExecuteBy] = useState('');
  const [error, setError] = useState('');

  const isPending = planMutation.isPending;

  const handleSubmit = () => {
    const hasPrice = planPrice.trim().length > 0 && Number(planPrice) > 0;
    const hasDeadline = executeBy.trim().length > 0;
    if (!hasPrice && !hasDeadline) {
      setError('目标价与最迟买入时间至少填写一个');
      return;
    }
    setError('');
    planMutation.mutate(
      {
        route_id: route.id,
        ...(hasPrice ? { plan_price: Number(planPrice) } : {}),
        ...(hasDeadline ? { plan_execute_by: new Date(executeBy).toISOString() } : {}),
      },
      { onSuccess: onClose },
    );
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
          <h2 id={titleId} className="text-lg font-semibold text-gray-900">创建买入计划</h2>
          <p className="text-sm text-gray-400 mt-0.5">
            价格达标或到达截止时间时通知你确认成交
          </p>
        </div>
        <div className="px-6 py-5 space-y-4">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1.5">
              目标买入价（已按当前价 95% 预填）
            </label>
            <input
              type="number"
              min="0.01"
              step="0.01"
              value={planPrice}
              onChange={(e) => setPlanPrice(e.target.value)}
              placeholder="留空则仅按时间触发"
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm tabular-nums focus:outline-none focus:ring-2 focus:ring-amber-100 focus:border-amber-400"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1.5">
              最迟买入时间
            </label>
            <input
              type="datetime-local"
              value={executeBy}
              onChange={(e) => setExecuteBy(e.target.value)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-amber-100 focus:border-amber-400"
            />
          </div>
          {(error || planMutation.isError) && (
            <p className="text-xs text-red-500">{error || '创建失败，请重试'}</p>
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
            disabled={isPending}
            onClick={handleSubmit}
            className="px-4 py-2 text-sm font-medium text-white bg-amber-600 hover:bg-amber-700 rounded-lg disabled:opacity-40"
          >
            {isPending ? '创建中…' : '创建计划'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── 入口组件 ────────────────────────────────────────────────────────────────

export function BuyActions({ route }: { route: RouteDetailResponse }) {
  const [buyOpen, setBuyOpen] = useState(false);
  const [planOpen, setPlanOpen] = useState(false);

  return (
    <>
      <div className="flex items-center gap-2 mt-2.5">
        <button
          type="button"
          onClick={() => setPlanOpen(true)}
          className="px-3 py-1.5 text-xs font-medium text-amber-700 bg-white border border-amber-300 hover:bg-amber-50 rounded-lg transition-colors"
        >
          创建买入计划
        </button>
        <button
          type="button"
          onClick={() => setBuyOpen(true)}
          className="px-3 py-1.5 text-xs font-medium text-white bg-amber-600 hover:bg-amber-700 rounded-lg transition-colors"
        >
          💰 记录买入
        </button>
      </div>
      {buyOpen && <InstantBuyDialog route={route} onClose={() => setBuyOpen(false)} />}
      {planOpen && <CreatePlanDialog route={route} onClose={() => setPlanOpen(false)} />}
    </>
  );
}
