import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../../api/client';

interface CookieStatus {
  platform: string;
  label: string;
  exists: boolean;
  valid: boolean;
  count: number;
  updated_at: string | null;
  key_cookies_present: string[];
  key_cookies_missing: string[];
}

interface LoginStateResponse {
  platform: string;
  status: 'idle' | 'starting' | 'qr_ready' | 'success' | 'error';
  message: string;
  qr_base64: string | null;
  done: boolean;
  success: boolean;
  elapsed_seconds: number;
  timeout_seconds: number;
}

function fmtAge(iso: string | null): string {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  const elapsed = (Date.now() - t) / 1000;
  if (elapsed < 60) return '刚刚';
  if (elapsed < 3600) return `${Math.max(Math.floor(elapsed / 60), 1)} 分钟前`;
  if (elapsed < 86400) return `${Math.floor(elapsed / 3600)} 小时前`;
  return `${Math.floor(elapsed / 86400)} 天前`;
}

// ── Upload modal ──────────────────────────────────────────────────────────

function UploadModal({
  platform,
  label,
  onClose,
}: {
  platform: string;
  label: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [content, setContent] = useState('');
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post(`/cookies/${platform}/upload`, { content });
      return data as { platform: string; count: number; message: string };
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cookies', 'status'] });
      onClose();
    },
    onError: (err: { response?: { data?: { detail?: string } } }) => {
      setError(err?.response?.data?.detail || '上传失败');
    },
  });

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl p-5 max-w-lg w-full">
        <h3 className="text-base font-semibold text-gray-900 mb-2">上传 {label} Cookie</h3>
        <p className="text-xs text-gray-500 mb-3">
          支持两种格式：浏览器扩展导出的 JSON 数组，或 DevTools 复制的 <code className="bg-gray-100 px-1 rounded">name=value; ...</code> 原始字符串
        </p>
        <textarea
          value={content}
          onChange={(e) => {
            setContent(e.target.value);
            setError(null);
          }}
          placeholder={'粘贴 Cookie 内容...'}
          className="w-full h-40 px-3 py-2 border border-gray-200 rounded-lg text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-100"
        />
        {error && <p className="text-xs text-red-600 mt-2">{error}</p>}
        <div className="flex gap-2 mt-4">
          <button
            onClick={() => mutation.mutate()}
            disabled={!content.trim() || mutation.isPending}
            className="flex-1 px-3 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50 font-medium"
          >
            {mutation.isPending ? '保存中...' : '保存'}
          </button>
          <button
            onClick={onClose}
            className="flex-1 px-3 py-2 bg-white text-gray-600 text-sm rounded-lg border border-gray-200 hover:bg-gray-50 font-medium"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

// ── QR login dialog ───────────────────────────────────────────────────────

function QrLoginDialog({
  platform,
  label,
  onClose,
}: {
  platform: string;
  label: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [startError, setStartError] = useState<string | null>(null);

  // Kick off login on mount
  useEffect(() => {
    apiClient.post(`/cookies/${platform}/login`).catch((err) => {
      setStartError(err?.response?.data?.detail || '启动失败');
    });
    // Eslint: we want this to run exactly once when the dialog mounts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { data: state } = useQuery({
    queryKey: ['cookies', 'login', platform],
    queryFn: async (): Promise<LoginStateResponse> => {
      const { data } = await apiClient.get<LoginStateResponse>(
        `/cookies/${platform}/login/status`,
      );
      return data;
    },
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === 'success' || s === 'error' ? false : 1500;
    },
  });

  const handleClose = async () => {
    try {
      await apiClient.post(`/cookies/${platform}/login/reset`);
    } catch {
      /* ignore */
    }
    queryClient.invalidateQueries({ queryKey: ['cookies', 'status'] });
    onClose();
  };

  const progressPct =
    state && state.timeout_seconds > 0
      ? Math.min((state.elapsed_seconds / state.timeout_seconds) * 100, 100)
      : 0;

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl p-5 max-w-md w-full">
        <h3 className="text-base font-semibold text-gray-900 mb-3">扫码刷新 {label} Cookie</h3>

        {startError && <p className="text-sm text-red-600 mb-3">{startError}</p>}

        {state?.done ? (
          <div className="text-center py-6">
            {state.success ? (
              <>
                <p className="text-4xl mb-2">✅</p>
                <p className="text-sm text-green-700 font-medium">{state.message}</p>
              </>
            ) : (
              <>
                <p className="text-4xl mb-2">❌</p>
                <p className="text-sm text-red-600">{state.message}</p>
              </>
            )}
          </div>
        ) : (
          <>
            <div className="flex flex-col items-center py-2">
              {state?.qr_base64 ? (
                <img
                  src={`data:image/png;base64,${state.qr_base64}`}
                  alt="QR code"
                  className="w-56 h-56 border border-gray-100 rounded"
                />
              ) : (
                <div className="w-56 h-56 bg-gray-50 rounded flex items-center justify-center text-sm text-gray-400">
                  ⏳ 二维码加载中…
                </div>
              )}
              <p className="text-xs text-gray-600 mt-3">
                {state?.message || '正在启动浏览器…'}
              </p>
            </div>
            <div className="mt-3">
              <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-500 transition-all"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
              <p className="text-[10px] text-gray-400 text-right mt-1">
                {Math.floor(state?.elapsed_seconds || 0)} / {state?.timeout_seconds || 0}s
              </p>
            </div>
          </>
        )}

        <button
          onClick={handleClose}
          className="w-full mt-4 px-3 py-2 bg-white text-gray-600 text-sm rounded-lg border border-gray-200 hover:bg-gray-50 font-medium"
        >
          {state?.done ? '完成' : '取消'}
        </button>
      </div>
    </div>
  );
}

// ── Cookie row ────────────────────────────────────────────────────────────

function CookieRow({ status }: { status: CookieStatus }) {
  const queryClient = useQueryClient();
  const [showUpload, setShowUpload] = useState(false);
  const [showLogin, setShowLogin] = useState(false);

  const deleteMutation = useMutation({
    mutationFn: async () => {
      await apiClient.delete(`/cookies/${status.platform}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cookies', 'status'] });
    },
  });

  const statusText = !status.exists
    ? '未配置'
    : status.valid
      ? `有效 · ${status.count} 条`
      : '已失效';

  const statusColor = !status.exists
    ? 'text-gray-400'
    : status.valid
      ? 'text-green-600'
      : 'text-orange-500';

  const dotColor = !status.exists
    ? 'bg-gray-300'
    : status.valid
      ? 'bg-green-500'
      : 'bg-orange-400';

  return (
    <>
      <div className="py-3 border-b border-gray-50 last:border-0">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${dotColor}`} />
            <span className="text-sm font-medium text-gray-800">{status.label}</span>
            <span className={`text-xs ${statusColor}`}>{statusText}</span>
          </div>
          <span className="text-[11px] text-gray-400">
            {status.exists ? `更新于 ${fmtAge(status.updated_at)}` : '—'}
          </span>
        </div>

        {status.exists && status.key_cookies_missing.length > 0 && (
          <p className="text-[11px] text-orange-500 mb-2">
            缺少关键 Cookie：{status.key_cookies_missing.join(', ')}
          </p>
        )}

        <div className="flex gap-2">
          <button
            onClick={() => setShowLogin(true)}
            className="flex-1 px-3 py-1.5 bg-blue-600 text-white text-xs rounded-lg hover:bg-blue-700 font-medium"
          >
            扫码刷新
          </button>
          <button
            onClick={() => setShowUpload(true)}
            className="flex-1 px-3 py-1.5 bg-white text-gray-700 text-xs rounded-lg border border-gray-200 hover:bg-gray-50 font-medium"
          >
            手动上传
          </button>
          {status.exists && (
            <button
              onClick={() => {
                if (confirm(`确定删除 ${status.label} Cookie？`)) {
                  deleteMutation.mutate();
                }
              }}
              className="px-3 py-1.5 bg-white text-red-600 text-xs rounded-lg border border-red-100 hover:bg-red-50 font-medium"
            >
              清除
            </button>
          )}
        </div>
      </div>

      {showUpload && (
        <UploadModal
          platform={status.platform}
          label={status.label}
          onClose={() => setShowUpload(false)}
        />
      )}
      {showLogin && (
        <QrLoginDialog
          platform={status.platform}
          label={status.label}
          onClose={() => setShowLogin(false)}
        />
      )}
    </>
  );
}

// ── Main card ─────────────────────────────────────────────────────────────

export function CookieCard() {
  const { data, isLoading } = useQuery({
    queryKey: ['cookies', 'status'],
    queryFn: async (): Promise<CookieStatus[]> => {
      const { data } = await apiClient.get<CookieStatus[]>('/cookies/status');
      return data;
    },
    refetchInterval: 30 * 1000,
  });

  return (
    <div className="bg-white rounded-xl border border-gray-100 p-5">
      <h3 className="text-sm font-semibold text-gray-700 mb-1">Cookie 管理</h3>
      <p className="text-[11px] text-gray-400 mb-3">
        去哪儿 Cookie 是 wbdflightlist 接口必需的凭据；携程 Cookie 可提升 API 采集成功率
      </p>

      {isLoading || !data ? (
        <div className="space-y-3">
          {Array.from({ length: 2 }).map((_, i) => (
            <div key={i} className="h-20 bg-gray-50 rounded animate-pulse" />
          ))}
        </div>
      ) : (
        <div>
          {data.map((s) => (
            <CookieRow key={s.platform} status={s} />
          ))}
        </div>
      )}
    </div>
  );
}
