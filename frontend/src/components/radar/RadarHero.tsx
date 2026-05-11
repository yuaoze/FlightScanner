import { motion } from 'framer-motion';

interface Props {
  total: number;
  latestScanAt: string | null;
  isScanning: boolean;
  onScan: () => void;
}

function fmtAge(iso: string | null): string {
  if (!iso) return '暂无扫描';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 3600) return `${Math.max(Math.floor(diff / 60), 1)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

export function RadarHero({ total, latestScanAt, isScanning, onScan }: Props) {
  return (
    <div className="relative overflow-hidden rounded-2xl mb-6">
      {/* Animated gradient background */}
      <div
        className="absolute inset-0"
        style={{
          background:
            'linear-gradient(135deg, #0f172a 0%, #1e293b 35%, #4c1d95 75%, #7c3aed 100%)',
        }}
      />

      {/* Radar sweep */}
      <motion.div
        className="absolute top-1/2 right-8 w-72 h-72 -translate-y-1/2 opacity-25 hidden md:block pointer-events-none"
        animate={{ rotate: 360 }}
        transition={{ duration: 12, repeat: Infinity, ease: 'linear' }}
      >
        <div
          className="w-full h-full rounded-full"
          style={{
            background:
              'conic-gradient(from 0deg, rgba(139,92,246,0) 0%, rgba(139,92,246,0.6) 20%, rgba(139,92,246,0) 50%)',
          }}
        />
      </motion.div>

      {/* Concentric rings */}
      <div className="absolute top-1/2 right-20 -translate-y-1/2 hidden md:block pointer-events-none">
        {[1, 2, 3].map((r) => (
          <motion.div
            key={r}
            className="absolute rounded-full border border-purple-400/25"
            style={{
              width: `${r * 80}px`,
              height: `${r * 80}px`,
              top: `-${r * 40}px`,
              left: `-${r * 40}px`,
            }}
            animate={{ scale: [1, 1.08, 1], opacity: [0.25, 0.45, 0.25] }}
            transition={{ duration: 3, delay: r * 0.4, repeat: Infinity, ease: 'easeInOut' }}
          />
        ))}
        <motion.div
          className="absolute w-2 h-2 bg-purple-300 rounded-full"
          style={{ top: -4, left: -4 }}
          animate={{ scale: [1, 1.8, 1], boxShadow: ['0 0 0 0 rgba(167,139,250,0.8)', '0 0 0 12px rgba(167,139,250,0)', '0 0 0 0 rgba(167,139,250,0.8)'] }}
          transition={{ duration: 2, repeat: Infinity }}
        />
      </div>

      {/* Floating emoji accents */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        {['🌴', '✈️', '🗾', '🏝️', '🏙️'].map((e, i) => (
          <motion.span
            key={i}
            className="absolute text-2xl opacity-30"
            style={{
              left: `${15 + i * 18}%`,
              top: `${20 + (i % 3) * 25}%`,
            }}
            animate={{ y: [0, -12, 0], rotate: [0, 8, 0] }}
            transition={{ duration: 4 + i * 0.5, repeat: Infinity, ease: 'easeInOut', delay: i * 0.3 }}
          >
            {e}
          </motion.span>
        ))}
      </div>

      {/* Content */}
      <div className="relative z-10 px-6 md:px-8 py-8 md:py-10">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
        >
          <span className="inline-block text-[11px] font-mono tracking-wider text-purple-300/90 uppercase mb-2">
            Weekend Inspiration Radar
          </span>
          <h1 className="text-2xl md:text-4xl font-bold text-white leading-tight">
            这个周末，<span className="bg-gradient-to-r from-pink-300 to-purple-200 bg-clip-text text-transparent">飞到哪里都不晚</span>
          </h1>
          <p className="text-sm text-purple-100/80 mt-2 max-w-xl">
            AI 已为你锁定未来 8 个周末、30+ 个城市的性价比航线 —— 周五下班出发，周日晚归，不请假也能出逃。
          </p>

          <div className="flex items-center gap-4 mt-5 flex-wrap">
            <motion.button
              onClick={onScan}
              disabled={isScanning}
              whileHover={{ scale: isScanning ? 1 : 1.03 }}
              whileTap={{ scale: 0.97 }}
              className="px-5 py-2.5 rounded-lg bg-white text-gray-900 text-sm font-semibold shadow-lg hover:shadow-xl transition-shadow disabled:opacity-60 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {isScanning ? (
                <>
                  <motion.span
                    animate={{ rotate: 360 }}
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
              <span className="text-white font-semibold">{total}</span> 条灵感 · 更新于 {fmtAge(latestScanAt)}
            </div>
          </div>
        </motion.div>
      </div>
    </div>
  );
}
