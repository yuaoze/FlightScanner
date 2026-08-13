import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  analyzePurchase,
  cancelPlan,
  confirmPlan,
  createPlan,
  fetchPlans,
  fetchPurchaseDetail,
  fetchPurchases,
  instantBuy,
  type ConfirmPlanBody,
  type CreatePlanBody,
  type InstantBuyBody,
} from '../api/purchases';

// ── 买入记录 ────────────────────────────────────────────────────────────────

export function usePurchases(status?: string, routeId?: number) {
  return useQuery({
    queryKey: ['purchases', status ?? 'all', routeId ?? 'all'],
    queryFn: () =>
      fetchPurchases({
        ...(status ? { status } : {}),
        ...(routeId ? { route_id: routeId } : {}),
      }),
    staleTime: 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  });
}

export function usePurchaseDetail(id: number | null) {
  return useQuery({
    queryKey: ['purchase', id],
    queryFn: () => fetchPurchaseDetail(id as number),
    enabled: id != null,
    staleTime: 60 * 1000,
  });
}

export function useInstantBuy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: InstantBuyBody) => instantBuy(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['purchases'] });
      queryClient.invalidateQueries({ queryKey: ['routes'] });
    },
  });
}

export function useAnalyzePurchase() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => analyzePurchase(id),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: ['purchases'] });
      queryClient.invalidateQueries({ queryKey: ['purchase', id] });
      queryClient.invalidateQueries({ queryKey: ['experiences'] });
    },
  });
}

// ── 买入计划 ────────────────────────────────────────────────────────────────

export function usePlans(status?: string, routeId?: number) {
  return useQuery({
    queryKey: ['plans', status ?? 'all', routeId ?? 'all'],
    queryFn: () =>
      fetchPlans({
        ...(status ? { status } : {}),
        ...(routeId ? { route_id: routeId } : {}),
      }),
    staleTime: 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  });
}

export function useCreatePlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreatePlanBody) => createPlan(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['plans'] });
    },
  });
}

export function useCancelPlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => cancelPlan(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['plans'] });
    },
  });
}

export function useConfirmPlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: ConfirmPlanBody }) => confirmPlan(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['plans'] });
      queryClient.invalidateQueries({ queryKey: ['purchases'] });
    },
  });
}
