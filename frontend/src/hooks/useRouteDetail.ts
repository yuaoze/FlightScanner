import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../api/client';
import type {
  RouteDetailResponse,
  PriceHistoryResponse,
  RoutePredictionsResponse,
  CalendarData,
  RouteBatchesResponse,
  RouteFlightsResponse,
} from '../types';

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
  return useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post(`/routes/${id}/scrape`);
      return data;
    },
  });
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

export function useRouteFlights(id: number, batchId: string | null, limit: number) {
  return useQuery({
    queryKey: ['route', id, 'flights', batchId, limit],
    queryFn: async (): Promise<RouteFlightsResponse> => {
      const params: Record<string, unknown> = { limit };
      if (batchId) params.batch_id = batchId;
      const { data } = await apiClient.get<RouteFlightsResponse>(`/routes/${id}/flights`, {
        params,
      });
      return data;
    },
    staleTime: 2 * 60 * 1000,
  });
}
