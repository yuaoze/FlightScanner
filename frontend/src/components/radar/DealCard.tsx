import { useState } from 'react';
import { motion } from 'framer-motion';
import { useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { apiClient } from '../../api/client';
import type { WeekendDealItem } from '../../types/radar';

interface Props {
  deal: WeekendDealItem;
  index: number;
}

function fmtShortDate(iso: string): string {
  const d = new Date(iso);
  const weekday = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()];
  return `${d.getMonth() + 1}.${d.getDate()} ${weekday}`;
}

function priceTag(price: number, beat_pct: number | null): { label: string; color: string } | null {
  if (beat_pct && beat_pct >= 85) return { label: `📉 击败${beat_pct}%历史价`, color: 'bg-red-500' };
  if (price < 600) return { label: '🔥 骨折白菜价', color: 'bg-orange-500' };
  if (price < 1000) return { label: '✨ 极致性价比', color: 'bg-blue-500' };
  if (price < 2000) return { label: '💎 值得出手', color: 'bg-purple-500' };
  return null;
}

export function DealCard({ deal, index }: Props) {
  const navigate = useNavigate();
  const [locked, setLocked] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);

  const lockMutation = useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post(`/radar/${deal.id}/lock`);
      return data as { route_id: number; message: string };
    },
    onSuccess: () => setLocked(true),
  });

  const tag = priceTag(deal.total_price, deal.beat_pct);
  const headline = deal.ai_brief?.headline || `${deal.destination} 周末逃跑计划`;
  const body =
    deal.ai_brief?.body ||
    `周五出发、周日返程，往返仅 ¥${Math.round(deal.total_price)}，不请假的短途逃跑。`;

  const hasImage = deal.image_url && !imageFailed;

  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: Math.min(index * 0.04, 0.6) }}
      whileHover={{ y: -4 }}
      className="group bg-white rounded-2xl overflow-hidden border border-gray-100 hover:shadow-2xl transition-all flex flex-col"
    >
      {/* Hero: city image (or gradient fallback) + dark overlay */}
      <div
        className="relative h-40 overflow-hidden"
        style={hasImage ? undefined : { background: deal.gradient }}
      >
        {/* Background image */}
        {hasImage && (
          <img
            src={deal.image_url!}
            alt={deal.destination}
            loading="lazy"
            onError={() => setImageFailed(true)}
            className="absolute inset-0 w-full h-full object-cover group-hover:scale-105 transition-transform duration-700"
          />
        )}

        {/* Dark gradient overlay for text legibility */}
        <div
          className="absolute inset-0"
          style={{
            background: hasImage
              ? 'linear-gradient(180deg, rgba(0,0,0,0.15) 0%, rgba(0,0,0,0.55) 100%)'
              : 'radial-gradient(circle at 20% 30%, rgba(255,255,255,0.25) 0%, transparent 50%), radial-gradient(circle at 80% 80%, rgba(255,255,255,0.2) 0%, transparent 40%)',
          }}
        />

        {/* Top-left red-eye badge */}
        {deal.red_eye && (
          <div className="absolute top-3 left-3 text-[10px] text-white/95 bg-black/40 backdrop-blur-sm px-2 py-0.5 rounded-full z-10">
            🌙 红眼
          </div>
        )}

        {/* Top-right value tag */}
        {tag && (
          <div
            className={`absolute top-3 right-3 text-[10px] text-white font-medium px-2 py-0.5 rounded-full ${tag.color} shadow-md z-10`}
          >
            {tag.label}
          </div>
        )}

        {/* Bottom row: destination + price */}
        <div className="absolute inset-x-0 bottom-0 px-5 pb-4 flex items-end justify-between z-10 text-white">
          <div>
            <div className="flex items-center gap-2">
              <motion.span
                className="text-3xl inline-block drop-shadow-lg"
                whileHover={{ scale: 1.15, rotate: -8 }}
                transition={{ type: 'spring', stiffness: 300 }}
              >
                {deal.emoji}
              </motion.span>
              <span className="text-xl font-bold drop-shadow-md">{deal.destination}</span>
            </div>
            {deal.is_international && (
              <span className="inline-block text-[10px] bg-white/25 backdrop-blur-sm px-1.5 py-0.5 rounded mt-1">
                🌏 国际
              </span>
            )}
          </div>

          <div className="text-right">
            <p className="text-[10px] opacity-85">往返</p>
            <p className="text-3xl font-black drop-shadow-lg tracking-tight leading-none">
              ¥{Math.round(deal.total_price)}
            </p>
            {deal.historical_avg && deal.historical_avg > deal.total_price && (
              <p className="text-[10px] opacity-80 line-through">
                均价 ¥{Math.round(deal.historical_avg)}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="p-5 flex-1 flex flex-col">
        {/* AI headline + body */}
        <h3 className="text-sm font-bold text-gray-900 leading-snug mb-1.5">{headline}</h3>
        <p className="text-xs text-gray-500 leading-relaxed line-clamp-3">{body}</p>

        {/* Visa badge */}
        {deal.visa_label && (
          <div className="mt-3 inline-block">
            <span
              className={`text-[10px] px-2 py-1 rounded-md font-medium ${
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

        {/* AI tags */}
        {deal.ai_brief?.tags && deal.ai_brief.tags.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-2">
            {deal.ai_brief.tags.slice(0, 4).map((t) => (
              <span key={t} className="text-[10px] bg-gray-50 text-gray-500 px-1.5 py-0.5 rounded">
                #{t}
              </span>
            ))}
          </div>
        )}

        {/* Flight meta */}
        <div className="mt-4 pt-3 border-t border-gray-50 space-y-1.5">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-gray-400">去 · {fmtShortDate(deal.outbound_date)}</span>
            <span className="font-mono text-gray-700">
              {deal.outbound_dep_time} → {deal.outbound_arr_time}
            </span>
            <span className="text-gray-400 text-right truncate max-w-[80px]">
              {deal.outbound_airline}
            </span>
          </div>
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-gray-400">回 · {fmtShortDate(deal.return_date)}</span>
            <span className="font-mono text-gray-700">
              {deal.return_dep_time} → {deal.return_arr_time}
            </span>
            <span className="text-gray-400 text-right truncate max-w-[80px]">
              {deal.return_airline}
            </span>
          </div>
        </div>

        {/* CTA */}
        <div className="mt-4 flex gap-2">
          {locked && lockMutation.data ? (
            <button
              onClick={() => navigate(`/route/${lockMutation.data.route_id}`)}
              className="flex-1 px-3 py-2 bg-gradient-to-r from-emerald-500 to-teal-500 text-white text-xs rounded-lg font-semibold shadow hover:shadow-md transition-shadow"
            >
              ✓ 已锁定 · 查看监控
            </button>
          ) : (
            <motion.button
              whileTap={{ scale: 0.96 }}
              onClick={() => lockMutation.mutate()}
              disabled={lockMutation.isPending}
              className="flex-1 px-3 py-2 bg-gray-900 text-white text-xs rounded-lg font-medium hover:bg-gray-800 transition-colors disabled:opacity-60"
            >
              {lockMutation.isPending ? '添加中...' : '❤️ 锁定价格'}
            </motion.button>
          )}
        </div>
        {lockMutation.isError && (
          <p className="text-[10px] text-red-500 mt-1">
            {(lockMutation.error as { response?: { data?: { detail?: string } } })?.response?.data
              ?.detail || '添加失败'}
          </p>
        )}
      </div>
    </motion.div>
  );
}
