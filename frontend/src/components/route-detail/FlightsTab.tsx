import { useState } from 'react';
import type { FlightBriefInfo, RouteDetailResponse } from '../../types';
import { useRouteBatches, useRouteFlights } from '../../hooks/useRouteDetail';
import { formatFlightDate } from '../../lib/arrivalDay';

interface Props {
  routeId: number;
  route: RouteDetailResponse;
}

const SOURCE_LABELS: Record<string, string> = {
  fliggy: '飞猪',
  qunar: '去哪儿',
  ctrip: '携程',
  tongcheng: '同程旅行',
  trip: 'Trip',
};

const SOURCE_COLORS: Record<string, string> = {
  fliggy: 'bg-yellow-50 text-yellow-700',
  qunar: 'bg-amber-50 text-amber-700',
  ctrip: 'bg-blue-50 text-blue-700',
  tongcheng: 'bg-violet-50 text-violet-700',
  trip: 'bg-green-50 text-green-700',
};

function FlightLeg({
  label,
  flight,
  origin,
  destination,
  differentDepartureAirport = false,
  differentArrivalAirport = false,
}: {
  label: string;
  flight: Omit<FlightBriefInfo, 'arrival_date_is_estimated'>;
  origin: string;
  destination: string;
  differentDepartureAirport?: boolean;
  differentArrivalAirport?: boolean;
}) {
  const flightNumbers = flight.flight_no.split(/[+/]/).map((number) => number.trim()).filter(Boolean);
  const transferCount = Math.max(0, flightNumbers.length - 1);
  const airlineCode = flight.flight_no.match(/^([A-Z0-9]{2})(?=\d)/i)?.[1].toUpperCase();
  const durationMatch = flight.duration?.match(/^(\d+)h(\d+)m$/);
  const duration = durationMatch
    ? `${Number(durationMatch[1]) ? `${Number(durationMatch[1])}小时` : ''}${Number(durationMatch[2]) ? `${Number(durationMatch[2])}分` : ''}` || '0分'
    : flight.duration || '时长待采集';
  const departureAirport = flight.departure_airport_code || origin;
  const arrivalAirport = flight.arrival_airport_code || destination;

  return (
    <div aria-label={`${label} ${flight.flight_no}`}>
      <div className="grid grid-cols-[28px_60px_minmax(54px,1fr)_72px] items-center gap-1.5 @md:grid-cols-[40px_80px_minmax(100px,1fr)_92px] @md:gap-4">
        <div className="flex flex-col items-center gap-1">
          <svg viewBox="0 0 40 36" className="h-8 w-7 @md:h-9 @md:w-10" aria-hidden="true">
            <path d="M2 31 23 5h15L26 31Z" fill="#0ea5e9" />
            <path d="m2 31 13-5h13l-2 5Z" fill="#0284c7" />
            <text x="24" y="23" textAnchor="middle" fill="white" fontSize="11" fontWeight="700" fontFamily="Arial, sans-serif">
              {airlineCode || '—'}
            </text>
          </svg>
          <span className="text-[10px] text-slate-400">{label}</span>
        </div>

        <div className="text-center">
          <time
            dateTime={flight.departure_date && flight.departure_time ? `${flight.departure_date}T${flight.departure_time}` : undefined}
            title={`出发 ${formatFlightDate(flight.departure_date)}`}
            className="block font-[Arial,sans-serif] text-[22px] leading-none tracking-tight tabular-nums text-slate-900 @md:text-[26px]"
          >
            {flight.departure_time || '--:--'}
          </time>
          <span
            title={differentDepartureAirport ? `${departureAirport} · 往返使用不同机场` : departureAirport}
            className={`mt-1.5 inline-block max-w-full rounded-md px-1 py-0.5 text-xs leading-4 @md:text-sm ${
              differentDepartureAirport ? 'bg-pink-100 text-pink-700' : 'text-slate-500'
            }`}
          >
            {departureAirport}
          </span>
        </div>

        <div className="min-w-0 text-center">
          <p className="text-[10px] leading-4 text-slate-500 @md:text-xs">{duration}</p>
          <div className="relative my-1 flex h-3 items-center pr-3" aria-hidden="true">
            <div className="h-px w-full bg-slate-300" />
            {transferCount > 0 && (
              <div className="absolute inset-y-0 left-0 right-3 flex items-center justify-evenly">
                {Array.from({ length: transferCount }, (_, index) => (
                  <span key={index} className="h-1.5 w-1.5 rounded-full bg-pink-500 ring-2 ring-white" />
                ))}
              </div>
            )}
            <svg viewBox="0 0 24 24" className="absolute right-0 h-3.5 w-3.5 text-slate-400" fill="currentColor">
              <path d="m21 12-7-2-5-7H6l3 7-5 1-2-3H0l2 4-2 4h2l2-3 5 1-3 7h3l5-7 7-2Z" />
            </svg>
          </div>
          <p
            title={transferCount > 0 ? '中转次数按航班段数展示，中转机场暂未提供' : '当前记录包含一个航班号，未提供经停信息'}
            className={`text-[10px] leading-4 @md:text-xs ${transferCount > 0 ? 'text-pink-500' : 'text-slate-400'}`}
          >
            {transferCount > 0 ? `转机${transferCount}次` : '单段航班'}
          </p>
        </div>

        <div className="text-left">
          <span className="relative inline-block font-[Arial,sans-serif] text-[22px] leading-none tracking-tight tabular-nums text-slate-900 @md:text-[26px]">
            <time
              dateTime={flight.arrival_date && flight.arrival_time ? `${flight.arrival_date}T${flight.arrival_time}` : undefined}
              title={`到达 ${formatFlightDate(flight.arrival_date)}`}
            >
              {flight.arrival_time || '--:--'}
            </time>
            {flight.arrival_day_offset !== null && flight.arrival_day_offset > 0 && (
              <sup
                className="absolute -right-3.5 top-0 text-[10px] leading-none tracking-normal"
                title={`出发后第 ${flight.arrival_day_offset} 天到达`}
                aria-label={`出发后第 ${flight.arrival_day_offset} 天到达`}
              >
                +{flight.arrival_day_offset}
              </sup>
            )}
          </span>
          <div>
            <span
              title={differentArrivalAirport ? `${arrivalAirport} · 往返使用不同机场` : arrivalAirport}
              className={`mt-1.5 inline-block max-w-full rounded-md px-1 py-0.5 text-xs leading-4 @md:text-sm ${
                differentArrivalAirport ? 'bg-pink-100 text-pink-700' : 'text-slate-500'
              }`}
            >
              {arrivalAirport}
            </span>
          </div>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-0.5 pl-[34px] text-[10px] text-slate-400 @md:pl-14 @md:text-[11px]">
        <time dateTime={flight.departure_date || undefined}>{formatFlightDate(flight.departure_date)}</time>
        <span>{flight.airline || '航司待采集'}</span>
        <span className="break-all font-mono text-slate-500">{flight.flight_no}</span>
      </div>
    </div>
  );
}

export function FlightsTab({ routeId, route }: Props) {
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null);
  const [selectedSource, setSelectedSource] = useState<string | null>(null);
  const [topN, setTopN] = useState(10);

  const { data: batchesData, isLoading: batchesLoading } = useRouteBatches(routeId, 30);
  const { data: flightsData, isLoading: flightsLoading } = useRouteFlights(
    routeId,
    selectedBatchId,
    selectedSource,
    topN
  );

  const batches = batchesData?.batches ?? [];

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="bg-white rounded-xl border border-gray-100 p-5">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label htmlFor="flight-batch" className="text-xs text-gray-500 block mb-1.5">采集批次</label>
            <select
              id="flight-batch"
              value={
                selectedBatchId && selectedSource
                  ? `${selectedBatchId}::${selectedSource}`
                  : ''
              }
              onChange={(e) => {
                const selected = batches.find(
                  (batch) => `${batch.batch_id}::${batch.source}` === e.target.value,
                );
                setSelectedBatchId(selected?.batch_id ?? null);
                setSelectedSource(selected?.source ?? null);
              }}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-100"
              disabled={batchesLoading}
            >
              <option value="">最新批次</option>
              {batches.map((b) => (
                <option
                  key={`${b.batch_id}-${b.source}`}
                  value={`${b.batch_id}::${b.source}`}
                >
                  {b.scraped_at} · {SOURCE_LABELS[b.source] || b.source} · ¥{Math.round(b.min_price)}起 ({b.flight_count}班)
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="text-xs text-gray-500 block mb-1.5">展示前 N 条最便宜</label>
            <div className="flex gap-2">
              {[5, 10, 20, 50].map((n) => (
                <button
                  key={n}
                  onClick={() => setTopN(n)}
                  className={`flex-1 px-3 py-2 text-sm rounded-lg border transition-colors ${
                    topN === n
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
                  }`}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>
        </div>

        {flightsData?.scraped_at && (
          <p className="text-xs text-gray-400 mt-3">
            采集时间：{flightsData.scraped_at} · 共返回 {flightsData.flights.length} 条
          </p>
        )}
      </div>

      {/* Flight list */}
      {flightsLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="bg-white rounded-xl border border-gray-100 p-4 h-24 animate-pulse">
              <div className="h-full bg-gray-50 rounded" />
            </div>
          ))}
        </div>
      ) : !flightsData || flightsData.flights.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-100 p-5">
          <div className="flex flex-col items-center justify-center py-12 text-gray-400">
            <span className="text-3xl mb-3">✈️</span>
            <p className="text-sm">暂无航班数据</p>
            <p className="text-xs mt-1">等待下次采集后将显示最新航班信息</p>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          {flightsData.flights.map((f, idx) => {
            const hasReturn = Boolean(f.return_flight_no && f.return_flight_no !== 'VIRTUAL_RETURN');
            const differentOriginAirports = Boolean(
              hasReturn && f.departure_airport_code && f.return_arrival_airport_code &&
              f.departure_airport_code !== f.return_arrival_airport_code,
            );
            const differentDestinationAirports = Boolean(
              hasReturn && f.arrival_airport_code && f.return_departure_airport_code &&
              f.arrival_airport_code !== f.return_departure_airport_code,
            );

            return (
              <article
                key={`${f.flight_no}-${idx}`}
                aria-label={`第 ${idx + 1} 条航班行程`}
                className="@container rounded-xl border border-gray-100 bg-white p-3 transition-shadow hover:shadow-sm sm:p-5"
              >
                <div className="mb-4 flex flex-wrap items-center justify-between gap-2 text-[10px] text-slate-400">
                  <span>#{String(idx + 1).padStart(2, '0')} · {route.trip_type === 'roundtrip' ? '往返行程' : '单程行程'}</span>
                  {(differentOriginAirports || differentDestinationAirports) && (
                    <span className="text-pink-600">粉色标记：往返不同机场</span>
                  )}
                </div>
                <div className="flex flex-col gap-4 @[720px]:flex-row @[720px]:items-center @[720px]:gap-8">
                  <div className="w-full min-w-0 max-w-xl flex-1 space-y-5">
                    <FlightLeg
                      label={route.trip_type === 'roundtrip' ? '去程' : '单程'}
                      flight={f}
                      origin={route.origin}
                      destination={route.destination}
                      differentDepartureAirport={differentOriginAirports}
                      differentArrivalAirport={differentDestinationAirports}
                    />
                    {hasReturn && (
                      <FlightLeg
                        label="回程"
                        flight={{
                          flight_no: f.return_flight_no!,
                          airline: f.return_airline || '',
                          departure_date: f.return_departure_date || route.return_date || '',
                          arrival_date: f.return_arrival_date,
                          arrival_day_offset: f.return_arrival_day_offset,
                          departure_time: f.return_departure_time || '',
                          arrival_time: f.return_arrival_time || '',
                          duration: f.return_duration,
                          departure_airport_code: f.return_departure_airport_code,
                          arrival_airport_code: f.return_arrival_airport_code,
                        }}
                        origin={route.destination}
                        destination={route.origin}
                        differentDepartureAirport={differentDestinationAirports}
                        differentArrivalAirport={differentOriginAirports}
                      />
                    )}
                    {route.trip_type === 'roundtrip' && !hasReturn && (
                      <p className="pl-[34px] text-xs text-slate-400 @md:pl-14">回程明细待采集</p>
                    )}
                  </div>

                  <div className="flex shrink-0 items-center justify-between gap-3 border-t border-gray-100 pt-3 @[720px]:ml-auto @[720px]:block @[720px]:min-w-28 @[720px]:border-l @[720px]:border-t-0 @[720px]:pl-6 @[720px]:pt-0 @[720px]:text-right">
                    <div>
                      <p className="text-[10px] text-slate-400">{route.trip_type === 'roundtrip' ? '往返总价' : '单程票价'}</p>
                      <p className="mt-0.5 text-2xl font-semibold tracking-tight tabular-nums text-slate-900">
                        <span className="mr-0.5 text-sm font-normal">¥</span>{Math.round(f.price).toLocaleString('zh-CN')}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center justify-end gap-1.5 @[720px]:mt-2">
                      <span className={`rounded px-1.5 py-0.5 text-[10px] ${SOURCE_COLORS[f.source] || 'bg-gray-100 text-gray-600'}`}>
                        {SOURCE_LABELS[f.source] || f.source}
                      </span>
                      <span className="text-[10px] text-slate-400">{f.seat_class}</span>
                    </div>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
