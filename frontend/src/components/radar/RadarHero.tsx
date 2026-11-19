import { motion, useReducedMotion } from 'framer-motion';

interface Props {
  total: number;
  latestScanAt: string | null;
  isScanning: boolean;
  onScan: () => void;
}

function fmtAge(iso: string | null): string {
  if (!iso) return '暂无扫描';
  const diff = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.max(Math.floor(diff / 60), 1)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

export function RadarHero({ total, latestScanAt, isScanning, onScan }: Props) {
  const reduceMotion = useReducedMotion();

  return (
    <div className="relative mb-6 overflow-hidden rounded-2xl">
      <div
        className="absolute inset-0"
        style={{
          background:
            'linear-gradient(135deg, #0f172a 0%, #1e293b 35%, #4c1d95 75%, #7c3aed 100%)',
        }}
      />

      <motion.div
        className="pointer-events-none absolute right-8 top-1/2 hidden h-72 w-72 -translate-y-1/2 opacity-25 md:block"
        animate={reduceMotion ? undefined : { rotate: 360 }}
        transition={{ duration: 12, repeat: Infinity, ease: 'linear' }}
      >
        <div
          className="h-full w-full rounded-full"
          style={{
            background:
              'conic-gradient(from 0deg, rgba(139,92,246,0) 0%, rgba(139,92,246,0.6) 20%, rgba(139,92,246,0) 50%)',
          }}
        />
      </motion.div>

      <div className="pointer-events-none absolute right-20 top-1/2 hidden -translate-y-1/2 md:block">
        {[1, 2, 3].map((ring) => (
          <motion.div
            key={ring}
            className="absolute rounded-full border border-purple-400/25"
            style={{
              width: `${ring * 80}px`,
              height: `${ring * 80}px`,
              top: `-${ring * 40}px`,
              left: `-${ring * 40}px`,
            }}
            animate={reduceMotion ? undefined : { scale: [1, 1.08, 1], opacity: [0.25, 0.45, 0.25] }}
            transition={{ duration: 3, delay: ring * 0.4, repeat: Infinity, ease: 'easeInOut' }}
          />
        ))}
        <motion.div
          className="absolute h-2 w-2 rounded-full bg-purple-300"
          style={{ top: -4, left: -4 }}
          animate={reduceMotion ? undefined : {
            scale: [1, 1.8, 1],
            boxShadow: [
              '0 0 0 0 rgba(167,139,250,0.8)',
              '0 0 0 12px rgba(167,139,250,0)',
              '0 0 0 0 rgba(167,139,250,0.8)',
            ],
          }}
          transition={{ duration: 2, repeat: Infinity }}
        />
      </div>

      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        {['🌴', '✈️', '🗾', '🏝️', '🏙️'].map((emoji, index) => (
          <motion.span
            key={emoji}
            className="absolute text-2xl opacity-30"
            style={{ left: `${15 + index * 18}%`, top: `${20 + (index % 3) * 25}%` }}
            animate={reduceMotion ? undefined : { y: [0, -12, 0], rotate: [0, 8, 0] }}
            transition={{ duration: 4 + index * 0.5, repeat: Infinity, ease: 'easeInOut', delay: index * 0.3 }}
          >
            {emoji}
          </motion.span>
        ))}
      </div>

      <div className="relative z-10 px-6 py-8 md:px-8 md:py-10">
        <motion.div
          initial={reduceMotion ? false : { opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
        >
          <span className="mb-2 inline-block font-mono text-[11px] uppercase tracking-wider text-purple-300/90">
            Weekend Inspiration Radar
          </span>
          <h1 className="text-2xl font-bold leading-tight text-white md:text-4xl">
            这个周末，<span className="bg-gradient-to-r from-pink-300 to-purple-200 bg-clip-text text-transparent">飞到哪里都不晚</span>
          </h1>
          <p className="mt-2 max-w-xl text-sm text-purple-100/80">
            AI 已为你锁定未来 8 个周末、30+ 个城市的性价比航线 —— 周五下班出发，周日晚归，不请假也能出逃。
          </p>

          <div className="mt-5 flex flex-wrap items-center gap-4">
            <motion.button
              type="button"
              onClick={onScan}
              disabled={isScanning}
              whileHover={{ scale: isScanning || reduceMotion ? 1 : 1.03 }}
              whileTap={reduceMotion ? undefined : { scale: 0.97 }}
              className="flex items-center gap-2 rounded-lg bg-white px-5 py-2.5 text-sm font-semibold text-gray-900 shadow-lg transition-shadow hover:shadow-xl disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isScanning ? (
                <>
                  <motion.span
                    animate={reduceMotion ? undefined : { rotate: 360 }}
                    transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                    className="inline-block"
                  >
                    🛰️
                  </motion.span>
                  扫描中…
                </>
              ) : (
                <>🛰️ 立即重新扫描</>
              )}
            </motion.button>

            <div className="text-xs text-purple-100/70">
              <span className="font-semibold text-white">{total}</span> 条灵感 · 更新于 {fmtAge(latestScanAt)}
            </div>
          </div>
        </motion.div>
      </div>
    </div>
  );
}
