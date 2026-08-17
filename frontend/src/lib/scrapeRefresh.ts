import type { ScrapeTaskStatus, TriggerScrapeResponse } from '../types';

export const SCRAPE_POLL_INTERVAL_MS = 1_500;
export const SCRAPE_POLL_TIMEOUT_MS = 3 * 60 * 1_000;
export const SCRAPE_TERMINAL_STATUSES: ReadonlySet<ScrapeTaskStatus> = new Set([
  'completed',
  'partial',
  'failed',
]);

export interface ScrapeRefreshTarget {
  queryKey: readonly unknown[];
  exact?: boolean;
}

/** HTTP 202 may contain a failed task, so an active response must also have a task ID. */
export function requireQueuedScrape(response: TriggerScrapeResponse): TriggerScrapeResponse {
  if (!response.task_id || (response.status !== 'queued' && response.status !== 'running')) {
    throw new Error(response.error || response.message || '采集任务未成功排队');
  }
  return response;
}

export function isScrapeTerminal(status: ScrapeTaskStatus): boolean {
  return SCRAPE_TERMINAL_STATUSES.has(status);
}

/** Every view whose data can change after one route scrape. */
export function getScrapeRefreshTargets(routeId: number): ScrapeRefreshTarget[] {
  return [
    // Prefix matches detail, history, batches, flights and route-detail calendar.
    { queryKey: ['route', routeId] },
    { queryKey: ['calendar', routeId] },
    { queryKey: ['routes'] },
  ];
}
