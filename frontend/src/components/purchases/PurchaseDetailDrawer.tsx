import { useEffect, useId } from 'react';
import {
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useAnalyzePurchase, usePurchaseDetail } from '../../hooks/usePurchases';
import { VerdictBadge } from './VerdictBadge';
import { dataQualityLabel, formatCurrency, getAnalysisStatus } from './purchaseFormat';

function fmtAxisTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—';
  return fmtAxisTime(iso);
}

function StatCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: 'emerald' | 'red' | 'amber';
}) {
  const cls =
    accent === 'emerald'
      ? 'text-emerald-600'
      : accent === 'red'
        ? 'text-red-500'
        : accent === 'amber'
          ? 'text-amber-600'
          : 'text-gray-900';
  return (
    <div className="bg-gray-50 rounded-lg p-3">
      <p className="text-xs text-gray-400 mb-0.5">{label}</p>
      <p className={`text-lg font-bold tabular-nums ${cls}`}>{value}</p>
    </div>
  );
}

export function PurchaseDetailDrawer({
  purchaseId,
  onClose,
}: {
  purchaseId: number | null;
  onClose: () => void;
}) {
  const titleId = useId();
  const { data: detail, isLoading, isError, isFetching, refetch } = usePurchaseDetail(purchaseId);
  const analyzeMutation = useAnalyzePurchase();

  useEffect(() => {
    if (purchaseId == null) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose, purchaseId]);

  if (purchaseId == null) return null;

  const analysis = detail?.analysis ?? null;
  const analysisStatus = getAnalysisStatus(analysis);
  const chartData = (detail?.price_series ?? []).map((p) => ({
    time: fmtAxisTime(p.time),
    price: p.price,
  }));

  return (
    <div className="fixed inset-0 z-50">
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="absolute right-0 top-0 h-full w-full max-w-lg bg-white shadow-2xl overflow-y-auto"
      >
        {isError ? (
          <div className="h-full flex flex-col items-center justify-center px-6 text-center">
            <p className="text-sm font-medium text-red-700">买入详情加载失败</p>
            <p className="text-xs text-red-500 mt-1">记录仍然保留在服务端，请重新加载。</p>
            <div className="flex items-center gap-3 mt-4">
              <button
                type="button"
                onClick={() => void refetch()}
                disabled={isFetching}
                className="px-4 py-2 text-sm font-medium text-red-700 bg-red-50 border border-red-200 hover:bg-red-100 rounded-lg disabled:opacity-50"
              >
                {isFetching ? '重试中…' : '重新加载'}
              </button>
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 text-sm text-gray-500 hover:bg-gray-100 rounded-lg"
              >
                关闭
              </button>
            </div>
          </div>
        ) : isLoading || !detail ? (
          <div className="p-6 space-y-4">
            <div className="h-6 bg-gray-100 rounded w-1/2 animate-pulse" />
            <div className="grid grid-cols-2 gap-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="h-16 bg-gray-50 rounded-lg animate-pulse" />
              ))}
            </div>
            <div className="h-56 bg-gray-50 rounded-xl animate-pulse" />
          </div>
        ) : (
          <div className="p-6">
            {/* 头部 */}
            <div className="flex items-start justify-between mb-1">
              <div>
                <h2 id={titleId} className="text-lg font-bold text-gray-900">{detail.route_label}</h2>
                <p className="text-xs text-gray-400 mt-0.5">
                  {detail.flight_no ? `${detail.flight_no} · ${detail.airline ?? ''} · ` : ''}
                  起飞 {detail.target_date ?? '—'} · {detail.purchase_type === 'instant' ? '一键买入' : '计划买入'}
                </p>
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="关闭买入详情"
                className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-gray-100 text-gray-400 hover:text-gray-600"
              >
                ✕
              </button>
            </div>

            {/* 统计卡 */}
            <div className="grid grid-cols-2 gap-3 my-5">
              <StatCard
                label="单人可比票价"
                value={formatCurrency(detail.purchase_price, detail.currency)}
                accent="amber"
              />
              <StatCard
                label="订单实付总额"
                value={formatCurrency(
                  detail.total_paid ?? detail.purchase_price * detail.passengers,
                  detail.currency,
                )}
              />
              <StatCard
                label="买后最低"
                value={formatCurrency(analysis?.post_min_price, detail.currency)}
              />
              <StatCard
                label="起飞前最终价"
                value={formatCurrency(analysis?.final_price, detail.currency)}
              />
              <StatCard
                label="单人相对最终价节省"
                value={formatCurrency(analysis?.savings_vs_final, detail.currency, { sign: true })}
                accent={
                  analysis?.savings_vs_final != null
                    ? analysis.savings_vs_final >= 0
                      ? 'emerald'
                      : 'red'
                    : undefined
                }
              />
              <StatCard
                label="订单相对最终价节省"
                value={formatCurrency(
                  analysis?.savings_vs_final != null
                    ? analysis.savings_vs_final * detail.passengers
                    : null,
                  detail.currency,
                  { sign: true },
                )}
                accent={
                  analysis?.savings_vs_final != null
                    ? analysis.savings_vs_final >= 0
                      ? 'emerald'
                      : 'red'
                    : undefined
                }
              />
            </div>
            {analysis?.regret_cost != null && analysis.regret_cost > 0 && (
              <p className="text-xs text-red-500 -mt-3 mb-4 tabular-nums">
                单人后悔成本（相对买后最低多付）：
                {formatCurrency(analysis.regret_cost, detail.currency)}
              </p>
            )}

            {/* 买后价格走势 */}
            <div className="bg-white rounded-xl border border-gray-100 p-4 mb-5">
              <h3 className="text-sm font-semibold text-gray-700 mb-3">买后价格走势</h3>
              {chartData.length >= 2 ? (
                <>
                  <ResponsiveContainer width="100%" height={220}>
                    <LineChart data={chartData} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
                      <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#9ca3af' }} minTickGap={40} />
                      <YAxis
                        domain={['dataMin - 30', 'dataMax + 30']}
                        tick={{ fontSize: 10, fill: '#9ca3af' }}
                        width={52}
                      />
                      <Tooltip
                        formatter={(value) => [formatCurrency(Number(value), detail.currency), '价格']}
                        contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #f3f4f6' }}
                      />
                      <Line
                        type="monotone"
                        dataKey="price"
                        stroke="#2563eb"
                        strokeWidth={2}
                        dot={false}
                      />
                      <ReferenceLine
                        y={detail.purchase_price}
                        stroke="#d97706"
                        strokeDasharray="5 4"
                        label={{ value: `买入 ${formatCurrency(detail.purchase_price, detail.currency)}`, position: 'insideTopRight', fontSize: 10, fill: '#d97706' }}
                      />
                      {analysis?.post_min_price != null && (
                        <ReferenceLine
                          y={analysis.post_min_price}
                          stroke="#059669"
                          strokeDasharray="5 4"
                          label={{ value: `最低 ${formatCurrency(analysis.post_min_price, detail.currency)}`, position: 'insideBottomRight', fontSize: 10, fill: '#059669' }}
                        />
                      )}
                    </LineChart>
                  </ResponsiveContainer>
                  <div className="flex items-center gap-4 mt-2 text-xs text-gray-400">
                    <span className="flex items-center gap-1">
                      <span className="inline-block w-4 border-t-2 border-blue-600" /> 价格走势
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="inline-block w-4 border-t-2 border-dashed border-amber-600" /> 买入价
                    </span>
                    {analysis?.post_min_price != null && (
                      <span className="flex items-center gap-1">
                        <span className="inline-block w-4 border-t-2 border-dashed border-emerald-600" /> 买后最低
                      </span>
                    )}
                  </div>
                </>
              ) : (
                <p className="text-sm text-gray-400 py-10 text-center">
                  买后价格数据收集中，至少 2 个采集批次后展示走势
                </p>
              )}
            </div>

            {/* AI 分析 */}
            <div className="bg-white rounded-xl border border-gray-100 p-4 mb-5">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-gray-700">买点分析</h3>
                <div className="flex items-center gap-2">
                  {analysis && (
                    <VerdictBadge
                      verdict={analysis.verdict}
                      analysisStatus={analysisStatus}
                    />
                  )}
                  <button
                    type="button"
                    onClick={() => analyzeMutation.mutate(detail.id)}
                    disabled={analyzeMutation.isPending}
                    className="px-3 py-1.5 text-xs font-medium text-amber-700 bg-amber-50 hover:bg-amber-100 border border-amber-200 rounded-lg disabled:opacity-40"
                  >
                    {analyzeMutation.isPending ? '分析中…' : analysis ? '重新分析' : '立即分析'}
                  </button>
                </div>
              </div>

              {analysis ? (
                <div className="space-y-3">
                  {analysisStatus === 'provisional' && (
                    <p className="text-xs text-amber-600 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
                      临时分析：航班尚未起飞，结果不会计入 KPI；起飞后将自动生成终评。
                    </p>
                  )}
                  {analysisStatus === 'insufficient' && (
                    <p className="text-xs text-gray-600 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
                      当前采集数据不足，暂不评级且不计入 KPI。请继续采集后重新分析。
                    </p>
                  )}
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-400">
                    <span>样本批次：{analysis.sample_size ?? 0}</span>
                    <span>数据质量：{dataQualityLabel(analysis.data_quality)}</span>
                    <span>
                      覆盖时长：
                      {analysis.coverage_hours != null
                        ? `${analysis.coverage_hours.toFixed(1)} 小时`
                        : '未知'}
                    </span>
                  </div>
                  {analysis.ai_analysis?.verdict_comment && (
                    <p className="text-sm text-gray-800">{analysis.ai_analysis.verdict_comment}</p>
                  )}
                  {analysis.ai_analysis?.timing_assessment && (
                    <p className="text-xs text-gray-500 leading-relaxed">
                      {analysis.ai_analysis.timing_assessment}
                    </p>
                  )}
                  {analysis.ai_analysis?.key_lessons &&
                    analysis.ai_analysis.key_lessons.length > 0 && (
                      <div>
                        <p className="text-xs font-medium text-gray-500 mb-1.5">沉淀经验</p>
                        <ul className="space-y-1">
                          {analysis.ai_analysis.key_lessons.map((lesson, i) => (
                            <li key={i} className="text-xs text-gray-600 flex gap-1.5">
                              <span className="text-amber-500">•</span>
                              {lesson}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  <p className="text-xs text-gray-300">
                    {analysis.llm_source === 'deepseek' ? 'AI 分析' : '规则分析'} ·{' '}
                    {analysis.auto_generated ? '自动生成' : '手动生成'} · {fmtDateTime(analysis.analyzed_at)}
                  </p>
                </div>
              ) : (
                <p className="text-sm text-gray-400 py-4 text-center">
                  尚未生成买点分析。航班起飞后将自动生成，也可点击「立即分析」基于当前数据生成。
                </p>
              )}
              {analyzeMutation.isError && (
                <p className="text-xs text-red-500 mt-2">分析失败，请稍后重试</p>
              )}
            </div>

            {/* 买入信息 */}
            <div className="text-xs text-gray-400 space-y-1">
              <p>买入时间：{fmtDateTime(detail.purchased_at)}</p>
              {detail.seat_class && <p>舱位：{detail.seat_class}</p>}
              <p>乘客人数：{detail.passengers}</p>
              {detail.notes && <p>备注：{detail.notes}</p>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
