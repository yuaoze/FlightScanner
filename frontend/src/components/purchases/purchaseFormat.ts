import type { BuyPointAnalysis, BuyPointAnalysisStatus } from '../../types';

export function formatCurrency(
  value: number | null | undefined,
  currency = 'CNY',
  options?: { sign?: boolean },
): string {
  if (value == null || !Number.isFinite(value)) return '—';
  let formatted: string;
  try {
    formatted = new Intl.NumberFormat('zh-CN', {
      style: 'currency',
      currency: currency || 'CNY',
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    }).format(Math.abs(value));
  } catch {
    formatted = `${currency || 'CNY'} ${Math.abs(value).toFixed(0)}`;
  }
  if (options?.sign && value > 0) return `+${formatted}`;
  if (value < 0) return `-${formatted}`;
  return formatted;
}

/** 兼容 v2.2.0 早期响应中尚未包含 analysis_status 的记录。 */
export function getAnalysisStatus(
  analysis: BuyPointAnalysis | null | undefined,
): BuyPointAnalysisStatus | null {
  if (!analysis) return null;
  if (analysis.analysis_status) return analysis.analysis_status;
  if (analysis.pre_departure) return 'provisional';
  return analysis.verdict ? 'final' : 'insufficient';
}

export function dataQualityLabel(quality: string | null | undefined): string {
  const labels: Record<string, string> = {
    high: '高',
    good: '良好',
    medium: '中',
    limited: '有限',
    low: '低',
    insufficient: '不足',
  };
  return quality ? (labels[quality] ?? quality) : '未知';
}

export function toLocalDateTimeInput(date = new Date()): string {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}
