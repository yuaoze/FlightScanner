import { motion, useReducedMotion } from 'framer-motion';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { apiClient } from '../../api/client';
import type { WeekendDealItem } from '../../types/radar';
import { DestinationImage } from './DestinationImage';

interface Props {
  deal: WeekendDealItem;
  index: number;
}

function fmtShortDate(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  const weekday = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][date.getDay()];
  return `${date.getMonth() + 1}.${date.getDate()} ${weekday}`;
}

function priceTag(price: number, beatPct: number | null): { label: string; color: string } | null {
  if (beatPct && beatPct >= 85) return { label: `📉 击败${beatPct}%历史价`, color: 'bg-red-500' };
  if (price < 600) return { label: '🔥 骨折白菜价', color: 'bg-orange-500' };
  if (price < 1000) return { label: '✨ 极致性价比', color: 'bg-blue-500' };
  if (price < 2000) return { label: '💎 值得出手', color: 'bg-purple-500' };
  return null;
}

export function DealCard({ deal, index }: Props) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const reduceMotion = useReducedMotion();
  const lockMutation = useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post(`/radar/${deal.id}/lock`);
      return data as { route_id: number; message: string };
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['routes'] });
      void queryClient.invalidateQueries({ queryKey: ['stats'] });
    },
  });

  const tag = priceTag(deal.total_price, deal.beat_pct);
  const headline = deal.ai_brief?.headline || `${deal.destination} 周末逃跑计划`;
  const body = deal.ai_brief?.body ||
    `周五出发、周日返程，往返仅 ¥${Math.round(deal.total_price)}，不请假的短途逃跑。`;

  return (
    <motion.article
      initial={reduceMotion ? false : { opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      exit={reduceMotion ? undefined : { opacity: 0, scale: 0.96 }}
      transition={{ duration: 0.4, delay: reduceMotion ? 0 : Math.min(index * 0.04, 0.6) }}
      whileHover={reduceMotion ? undefined : { y: -4 }}
      className="group flex flex-col overflow-hidden rounded-2xl border border-gray-100 bg-white transition-all hover:shadow-2xl"
    >
      <div className="relative h-40 overflow-hidden" style={{ background: deal.gradient }}>
        <DestinationImage destination={deal.destination} imageUrl={deal.image_url} gradient={deal.gradient} />

        {deal.red_eye && (
          <div className="absolute left-3 top-3 z-10 rounded-full bg-black/40 px-2 py-0.5 text-[10px] text-white/95 backdrop-blur-sm">
            🌙 红眼
          </div>
        )}

        {tag && (
          <div className={`absolute right-3 top-3 z-10 rounded-full px-2 py-0.5 text-[10px] font-medium text-white shadow-md ${tag.color}`}>
            {tag.label}
          </div>
        )}

        <div className="absolute inset-x-0 bottom-0 z-10 flex items-end justify-between px-5 pb-4 text-white">
          <div>
            <div className="flex items-center gap-2">
              <motion.span
                className="inline-block text-3xl drop-shadow-lg"
                whileHover={reduceMotion ? undefined : { scale: 1.15, rotate: -8 }}
                transition={{ type: 'spring', stiffness: 300 }}
              >
                {deal.emoji}
              </motion.span>
              <h3 className="text-xl font-bold drop-shadow-md">{deal.destination}</h3>
            </div>
            {deal.is_international && (
              <span className="mt-1 inline-block rounded bg-white/25 px-1.5 py-0.5 text-[10px] backdrop-blur-sm">
                🌏 国际
              </span>
            )}
          </div>

          <div className="text-right">
            <p className="text-[10px] opacity-85">往返</p>
            <p className="text-3xl font-black leading-none tracking-tight drop-shadow-lg">
              ¥{Math.round(deal.total_price)}
            </p>
            {deal.historical_avg && deal.historical_avg > deal.total_price && (
              <p className="text-[10px] line-through opacity-80">
                均价 ¥{Math.round(deal.historical_avg)}
              </p>
            )}
          </div>
        </div>
      </div>

      <div className="flex flex-1 flex-col p-5">
        <h4 className="mb-1.5 text-sm font-bold leading-snug text-gray-900">{headline}</h4>
        <p className="line-clamp-3 text-xs leading-relaxed text-gray-500">{body}</p>

        {deal.visa_label && (
          <div className="mt-3 inline-block">
            <span
              className={`rounded-md px-2 py-1 text-[10px] font-medium ${
                deal.visa_status === '免签'
                  ? 'bg-emerald-50 text-emerald-700'
                  : deal.visa_status === '落地签'
                    ? 'bg-amber-50 text-amber-700'
                    : 'bg-gray-50 text-gray-600'
              }`}
            >
              {deal.visa_label}
            </span>
          </div>
        )}

        {deal.ai_brief?.tags && deal.ai_brief.tags.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {deal.ai_brief.tags.slice(0, 4).map((tag, tagIndex) => (
              <span key={`${tag}-${tagIndex}`} className="rounded bg-gray-50 px-1.5 py-0.5 text-[10px] text-gray-500">
                #{tag}
              </span>
            ))}
          </div>
        )}

        <div className="mt-4 space-y-1.5 border-t border-gray-50 pt-3">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-gray-400">去 · <time dateTime={deal.outbound_date}>{fmtShortDate(deal.outbound_date)}</time></span>
            <span className="font-mono text-gray-700">
              {deal.outbound_dep_time || '--:--'} → {deal.outbound_arr_time || '--:--'}
            </span>
            <span className="max-w-[80px] truncate text-right text-gray-400">
              {deal.outbound_airline}
            </span>
          </div>
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-gray-400">回 · <time dateTime={deal.return_date}>{fmtShortDate(deal.return_date)}</time></span>
            <span className="font-mono text-gray-700">
              {deal.return_dep_time || '--:--'} → {deal.return_arr_time || '--:--'}
            </span>
            <span className="max-w-[80px] truncate text-right text-gray-400">
              {deal.return_airline}
            </span>
          </div>
        </div>

        <div className="mt-4 flex gap-2">
          {lockMutation.isSuccess ? (
            <button
              type="button"
              onClick={() => navigate(`/route/${lockMutation.data.route_id}`)}
              className="flex-1 rounded-lg bg-gradient-to-r from-emerald-500 to-teal-500 px-3 py-2 text-xs font-semibold text-white shadow transition-shadow hover:shadow-md"
            >
              ✓ 已锁定 · 查看监控
            </button>
          ) : (
            <motion.button
              type="button"
              whileTap={reduceMotion ? undefined : { scale: 0.96 }}
              onClick={() => lockMutation.mutate()}
              disabled={lockMutation.isPending}
              aria-label={`锁定价格 ${deal.destination} ${fmtShortDate(deal.outbound_date)}`}
              className="flex-1 rounded-lg bg-gray-900 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-gray-800 disabled:opacity-60"
            >
              {lockMutation.isPending ? '添加中...' : '❤️ 锁定价格'}
            </motion.button>
          )}
        </div>
        {lockMutation.isError && (
          <p className="mt-1 text-[10px] text-red-500" role="alert">
            {(lockMutation.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '添加失败，请重试'}
          </p>
        )}
      </div>
    </motion.article>
  );
}
