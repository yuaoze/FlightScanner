import { apiClient } from './client';
import type { RouteResponse, StatsResponse } from '../types';

export async function fetchRoutes(): Promise<RouteResponse[]> {
  const { data } = await apiClient.get<RouteResponse[]>('/routes');
  return data;
}

export async function fetchStats(): Promise<StatsResponse> {
  const { data } = await apiClient.get<StatsResponse>('/stats');
  return data;
}
