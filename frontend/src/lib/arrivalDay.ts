import type { ArrivalDayLimit } from '../types';

export const ARRIVAL_DAY_LIMIT_OPTIONS: ReadonlyArray<{
  value: string;
  limit: ArrivalDayLimit;
  label: string;
}> = [
  { value: '0', limit: 0, label: '仅当天到达' },
  { value: '1', limit: 1, label: '最晚 +1 天到达（含当天）' },
  { value: '2', limit: 2, label: '最晚 +2 天到达（含当天、+1）' },
  { value: 'unlimited', limit: null, label: '不限到达日期（含 +2 天以上）' },
];

export function arrivalDayLimitValue(limit: ArrivalDayLimit): string {
  return limit === null ? 'unlimited' : String(limit);
}

export function parseArrivalDayLimit(value: string): ArrivalDayLimit {
  if (value === '0') return 0;
  if (value === '1') return 1;
  if (value === '2') return 2;
  return null;
}

export function arrivalDayLimitDescription(limit: ArrivalDayLimit): string {
  if (limit === 0) return '仅保留出发当天落地的航班。';
  if (limit === 1) return '保留当天和 +1 天到达的航班。';
  if (limit === 2) return '保留当天、+1 天和 +2 天到达的航班。';
  return '不限制跨日天数，+2 天以上的航班也会保留。';
}

export function formatFlightDate(date: string | null | undefined): string {
  if (!date) return '日期待采集';
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(date);
  if (!match) return date;
  return `${match[1]}-${match[2]}-${match[3]}`;
}

export function formatArrivalDayOffset(offset: number): string {
  if (!Number.isFinite(offset) || offset <= 0) return '当天';
  return `+${Math.trunc(offset)}天`;
}
