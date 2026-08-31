import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUpdateRoute, useTriggerScrape, useDeleteRoute } from '../../hooks/useRouteDetail';
import type { UpdateRouteBody } from '../../hooks/useRouteDetail';
import type {
  RouteDetailResponse,
  ScrapePlatformResult,
  ScrapeTaskStatus,
  TriggerScrapeResponse,
} from '../../types';
import { ArrivalDayLimitSelect } from '../ArrivalDayLimitSelect';

interface Props {
  routeId: number;
  route: RouteDetailResponse;
}

const HHMM_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

const SCRAPE_STATUS_LABELS: Record<ScrapeTaskStatus, string> = {
  queued: '排队中',
  running: '采集中',
  completed: '已完成',
  partial: '部分完成',
  failed: '失败',
};

const SCRAPE_STATUS_STYLES: Record<ScrapeTaskStatus, string> = {
  queued: 'bg-gray-100 text-gray-600',
  running: 'bg-blue-50 text-blue-700',
  completed: 'bg-green-50 text-green-700',
  partial: 'bg-amber-50 text-amber-700',
  failed: 'bg-red-50 text-red-700',
};

function ScrapeStatusBadge({ status }: { status: ScrapeTaskStatus }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${SCRAPE_STATUS_STYLES[status]}`}>
      {SCRAPE_STATUS_LABELS[status]}
    </span>
  );
}

function ScrapePlatformRow({ result }: { result: ScrapePlatformResult }) {
  return (
    <div className="rounded-lg border border-gray-100 bg-white px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-xs font-medium text-gray-700">{result.display_name}</span>
          <ScrapeStatusBadge status={result.status} />
        </div>
        <span className="shrink-0 text-xs tabular-nums text-gray-500">{result.count} 条</span>
      </div>
      {result.warning && <p className="mt-1 text-[11px] leading-4 text-amber-700">{result.warning}</p>}
      {result.error && <p className="mt-1 break-words text-[11px] leading-4 text-red-600">{result.error}</p>}
    </div>
  );
}

function ScrapeTaskPanel({ task }: { task: TriggerScrapeResponse }) {
  const summaryStyle =
    task.status === 'failed'
      ? 'border-red-100 bg-red-50/60 text-red-700'
      : task.status === 'partial'
        ? 'border-amber-100 bg-amber-50/60 text-amber-700'
        : task.status === 'completed'
          ? 'border-green-100 bg-green-50/60 text-green-700'
          : 'border-blue-100 bg-blue-50/60 text-blue-700';

  const summary =
    task.status === 'completed'
      ? `采集完成，共获取 ${task.total_count} 条报价`
      : task.status === 'partial'
        ? `部分平台采集失败，共获取 ${task.total_count} 条报价`
        : task.status === 'failed'
          ? task.error || task.message || '采集任务失败'
          : task.message || (task.status === 'queued' ? '采集任务已排队' : '正在采集各平台价格');

  return (
    <div className={`rounded-lg border p-3 ${summaryStyle}`}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-medium leading-5">{summary}</p>
        <ScrapeStatusBadge status={task.status} />
      </div>
      {task.platforms.length > 0 && (
        <div className="mt-2 space-y-2">
          {task.platforms.map((result) => (
            <ScrapePlatformRow key={result.platform} result={result} />
          ))}
        </div>
      )}
    </div>
  );
}

function getMutationErrorMessage(error: unknown): string {
  const apiError = error as {
    message?: string;
    response?: { data?: { detail?: string; message?: string } };
  };
  return (
    apiError.response?.data?.detail ||
    apiError.response?.data?.message ||
    apiError.message ||
    '采集任务提交或状态查询失败'
  );
}

function TimeInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
}) {
  return (
    <input
      type="time"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className="w-full px-2 py-1.5 border border-gray-200 rounded text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-100"
    />
  );
}

function TimeRow({
  label,
  fromValue,
  toValue,
  onFromChange,
  onToChange,
}: {
  label: string;
  fromValue: string;
  toValue: string;
  onFromChange: (v: string) => void;
  onToChange: (v: string) => void;
}) {
  return (
    <div className="grid grid-cols-1 items-center gap-2 sm:grid-cols-[80px_1fr_auto_1fr]">
      <span className="text-xs text-gray-500">{label}</span>
      <TimeInput value={fromValue} onChange={onFromChange} placeholder="HH:MM" />
      <span className="text-xs text-gray-400">至</span>
      <TimeInput value={toValue} onChange={onToChange} placeholder="HH:MM" />
    </div>
  );
}

export function ConfigTab({ routeId, route }: Props) {
  const navigate = useNavigate();
  const updateMutation = useUpdateRoute(routeId);
  const scrapeMutation = useTriggerScrape(routeId);
  const deleteMutation = useDeleteRoute();

  const [targetPrice, setTargetPrice] = useState(route.target_price.toString());
  const [scrapeInterval, setScrapeInterval] = useState(route.scrape_interval.toString());
  const [isActive, setIsActive] = useState(route.is_active);

  // Time-window state — empty string represents "no constraint" (cleared).
  const [depFrom, setDepFrom] = useState(route.dep_time_from ?? '');
  const [depTo, setDepTo] = useState(route.dep_time_to ?? '');
  const [arrFrom, setArrFrom] = useState(route.arr_time_from ?? '');
  const [arrTo, setArrTo] = useState(route.arr_time_to ?? '');
  const [retDepFrom, setRetDepFrom] = useState(route.ret_dep_time_from ?? '');
  const [retDepTo, setRetDepTo] = useState(route.ret_dep_time_to ?? '');
  const [retArrFrom, setRetArrFrom] = useState(route.ret_arr_time_from ?? '');
  const [retArrTo, setRetArrTo] = useState(route.ret_arr_time_to ?? '');
  const [maxArrivalDayOffset, setMaxArrivalDayOffset] = useState(
    route.max_arrival_day_offset,
  );
  const [retMaxArrivalDayOffset, setRetMaxArrivalDayOffset] = useState(
    route.ret_max_arrival_day_offset,
  );

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);

  const isRoundtrip = route.trip_type === 'roundtrip';

  const handleSave = () => {
    const allTimes = [depFrom, depTo, arrFrom, arrTo, retDepFrom, retDepTo, retArrFrom, retArrTo];
    for (const t of allTimes) {
      if (t && !HHMM_RE.test(t)) {
        setValidationError(`时间格式应为 HH:MM（有问题：${t}）`);
        return;
      }
    }
    setValidationError(null);

    const body: UpdateRouteBody = {
      target_price: parseFloat(targetPrice),
      scrape_interval: parseInt(scrapeInterval),
      is_active: isActive,
      // Send empty string to clear; server treats it as null.
      dep_time_from: depFrom,
      dep_time_to: depTo,
      arr_time_from: arrFrom,
      arr_time_to: arrTo,
      max_arrival_day_offset: maxArrivalDayOffset,
      ret_max_arrival_day_offset: isRoundtrip ? retMaxArrivalDayOffset : null,
    };
    if (isRoundtrip) {
      body.ret_dep_time_from = retDepFrom;
      body.ret_dep_time_to = retDepTo;
      body.ret_arr_time_from = retArrFrom;
      body.ret_arr_time_to = retArrTo;
    }
    updateMutation.mutate(body);
  };

  const handleDelete = () => {
    deleteMutation.mutate(routeId, {
      onSuccess: () => navigate('/'),
    });
  };

  const clearAllTimes = () => {
    setDepFrom('');
    setDepTo('');
    setArrFrom('');
    setArrTo('');
    setRetDepFrom('');
    setRetDepTo('');
    setRetArrFrom('');
    setRetArrTo('');
  };

  return (
    <div className="space-y-4">
      {/* Basic monitor params */}
      <div className="bg-white rounded-xl border border-gray-100 p-5">
        <h3 className="text-sm font-semibold text-gray-700 mb-4">监控参数</h3>
        <div className="space-y-4">
          <div>
            <label className="text-xs text-gray-500 block mb-1">目标价格 (¥)</label>
            <input
              type="number"
              value={targetPrice}
              onInput={(e) => setTargetPrice((e.target as HTMLInputElement).value)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">采集间隔 (小时)</label>
            <select
              value={scrapeInterval}
              onChange={(e) => setScrapeInterval(e.target.value)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100"
            >
              {[1, 2, 4, 6, 12, 24].map((h) => (
                <option key={h} value={h}>
                  {h} 小时
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-center justify-between">
            <label className="text-xs text-gray-500">启用监控</label>
            <button
              onClick={() => setIsActive(!isActive)}
              className={`relative w-10 h-5 rounded-full transition-colors ${
                isActive ? 'bg-blue-600' : 'bg-gray-300'
              }`}
            >
              <span
                className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
                  isActive ? 'translate-x-5' : 'translate-x-0.5'
                }`}
              />
            </button>
          </div>
        </div>
      </div>

      {/* Arrival-day limits */}
      <div className="rounded-xl border border-gray-100 bg-white p-5">
        <h3 className="text-sm font-semibold text-gray-700">到达日期限制</h3>
        <p className="mb-4 mt-1 text-[11px] leading-4 text-gray-400">
          按每段行程的实际到达日期过滤。这是累计上限：选择“最晚 +1 天”会保留当天和 +1 天到达的航班。
        </p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <ArrivalDayLimitSelect
            id="config-max-arrival-day-offset"
            label="去程最晚到达"
            value={maxArrivalDayOffset}
            onChange={setMaxArrivalDayOffset}
          />
          {isRoundtrip && (
            <ArrivalDayLimitSelect
              id="config-ret-max-arrival-day-offset"
              label="回程最晚到达"
              value={retMaxArrivalDayOffset}
              onChange={setRetMaxArrivalDayOffset}
            />
          )}
        </div>
      </div>

      {/* Time-window editor */}
      <div className="bg-white rounded-xl border border-gray-100 p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-700">起降时间窗</h3>
          <button
            onClick={clearAllTimes}
            className="text-[11px] text-gray-400 hover:text-gray-600"
          >
            清除全部
          </button>
        </div>
        <p className="text-[11px] text-gray-400 mb-3">
          留空表示不限。修改后历史数据按新窗口重新筛选展示，未来采集也会按此约束。
        </p>

        <div className="space-y-3">
          <p className="text-[11px] font-medium text-gray-500 uppercase tracking-wide">
            去程
          </p>
          <TimeRow
            label="起飞时间"
            fromValue={depFrom}
            toValue={depTo}
            onFromChange={setDepFrom}
            onToChange={setDepTo}
          />
          <TimeRow
            label="落地时间"
            fromValue={arrFrom}
            toValue={arrTo}
            onFromChange={setArrFrom}
            onToChange={setArrTo}
          />
        </div>

        {isRoundtrip && (
          <div className="mt-5 space-y-3 pt-4 border-t border-gray-50">
            <p className="text-[11px] font-medium text-gray-500 uppercase tracking-wide">
              回程
            </p>
            <TimeRow
              label="起飞时间"
              fromValue={retDepFrom}
              toValue={retDepTo}
              onFromChange={setRetDepFrom}
              onToChange={setRetDepTo}
            />
            <TimeRow
              label="落地时间"
              fromValue={retArrFrom}
              toValue={retArrTo}
              onFromChange={setRetArrFrom}
              onToChange={setRetArrTo}
            />
          </div>
        )}

        {(route.dep_airport_code || route.arr_airport_code) && (
          <div className="mt-4 pt-4 border-t border-gray-50">
            <p className="text-[11px] font-medium text-gray-500 mb-2">机场限制（只读）</p>
            <div className="grid grid-cols-2 gap-3 text-xs">
              {route.dep_airport_code && (
                <div>
                  <span className="text-gray-400">出发机场</span>
                  <p className="text-gray-700 font-medium">{route.dep_airport_code}</p>
                </div>
              )}
              {route.arr_airport_code && (
                <div>
                  <span className="text-gray-400">到达机场</span>
                  <p className="text-gray-700 font-medium">{route.arr_airport_code}</p>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Save action */}
      <div className="bg-white rounded-xl border border-gray-100 p-5">
        {validationError && (
          <p className="text-xs text-red-600 mb-2">{validationError}</p>
        )}
        <button
          onClick={handleSave}
          disabled={updateMutation.isPending}
          className="w-full px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50 font-medium"
        >
          {updateMutation.isPending ? '保存中...' : '保存修改'}
        </button>
        {updateMutation.isSuccess && (
          <p className="text-xs text-green-600 text-center mt-2">已保存，历史数据已按新过滤条件重新筛选</p>
        )}
        {updateMutation.isError && (
          <p className="text-xs text-red-600 text-center mt-2">
            {(updateMutation.error as { response?: { data?: { detail?: string } } })?.response
              ?.data?.detail || '保存失败'}
          </p>
        )}
      </div>

      {/* Actions */}
      <div className="bg-white rounded-xl border border-gray-100 p-5">
        <h3 className="text-sm font-semibold text-gray-700 mb-4">操作</h3>
        <div className="space-y-3">
          <button
            onClick={() => scrapeMutation.mutate()}
            disabled={scrapeMutation.isPending}
            className="w-full px-4 py-2 bg-white text-gray-700 text-sm rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors disabled:opacity-50 font-medium"
          >
            {scrapeMutation.isPending
              ? scrapeMutation.task?.status === 'queued'
                ? '任务排队中...'
                : scrapeMutation.task?.status === 'running'
                  ? '正在采集...'
                  : '正在提交...'
              : '立即采集'}
          </button>
          {scrapeMutation.task && <ScrapeTaskPanel task={scrapeMutation.task} />}
          {scrapeMutation.isError && (
            <p className="text-xs text-red-600 text-center">
              {getMutationErrorMessage(scrapeMutation.error)}
            </p>
          )}

          {!showDeleteConfirm ? (
            <button
              onClick={() => setShowDeleteConfirm(true)}
              className="w-full px-4 py-2 bg-white text-red-600 text-sm rounded-lg border border-red-200 hover:bg-red-50 transition-colors font-medium"
            >
              删除路线
            </button>
          ) : (
            <div className="p-3 bg-red-50 rounded-lg border border-red-100">
              <p className="text-xs text-red-700 mb-3">
                确定要删除此路线？删除后所有历史数据将一并清除，此操作不可撤销。
              </p>
              <div className="flex gap-2">
                <button
                  onClick={handleDelete}
                  disabled={deleteMutation.isPending}
                  className="flex-1 px-3 py-1.5 bg-red-600 text-white text-xs rounded-lg hover:bg-red-700 disabled:opacity-50"
                >
                  {deleteMutation.isPending ? '删除中...' : '确认删除'}
                </button>
                <button
                  onClick={() => setShowDeleteConfirm(false)}
                  className="flex-1 px-3 py-1.5 bg-white text-gray-600 text-xs rounded-lg border border-gray-200"
                >
                  取消
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Meta info */}
      <div className="text-xs text-gray-400 text-center py-2">
        创建于 {route.created_at || '未知'} · ID: {routeId}
      </div>
    </div>
  );
}
