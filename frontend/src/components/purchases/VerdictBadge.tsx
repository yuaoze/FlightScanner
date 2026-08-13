import type { BuyPointAnalysisStatus, BuyVerdict } from '../../types';

const VERDICT_STYLES: Record<BuyVerdict, { label: string; cls: string }> = {
  excellent: { label: '✓ 优秀', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  good: { label: '● 良好', cls: 'bg-blue-50 text-blue-700 border-blue-200' },
  fair: { label: '◐ 一般', cls: 'bg-amber-50 text-amber-700 border-amber-200' },
  poor: { label: '✗ 偏差', cls: 'bg-red-50 text-red-700 border-red-200' },
};

export function VerdictBadge({
  verdict,
  analysisStatus,
}: {
  verdict: BuyVerdict | null | undefined;
  analysisStatus?: BuyPointAnalysisStatus | null;
}) {
  if (analysisStatus === 'insufficient') {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border bg-gray-50 text-gray-500 border-gray-200">
        数据不足
      </span>
    );
  }
  if (!verdict) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border bg-gray-50 text-gray-400 border-gray-200">
        待分析
      </span>
    );
  }
  const style = VERDICT_STYLES[verdict];
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${style.cls}`}
    >
      {analysisStatus === 'provisional' ? '临时 · ' : analysisStatus === 'final' ? '终评 · ' : ''}
      {style.label}
    </span>
  );
}
