import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../../api/client';

interface CityItem {
  name: string;
  code: string;
}

function CityInput({
  value,
  onChange,
  placeholder,
  cities,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  cities: CityItem[];
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState(value);

  const filtered = cities.filter(
    (c) => c.name.includes(search) || c.code.toLowerCase().includes(search.toLowerCase())
  ).slice(0, 8);

  return (
    <div className="relative">
      <input
        type="text"
        value={search}
        onChange={(e) => {
          setSearch(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 200)}
        placeholder={placeholder}
        className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-400"
      />
      {open && filtered.length > 0 && (
        <div className="absolute z-50 mt-1 w-full bg-white border border-gray-200 rounded-lg shadow-lg max-h-48 overflow-y-auto">
          {filtered.map((city) => (
            <button
              key={city.code}
              type="button"
              className="w-full px-3 py-2 text-left text-sm hover:bg-blue-50 flex justify-between"
              onMouseDown={() => {
                onChange(city.name);
                setSearch(city.name);
                setOpen(false);
              }}
            >
              <span>{city.name}</span>
              <span className="text-xs text-gray-400">{city.code}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

interface AddMonitorDialogProps {
  open: boolean;
  onClose: () => void;
  cities: CityItem[];
}

export function AddMonitorDialog({ open, onClose, cities }: AddMonitorDialogProps) {
  const queryClient = useQueryClient();

  const [monitoringMode, setMonitoringMode] = useState<'route' | 'flight'>('route');
  const [origin, setOrigin] = useState('');
  const [destination, setDestination] = useState('');
  const [targetDate, setTargetDate] = useState('');
  const [returnDate, setReturnDate] = useState('');
  const [isRoundTrip, setIsRoundTrip] = useState(false);
  const [targetPrice, setTargetPrice] = useState('');
  const [scrapeInterval, setScrapeInterval] = useState(6);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [depTimeFrom, setDepTimeFrom] = useState('');
  const [depTimeTo, setDepTimeTo] = useState('');
  const [depAirport, setDepAirport] = useState('');
  const [arrAirport, setArrAirport] = useState('');

  const [outboundFlightNo, setOutboundFlightNo] = useState('');
  const [inboundFlightNo, setInboundFlightNo] = useState('');
  const [pinnedSeatClass, setPinnedSeatClass] = useState('');

  const createMutation = useMutation({
    mutationFn: async () => {
      const body: Record<string, unknown> = {
        origin,
        destination,
        target_date: targetDate,
        target_price: parseFloat(targetPrice),
        scrape_interval: scrapeInterval,
        trip_type: isRoundTrip ? 'roundtrip' : 'oneway',
      };
      if (isRoundTrip && returnDate) body.return_date = returnDate;
      if (depTimeFrom) body.dep_time_from = depTimeFrom;
      if (depTimeTo) body.dep_time_to = depTimeTo;
      if (depAirport) body.dep_airport_code = depAirport;
      if (arrAirport) body.arr_airport_code = arrAirport;

      if (monitoringMode === 'flight') {
        body.monitoring_mode = 'flight';
        body.outbound_flight_no = outboundFlightNo;
        if (isRoundTrip && inboundFlightNo) body.inbound_flight_no = inboundFlightNo;
        if (pinnedSeatClass) body.pinned_seat_class = pinnedSeatClass;
      }

      const { data } = await apiClient.post('/routes', body);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['routes'] });
      queryClient.invalidateQueries({ queryKey: ['stats'] });
      onClose();
    },
  });

  const canSubmit = origin && destination && targetDate && targetPrice
    && (monitoringMode === 'route' || outboundFlightNo);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/30 backdrop-blur-sm" onClick={onClose} />
      <div className="relative bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto mx-4">
        <div className="sticky top-0 bg-white border-b border-gray-100 px-6 py-4 rounded-t-2xl">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-900">添加监控</h2>
            <button
              type="button"
              onClick={onClose}
              className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-gray-100 text-gray-400 hover:text-gray-600"
            >
              ✕
            </button>
          </div>
          <p className="text-sm text-gray-400 mt-0.5">设置航线监控，追踪价格变化</p>
        </div>

        <div className="px-6 py-5 space-y-5">
          {/* Monitoring Mode Toggle */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-2">监控模式</label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setMonitoringMode('route')}
                className={`flex-1 px-4 py-2 text-sm font-medium rounded-lg border transition-colors ${
                  monitoringMode === 'route'
                    ? 'bg-blue-50 border-blue-300 text-blue-700'
                    : 'bg-white border-gray-200 text-gray-500 hover:bg-gray-50'
                }`}
              >
                路线监控
              </button>
              <button
                type="button"
                onClick={() => setMonitoringMode('flight')}
                className={`flex-1 px-4 py-2 text-sm font-medium rounded-lg border transition-colors ${
                  monitoringMode === 'flight'
                    ? 'bg-blue-50 border-blue-300 text-blue-700'
                    : 'bg-white border-gray-200 text-gray-500 hover:bg-gray-50'
                }`}
              >
                精准航班监控
              </button>
            </div>
            {monitoringMode === 'flight' && (
              <p className="text-xs text-gray-400 mt-1.5">
                按航班号精确追踪指定航班的价格与状态
              </p>
            )}
          </div>

          {/* Cities */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1.5">出发城市</label>
              <CityInput
                value={origin}
                onChange={setOrigin}
                placeholder="如：上海"
                cities={cities}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1.5">到达城市</label>
              <CityInput
                value={destination}
                onChange={setDestination}
                placeholder="如：广州"
                cities={cities}
              />
            </div>
          </div>

          {/* Dates */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1.5">出发日期</label>
              <input
                type="date"
                value={targetDate}
                onChange={(e) => setTargetDate(e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-400"
              />
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1.5">
                <input
                  type="checkbox"
                  id="dialog-roundtrip"
                  checked={isRoundTrip}
                  onChange={(e) => setIsRoundTrip(e.target.checked)}
                  className="rounded border-gray-300"
                />
                <label htmlFor="dialog-roundtrip" className="text-xs font-medium text-gray-500">
                  往返
                </label>
              </div>
              <input
                type="date"
                value={returnDate}
                onChange={(e) => setReturnDate(e.target.value)}
                disabled={!isRoundTrip}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-400 disabled:bg-gray-50 disabled:text-gray-300"
              />
            </div>
          </div>

          {/* Pinned Flight Fields */}
          {monitoringMode === 'flight' && (
            <div className="space-y-4 p-4 bg-blue-50 rounded-lg border border-blue-200">
              <p className="text-xs font-medium text-blue-700">精准航班信息</p>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1.5">
                    去程航班号 <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="text"
                    value={outboundFlightNo}
                    onChange={(e) => setOutboundFlightNo(e.target.value.toUpperCase())}
                    placeholder="如：CA953"
                    maxLength={10}
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100 font-mono"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1.5">舱位</label>
                  <select
                    value={pinnedSeatClass}
                    onChange={(e) => setPinnedSeatClass(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100"
                  >
                    <option value="">不限</option>
                    <option value="经济舱">经济舱</option>
                    <option value="商务舱">商务舱</option>
                    <option value="头等舱">头等舱</option>
                  </select>
                </div>
              </div>
              {isRoundTrip && (
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1.5">
                    回程航班号
                  </label>
                  <input
                    type="text"
                    value={inboundFlightNo}
                    onChange={(e) => setInboundFlightNo(e.target.value.toUpperCase())}
                    placeholder="如：CA952（选填）"
                    maxLength={10}
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100 font-mono"
                  />
                </div>
              )}
            </div>
          )}

          {/* Price & Interval */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1.5">目标价格 (¥)</label>
              <input
                type="number"
                value={targetPrice}
                onInput={(e) => setTargetPrice((e.target as HTMLInputElement).value)}
                placeholder="如：800"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-400"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1.5">采集间隔</label>
              <select
                value={scrapeInterval}
                onChange={(e) => setScrapeInterval(Number(e.target.value))}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-400"
              >
                <option value={1}>每 1 小时</option>
                <option value={2}>每 2 小时</option>
                <option value={4}>每 4 小时</option>
                <option value={6}>每 6 小时</option>
                <option value={12}>每 12 小时</option>
                <option value={24}>每 24 小时</option>
              </select>
            </div>
          </div>

          {/* Advanced */}
          <div>
            <button
              type="button"
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1"
            >
              <span>{showAdvanced ? '▼' : '▶'}</span>
              高级选项
            </button>
            {showAdvanced && (
              <div className="mt-3 grid grid-cols-2 gap-4 p-4 bg-gray-50 rounded-lg">
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1.5">起飞时间从</label>
                  <input
                    type="time"
                    value={depTimeFrom}
                    onChange={(e) => setDepTimeFrom(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1.5">起飞时间到</label>
                  <input
                    type="time"
                    value={depTimeTo}
                    onChange={(e) => setDepTimeTo(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1.5">出发机场 (IATA)</label>
                  <input
                    type="text"
                    value={depAirport}
                    onChange={(e) => setDepAirport(e.target.value.toUpperCase())}
                    placeholder="如：PVG"
                    maxLength={3}
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1.5">到达机场 (IATA)</label>
                  <input
                    type="text"
                    value={arrAirport}
                    onChange={(e) => setArrAirport(e.target.value.toUpperCase())}
                    placeholder="如：CAN"
                    maxLength={3}
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100"
                  />
                </div>
              </div>
            )}
          </div>

          {/* Submit */}
          <div className="flex gap-3 pt-2 border-t border-gray-100">
            <button
              type="button"
              disabled={!canSubmit || createMutation.isPending}
              onClick={() => createMutation.mutate()}
              className="px-6 py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {createMutation.isPending ? '创建中...' : '创建监控'}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="px-6 py-2.5 text-gray-500 text-sm font-medium rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors"
            >
              取消
            </button>
          </div>

          {createMutation.isError && (
            <p className="text-sm text-red-500">
              创建失败：{(createMutation.error as Error)?.message || '请检查输入'}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
