export function formatPrice(price: number | null): string {
  if (price === null) return '--';
  return `¥${Math.round(price).toLocaleString()}`;
}

export function formatDate(dateStr: string): string {
  const d = new Date(dateStr);
  return `${d.getMonth() + 1}.${String(d.getDate()).padStart(2, '0')}`;
}

export function formatDateRange(dep: string, ret: string | null): string {
  const depStr = formatDate(dep);
  if (!ret) return depStr;
  return `${depStr} - ${formatDate(ret)}`;
}

export function daysUntilText(days: number): string {
  if (days < 0) return '已过期';
  if (days === 0) return '今天';
  return `${days}天后`;
}

export function nextScrapeCountdown(
  latestScrapedAt: string | null,
  scrapeIntervalHours: number,
  isActive: boolean,
): { label: string; overdue: boolean } | null {
  if (!isActive) return null;
  if (!latestScrapedAt) return { label: '⏱ 等待首次采集', overdue: false };
  const last = new Date(latestScrapedAt).getTime();
  const next = last + scrapeIntervalHours * 3600 * 1000;
  const deltaSec = Math.floor((next - Date.now()) / 1000);
  if (deltaSec <= 0) return { label: '⏱ 即将采集', overdue: true };
  const mins = Math.floor(deltaSec / 60);
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  const label = h > 0 ? `⏱ ${h}h ${String(m).padStart(2, '0')}m 后采集` : `⏱ ${m}m 后采集`;
  return { label, overdue: false };
}
