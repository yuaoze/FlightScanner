import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../api/client';

interface CityItem {
  name: string;
  code: string;
}

function useCities() {
  return useQuery({
    queryKey: ['cities'],
    queryFn: async (): Promise<CityItem[]> => {
      const { data } = await apiClient.get<CityItem[]>('/cities');
      return data;
    },
    staleTime: Infinity,
  });
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
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setSearch(value);
  }, [value]);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const filtered = cities.filter(
    (c) => c.name.includes(search) || c.code.toLowerCase().includes(search.toLowerCase())
  ).slice(0, 10);

  return (
    <div className="relative" ref={ref}>
      <input
        type="text"
        value={search}
        onChange={(e) => {
          setSearch(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        className="w-full px-3 py-2.5 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-400"
      />
      {open && filtered.length > 0 && (
        <div className="absolute z-50 mt-1 w-full bg-white border border-gray-200 rounded-lg shadow-lg max-h-48 overflow-y-auto">
          {filtered.map((city) => (
            <button
              key={city.code}
              type="button"
              className="w-full px-3 py-2 text-left text-sm hover:bg-blue-50 flex justify-between"
              onClick={() => {
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

export function AddMonitorPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: cities = [] } = useCities();

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
      const { data } = await apiClient.post('/routes', body);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['routes'] });
      queryClient.invalidateQueries({ queryKey: ['stats'] });
      navigate('/');
    },
  });

  const canSubmit = origin && destination && targetDate && targetPrice;

  return (
    <div className="max-w-2xl">
      <div className="mb-6">
        <h1 className="text-xl font-bold text-gray-900">添加监控</h1>
        <p className="text-sm text-gray-400 mt-0.5">设置航线监控，追踪价格变化</p>
      </div>

      <div className="bg-white rounded-xl border border-gray-100 p-6 space-y-5">
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
              className="w-full px-3 py-2.5 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-400"
            />
          </div>
          <div>
            <div className="flex items-center gap-2 mb-1.5">
              <input
                type="checkbox"
                id="roundtrip"
                checked={isRoundTrip}
                onChange={(e) => setIsRoundTrip(e.target.checked)}
                className="rounded border-gray-300"
              />
              <label htmlFor="roundtrip" className="text-xs font-medium text-gray-500">
                往返
              </label>
            </div>
            <input
              type="date"
              value={returnDate}
              onChange={(e) => setReturnDate(e.target.value)}
              disabled={!isRoundTrip}
              className="w-full px-3 py-2.5 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-400 disabled:bg-gray-50 disabled:text-gray-300"
            />
          </div>
        </div>

        {/* Price & Interval */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1.5">目标价格 (¥)</label>
            <input
              type="number"
              value={targetPrice}
              onChange={(e) => setTargetPrice(e.target.value)}
              placeholder="如：800"
              className="w-full px-3 py-2.5 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-400"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1.5">采集间隔</label>
            <select
              value={scrapeInterval}
              onChange={(e) => setScrapeInterval(Number(e.target.value))}
              className="w-full px-3 py-2.5 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-400"
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
                <label className="block text-xs font-medium text-gray-500 mb-1.5">
                  起飞时间从
                </label>
                <input
                  type="time"
                  value={depTimeFrom}
                  onChange={(e) => setDepTimeFrom(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1.5">
                  起飞时间到
                </label>
                <input
                  type="time"
                  value={depTimeTo}
                  onChange={(e) => setDepTimeTo(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1.5">
                  出发机场 (IATA)
                </label>
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
                <label className="block text-xs font-medium text-gray-500 mb-1.5">
                  到达机场 (IATA)
                </label>
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
        <div className="flex gap-3 pt-2">
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
            onClick={() => navigate('/')}
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
  );
}
