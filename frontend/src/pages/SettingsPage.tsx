import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../api/client';
import { CookieCard } from '../components/settings/CookieCard';

interface SettingsData {
  scraper: {
    scraper_type: string;
    headless: boolean;
    timeout: number;
    retry_count: number;
    max_results_per_platform: number;
  };
  notifications: {
    email: boolean;
    telegram: boolean;
    wecom: boolean;
    feishu: boolean;
  };
  cooldowns: {
    target_hit: number;
    near_30d_low: number;
    rebound_warning: number;
    below_avg: number;
    trend_down: number;
    departure_approaching: number;
  };
  ai: {
    model: string;
    base_url: string;
    api_key_configured: boolean;
  };
  database_url: string;
  notify_below_avg_threshold: number;
}

interface UpdatePayload {
  scraper_type?: string;
  scraper_headless?: boolean;
  scraper_timeout?: number;
  scraper_retry_count?: number;
  max_results_per_platform?: number;
  notify_cooldown_target_hit?: number;
  notify_cooldown_near_30d_low?: number;
  notify_cooldown_rebound_warning?: number;
  notify_cooldown_below_avg?: number;
  notify_cooldown_trend_down?: number;
  notify_cooldown_departure_approaching?: number;
  notify_below_avg_threshold?: number;
  deepseek_model?: string;
  deepseek_base_url?: string;
  deepseek_api_key?: string;
  smtp_host?: string;
  smtp_port?: number;
  smtp_user?: string;
  smtp_password?: string;
  telegram_bot_token?: string;
  telegram_chat_id?: string;
  wecom_webhook_url?: string;
  feishu_webhook_url?: string;
  feishu_webhook_secret?: string;
}

function useSettings() {
  return useQuery({
    queryKey: ['settings'],
    queryFn: async (): Promise<SettingsData> => {
      const { data } = await apiClient.get<SettingsData>('/settings');
      return data;
    },
  });
}

function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: UpdatePayload): Promise<SettingsData> => {
      const { data } = await apiClient.put<SettingsData>('/settings', payload);
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['settings'], data);
    },
  });
}

function SettingCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl border border-gray-100 p-5">
      <h3 className="text-sm font-semibold text-gray-700 mb-4">{title}</h3>
      {children}
    </div>
  );
}

function StatusDot({ active }: { active: boolean }) {
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full ${active ? 'bg-green-500' : 'bg-gray-300'}`}
    />
  );
}

function NumberField({
  label,
  value,
  onChange,
  suffix,
  min,
  max,
  step = 1,
}: {
  label: string;
  value: number | string;
  onChange: (v: number | undefined) => void;
  suffix?: string;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <div className="flex justify-between items-center py-2 gap-3">
      <span className="text-xs text-gray-500 flex-shrink-0">{label}</span>
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={(e) => {
            const v = e.target.value;
            onChange(v === '' ? undefined : Number(v));
          }}
          className="w-24 px-2 py-1 border border-gray-200 rounded text-xs text-right focus:outline-none focus:ring-2 focus:ring-blue-100"
        />
        {suffix && <span className="text-xs text-gray-400">{suffix}</span>}
      </div>
    </div>
  );
}

function TextField({
  label,
  value,
  onChange,
  placeholder,
  type = 'text',
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <div className="py-2">
      <label className="text-xs text-gray-500 block mb-1">{label}</label>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full px-2 py-1 border border-gray-200 rounded text-xs focus:outline-none focus:ring-2 focus:ring-blue-100"
      />
    </div>
  );
}

function Toggle({ active, onChange }: { active: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!active)}
      className={`relative w-10 h-5 rounded-full transition-colors ${
        active ? 'bg-blue-600' : 'bg-gray-300'
      }`}
    >
      <span
        className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
          active ? 'translate-x-5' : 'translate-x-0.5'
        }`}
      />
    </button>
  );
}

export function SettingsPage() {
  const { data: settings, isLoading } = useSettings();
  const update = useUpdateSettings();
  const [draft, setDraft] = useState<UpdatePayload>({});

  // Keep an "extra channels" toggle so user can reveal credential inputs
  const [showCredentials, setShowCredentials] = useState(false);

  // Reset draft after successful save so the inputs reflect persisted state.
  useEffect(() => {
    if (update.isSuccess) {
      setDraft({});
    }
  }, [update.isSuccess]);

  if (isLoading || !settings) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-bold text-gray-900 mb-6">设置中心</h1>
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="bg-white rounded-xl border border-gray-100 p-5 animate-pulse">
            <div className="h-4 bg-gray-100 rounded w-1/4 mb-4" />
            <div className="space-y-3">
              <div className="h-3 bg-gray-50 rounded w-full" />
              <div className="h-3 bg-gray-50 rounded w-2/3" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  const get = <K extends keyof UpdatePayload>(key: K, fallback: NonNullable<UpdatePayload[K]>) =>
    (draft[key] as UpdatePayload[K]) ?? fallback;
  const set = <K extends keyof UpdatePayload>(key: K, value: UpdatePayload[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  const handleSave = () => {
    if (Object.keys(draft).length === 0) return;
    update.mutate(draft);
  };

  const handleDiscard = () => {
    setDraft({});
    update.reset();
  };

  const dirtyCount = Object.keys(draft).length;
  const dirty = dirtyCount > 0;

  return (
    <div className="pb-24">
      <div className="mb-6">
        <h1 className="text-xl font-bold text-gray-900">设置中心</h1>
        <p className="text-sm text-gray-400 mt-0.5">
          配置爬虫、通知、AI 等参数（保存到 .env 并即时生效）
        </p>
      </div>

      {update.isError && (
        <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-3 text-xs text-red-700">
          {(update.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
            '保存失败'}
        </div>
      )}
      {update.isSuccess && (
        <div className="mb-4 bg-green-50 border border-green-200 rounded-lg p-3 text-xs text-green-700">
          ✓ 已保存，配置已写入 .env 并应用到运行中实例
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Cookie Management */}
        <div className="lg:col-span-2">
          <CookieCard />
        </div>

        {/* Scraper Settings */}
        <SettingCard title="爬虫设置">
          <div className="py-2">
            <label className="text-xs text-gray-500 block mb-1">启用平台（逗号分隔，可选 qunar/ctrip）</label>
            <input
              type="text"
              value={get('scraper_type', settings.scraper.scraper_type) as string}
              onChange={(e) => set('scraper_type', e.target.value)}
              className="w-full px-2 py-1 border border-gray-200 rounded text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-100"
            />
          </div>
          <div className="flex justify-between items-center py-2">
            <span className="text-xs text-gray-500">无头模式</span>
            <Toggle
              active={get('scraper_headless', settings.scraper.headless) as boolean}
              onChange={(v) => set('scraper_headless', v)}
            />
          </div>
          <NumberField
            label="超时时间"
            value={get('scraper_timeout', settings.scraper.timeout) as number}
            onChange={(v) => set('scraper_timeout', v)}
            suffix="ms"
            min={5000}
            max={120000}
            step={1000}
          />
          <NumberField
            label="重试次数"
            value={get('scraper_retry_count', settings.scraper.retry_count) as number}
            onChange={(v) => set('scraper_retry_count', v)}
            min={0}
            max={10}
          />
          <NumberField
            label="每平台最大结果"
            value={get('max_results_per_platform', settings.scraper.max_results_per_platform) as number}
            onChange={(v) => set('max_results_per_platform', v)}
            min={1}
            max={200}
          />
        </SettingCard>

        {/* Notification Channels */}
        <SettingCard title="通知渠道">
          <div className="space-y-2.5">
            <div className="flex items-center gap-2">
              <StatusDot active={settings.notifications.email} />
              <span className="text-xs text-gray-600">邮件 (SMTP)</span>
              <span className="text-[10px] text-gray-400 ml-auto">
                {settings.notifications.email ? '已配置' : '未配置'}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <StatusDot active={settings.notifications.telegram} />
              <span className="text-xs text-gray-600">Telegram Bot</span>
              <span className="text-[10px] text-gray-400 ml-auto">
                {settings.notifications.telegram ? '已配置' : '未配置'}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <StatusDot active={settings.notifications.wecom} />
              <span className="text-xs text-gray-600">企业微信</span>
              <span className="text-[10px] text-gray-400 ml-auto">
                {settings.notifications.wecom ? '已配置' : '未配置'}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <StatusDot active={settings.notifications.feishu} />
              <span className="text-xs text-gray-600">飞书</span>
              <span className="text-[10px] text-gray-400 ml-auto">
                {settings.notifications.feishu ? '已配置' : '未配置'}
              </span>
            </div>
          </div>

          <button
            onClick={() => setShowCredentials((s) => !s)}
            className="mt-3 text-[11px] text-blue-600 hover:underline"
          >
            {showCredentials ? '收起' : '展开编辑凭据 ↓'}
          </button>

          {showCredentials && (
            <div className="mt-3 pt-3 border-t border-gray-50 space-y-1">
              <p className="text-[11px] text-gray-400 mb-2">
                凭据写入 .env，留空则保留原值；输入新值会覆盖
              </p>
              <TextField
                label="SMTP Host"
                value={get('smtp_host', '') as string}
                onChange={(v) => set('smtp_host', v)}
                placeholder="smtp.example.com"
              />
              <TextField
                label="SMTP User"
                value={get('smtp_user', '') as string}
                onChange={(v) => set('smtp_user', v)}
                placeholder="user@example.com"
              />
              <TextField
                label="SMTP Password"
                type="password"
                value={get('smtp_password', '') as string}
                onChange={(v) => set('smtp_password', v)}
                placeholder="••••••••"
              />
              <TextField
                label="Telegram Bot Token"
                type="password"
                value={get('telegram_bot_token', '') as string}
                onChange={(v) => set('telegram_bot_token', v)}
                placeholder="123456:ABC..."
              />
              <TextField
                label="Telegram Chat ID"
                value={get('telegram_chat_id', '') as string}
                onChange={(v) => set('telegram_chat_id', v)}
              />
              <TextField
                label="WeCom Webhook URL"
                value={get('wecom_webhook_url', '') as string}
                onChange={(v) => set('wecom_webhook_url', v)}
                placeholder="https://qyapi.weixin.qq.com/..."
              />
              <TextField
                label="Feishu Webhook URL"
                value={get('feishu_webhook_url', '') as string}
                onChange={(v) => set('feishu_webhook_url', v)}
              />
              <TextField
                label="Feishu Webhook Secret"
                type="password"
                value={get('feishu_webhook_secret', '') as string}
                onChange={(v) => set('feishu_webhook_secret', v)}
              />
            </div>
          )}
        </SettingCard>

        {/* Cooldown Settings */}
        <SettingCard title="通知冷却时间">
          <NumberField
            label="目标价触发"
            value={get('notify_cooldown_target_hit', settings.cooldowns.target_hit) as number}
            onChange={(v) => set('notify_cooldown_target_hit', v)}
            suffix="h"
            min={0}
            step={0.5}
          />
          <NumberField
            label="接近30天低点"
            value={get('notify_cooldown_near_30d_low', settings.cooldowns.near_30d_low) as number}
            onChange={(v) => set('notify_cooldown_near_30d_low', v)}
            suffix="h"
            min={0}
            step={0.5}
          />
          <NumberField
            label="反弹预警"
            value={get('notify_cooldown_rebound_warning', settings.cooldowns.rebound_warning) as number}
            onChange={(v) => set('notify_cooldown_rebound_warning', v)}
            suffix="h"
            min={0}
            step={0.5}
          />
          <NumberField
            label="低于均价"
            value={get('notify_cooldown_below_avg', settings.cooldowns.below_avg) as number}
            onChange={(v) => set('notify_cooldown_below_avg', v)}
            suffix="h"
            min={0}
            step={0.5}
          />
          <NumberField
            label="趋势下降"
            value={get('notify_cooldown_trend_down', settings.cooldowns.trend_down) as number}
            onChange={(v) => set('notify_cooldown_trend_down', v)}
            suffix="h"
            min={0}
            step={0.5}
          />
          <NumberField
            label="临近出发"
            value={
              get('notify_cooldown_departure_approaching', settings.cooldowns.departure_approaching) as number
            }
            onChange={(v) => set('notify_cooldown_departure_approaching', v)}
            suffix="h"
            min={0}
            step={0.5}
          />
          <NumberField
            label="低于均价阈值"
            value={
              get('notify_below_avg_threshold', settings.notify_below_avg_threshold) as number
            }
            onChange={(v) => set('notify_below_avg_threshold', v)}
            suffix="%"
            min={0}
            max={100}
            step={0.5}
          />
        </SettingCard>

        {/* AI Settings */}
        <SettingCard title="AI 配置">
          <TextField
            label="模型"
            value={get('deepseek_model', settings.ai.model) as string}
            onChange={(v) => set('deepseek_model', v)}
            placeholder="deepseek-chat"
          />
          <TextField
            label="API Base URL"
            value={get('deepseek_base_url', settings.ai.base_url) as string}
            onChange={(v) => set('deepseek_base_url', v)}
            placeholder="https://api.deepseek.com"
          />
          <TextField
            label="API Key (留空保留原值)"
            type="password"
            value={get('deepseek_api_key', '') as string}
            onChange={(v) => set('deepseek_api_key', v)}
            placeholder={settings.ai.api_key_configured ? '已配置 ****' : '未配置'}
          />
          <div className="flex items-center gap-2 pt-2">
            <StatusDot active={settings.ai.api_key_configured} />
            <span className="text-xs text-gray-600">
              API Key {settings.ai.api_key_configured ? '已配置' : '未配置'}
            </span>
          </div>
        </SettingCard>

        {/* DB info — read-only */}
        <SettingCard title="数据库">
          <div className="py-2 flex justify-between">
            <span className="text-xs text-gray-500">连接 URL</span>
            <span className="text-xs text-gray-700 font-mono break-all text-right">
              {settings.database_url}
            </span>
          </div>
          <p className="text-[11px] text-gray-400 mt-1">数据库地址为只读，需在 .env 直接修改后重启服务</p>
        </SettingCard>
      </div>

      {/* Sticky save/discard bar — shows only when there are unsaved changes */}
      {dirty && (
        <div className="fixed bottom-0 left-60 right-0 z-40 px-8 py-3 bg-white/95 backdrop-blur border-t border-gray-200 shadow-[0_-4px_16px_rgba(0,0,0,0.04)]">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
              <span className="text-sm text-gray-700">
                <span className="font-semibold">{dirtyCount}</span> 项未保存修改
              </span>
              <span className="text-xs text-gray-400 hidden md:inline">
                · 修改后需要保存才会写入 .env 并生效
              </span>
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleDiscard}
                disabled={update.isPending}
                className="px-4 py-2 text-sm rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50 disabled:opacity-50 transition-colors"
              >
                放弃修改
              </button>
              <button
                onClick={handleSave}
                disabled={update.isPending}
                className="px-5 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {update.isPending ? '保存中...' : '保存修改'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
