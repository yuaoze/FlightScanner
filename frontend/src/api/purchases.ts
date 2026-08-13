import { apiClient } from './client';
import type {
  BuyPlan,
  BuyPointAnalysis,
  ExperienceEntry,
  PurchaseDetail,
  PurchaseRecordItem,
} from '../types';

// ── 买入记录 ────────────────────────────────────────────────────────────────

export interface InstantBuyBody {
  route_id: number;
  /** 单人可比票价；price 同时保留用于兼容旧版 API。 */
  unit_price: number;
  price?: number;
  total_paid: number;
  seat_class?: string;
  passengers?: number;
  purchased_at: string;
  notes?: string;
}

export async function instantBuy(body: InstantBuyBody): Promise<PurchaseRecordItem> {
  const { data } = await apiClient.post<PurchaseRecordItem>('/purchases/instant', {
    ...body,
    price: body.price ?? body.unit_price,
  });
  return data;
}

export async function fetchPurchases(params?: {
  status?: string;
  route_id?: number;
}): Promise<PurchaseRecordItem[]> {
  const { data } = await apiClient.get<PurchaseRecordItem[]>('/purchases', { params });
  return data;
}

export async function fetchPurchaseDetail(id: number): Promise<PurchaseDetail> {
  const { data } = await apiClient.get<PurchaseDetail>(`/purchases/${id}`);
  return data;
}

export async function analyzePurchase(id: number): Promise<BuyPointAnalysis> {
  const { data } = await apiClient.post<BuyPointAnalysis>(
    `/purchases/${id}/analyze`,
    undefined,
    { timeout: 60_000 },
  );
  return data;
}

// ── 买入计划 ────────────────────────────────────────────────────────────────

export interface CreatePlanBody {
  route_id: number;
  plan_price?: number;
  plan_execute_by?: string;
}

export interface ConfirmPlanBody {
  /** 单人可比票价；price/actual_price 用于兼容不同版本的 API。 */
  unit_price: number;
  price?: number;
  actual_price?: number;
  total_paid: number;
  seat_class?: string;
  passengers?: number;
  purchased_at: string;
  notes?: string;
}

export async function createPlan(body: CreatePlanBody): Promise<BuyPlan> {
  const { data } = await apiClient.post<BuyPlan>('/plans', body);
  return data;
}

export async function fetchPlans(params?: {
  status?: string;
  route_id?: number;
}): Promise<BuyPlan[]> {
  const { data } = await apiClient.get<BuyPlan[]>('/plans', { params });
  return data;
}

export async function cancelPlan(id: number): Promise<void> {
  await apiClient.delete(`/plans/${id}`);
}

export async function confirmPlan(id: number, body: ConfirmPlanBody): Promise<PurchaseRecordItem> {
  const { data } = await apiClient.post<PurchaseRecordItem>(`/plans/${id}/confirm`, {
    ...body,
    price: body.price ?? body.unit_price,
    actual_price: body.actual_price ?? body.unit_price,
  });
  return data;
}

// ── 经验库 ──────────────────────────────────────────────────────────────────

export interface ExperienceBody {
  title: string;
  content: string;
  route_pattern?: string;
  category?: string;
}

export interface ExperienceUpdateBody {
  title?: string;
  content?: string;
  route_pattern?: string;
  category?: string;
  status?: string;
}

export async function fetchExperiences(params?: {
  status?: string;
  route_pattern?: string;
}): Promise<ExperienceEntry[]> {
  const { data } = await apiClient.get<ExperienceEntry[]>('/experiences', { params });
  return data;
}

export async function createExperience(body: ExperienceBody): Promise<ExperienceEntry> {
  const { data } = await apiClient.post<ExperienceEntry>('/experiences', body);
  return data;
}

export async function updateExperience(
  id: number,
  body: ExperienceUpdateBody,
): Promise<ExperienceEntry> {
  const { data } = await apiClient.put<ExperienceEntry>(`/experiences/${id}`, body);
  return data;
}

export async function deleteExperience(id: number): Promise<void> {
  await apiClient.delete(`/experiences/${id}`);
}
