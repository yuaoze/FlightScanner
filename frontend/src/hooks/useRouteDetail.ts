import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../api/client';
import type {
  RouteDetailResponse,
  PriceHistoryResponse,
  RoutePredictionsResponse,
  CalendarData,
  RouteBatchesResponse,
  RouteFlightsResponse,
  TriggerScrapeResponse,
  ArrivalDayLimit,
} from '../types';
import {
  SCRAPE_POLL_INTERVAL_MS,
  SCRAPE_POLL_TIMEOUT_MS,
  getScrapeRefreshTargets,
  isScrapeTerminal,
  requireQueuedScrape,
} from '../lib/scrapeRefresh';

export function useRouteDetail(id: number) {
  return useQuery({
    queryKey: ['route', id],
    queryFn: async (): Promise<RouteDetailResponse> => {
      const { data } = await apiClient.get<RouteDetailResponse>(`/routes/${id}/detail`);
      return data;
    },
    staleTime: 2 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  });
}

export function useRouteHistory(id: number, days = 30) {
  return useQuery({
    queryKey: ['route', id, 'history', days],
    queryFn: async (): Promise<PriceHistoryResponse> => {
      const { data } = await apiClient.get<PriceHistoryResponse>(`/routes/${id}/history`, {
        params: { days },
      });
      return data;
    },
    staleTime: 5 * 60 * 1000,
  });
}

export function useRoutePredictions(id: number) {
  return useQuery({
    queryKey: ['route', id, 'predictions'],
    queryFn: async (): Promise<RoutePredictionsResponse> => {
      const { data } = await apiClient.get<RoutePredictionsResponse>(`/routes/${id}/predictions`);
      return data;
    },
    staleTime: 5 * 60 * 1000,
  });
}

export function useRouteCalendar(id: number, month: string) {
  return useQuery({
    queryKey: ['route', id, 'calendar', month],
    queryFn: async (): Promise<CalendarData> => {
      const { data } = await apiClient.get<CalendarData>(`/routes/${id}/calendar`, {
        params: { month },
      });
      return data;
    },
    enabled: !!id,
    staleTime: 5 * 60 * 1000,
  });
}

export interface UpdateRouteBody {
  target_price?: number;
  scrape_interval?: number;
  is_active?: boolean;
  dep_time_from?: string;
  dep_time_to?: string;
  arr_time_from?: string;
  arr_time_to?: string;
  ret_dep_time_from?: string;
  ret_dep_time_to?: string;
  ret_arr_time_from?: string;
  ret_arr_time_to?: string;
  max_arrival_day_offset?: ArrivalDayLimit;
  ret_max_arrival_day_offset?: ArrivalDayLimit;
}

export function useUpdateRoute(id: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: UpdateRouteBody) => {
      const { data } = await apiClient.patch(`/routes/${id}`, body);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['route', id] });
      queryClient.invalidateQueries({ queryKey: ['routes'] });
    },
  });
}

export function useTriggerScrape(id: number) {
  const queryClient = useQueryClient();
  const [task, setTask] = useState<TriggerScrapeResponse | null>(null);

  const refreshScrapeResults = async () => {
    await Promise.all(
      getScrapeRefreshTargets(id).map((target) =>
        queryClient.invalidateQueries({ ...target, refetchType: 'all' }),
      ),
    );
  };

  const fetchTask = async (taskId: string): Promise<TriggerScrapeResponse> => {
    const { data } = await apiClient.get<TriggerScrapeResponse>(
      `/routes/${id}/scrape/${encodeURIComponent(taskId)}`,
    );
    return data;
  };

  const fetchLatestTask = async (): Promise<TriggerScrapeResponse> => {
    const { data } = await apiClient.get<TriggerScrapeResponse>(`/routes/${id}/scrape/status`);
    return data;
  };

  const mutation = useMutation({
    mutationFn: async (): Promise<TriggerScrapeResponse> => {
      const { data } = await apiClient.post<TriggerScrapeResponse>(`/routes/${id}/scrape`);
      setTask(data);

      if (isScrapeTerminal(data.status)) {
        return data;
      }

      const queuedTask = requireQueuedScrape(data);
      const taskId = queuedTask.task_id as string;
      const deadline = Date.now() + SCRAPE_POLL_TIMEOUT_MS;
      let consecutiveErrors = 0;

      while (Date.now() < deadline) {
        await new Promise<void>((resolve) => {
          window.setTimeout(resolve, SCRAPE_POLL_INTERVAL_MS);
        });

        try {
          const snapshot = await fetchTask(taskId);
          consecutiveErrors = 0;
          setTask(snapshot);
          if (isScrapeTerminal(snapshot.status)) {
            return snapshot;
          }
        } catch (error) {
          consecutiveErrors += 1;

          // A short server restart or task-store race should not immediately lose
          // the visible task. Fall back to the route's latest retained snapshot.
          if (consecutiveErrors >= 2) {
            try {
              const latest = await fetchLatestTask();
              if (latest.task_id === taskId) {
                consecutiveErrors = 0;
                setTask(latest);
                if (isScrapeTerminal(latest.status)) {
                  return latest;
                }
              }
            } catch {
              // Keep the original polling error and retry below.
            }
          }

          if (consecutiveErrors >= 4) {
            throw error;
          }
        }
      }

      // One last status lookup prevents a completion at the timeout boundary
      // from being reported as a frontend timeout.
      try {
        const snapshot = await fetchTask(taskId);
        setTask(snapshot);
        if (isScrapeTerminal(snapshot.status)) {
          return snapshot;
        }
      } catch {
        try {
          const latest = await fetchLatestTask();
          if (latest.task_id === taskId) {
            setTask(latest);
            if (isScrapeTerminal(latest.status)) {
              return latest;
            }
          }
        } catch {
          // The timeout message below is more useful than a final lookup error.
        }
      }

      throw new Error('采集仍在后台运行，但页面等待已超时；请稍后刷新查看最新结果');
    },
    onMutate: () => {
      setTask(null);
    },
    onSuccess: async (result) => {
      if (isScrapeTerminal(result.status)) {
        await refreshScrapeResults();
      }
    },
  });

  return { ...mutation, task };
}

export function useDeleteRoute() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      await apiClient.delete(`/routes/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['routes'] });
    },
  });
}

export function useRouteBatches(id: number, limit = 20) {
  return useQuery({
    queryKey: ['route', id, 'batches', limit],
    queryFn: async (): Promise<RouteBatchesResponse> => {
      const { data } = await apiClient.get<RouteBatchesResponse>(`/routes/${id}/batches`, {
        params: { limit },
      });
      return data;
    },
    staleTime: 2 * 60 * 1000,
  });
}

export function useRouteFlights(
  id: number,
  batchId: string | null,
  source: string | null,
  limit: number,
) {
  return useQuery({
    queryKey: ['route', id, 'flights', batchId, source, limit],
    queryFn: async (): Promise<RouteFlightsResponse> => {
      const params: Record<string, unknown> = { limit };
      if (batchId) params.batch_id = batchId;
      if (source) params.source = source;
      const { data } = await apiClient.get<RouteFlightsResponse>(`/routes/${id}/flights`, {
        params,
      });
      return data;
    },
    staleTime: 2 * 60 * 1000,
  });
}
