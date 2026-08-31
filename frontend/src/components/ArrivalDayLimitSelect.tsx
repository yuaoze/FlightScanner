import type { ArrivalDayLimit } from '../types';
import {
  ARRIVAL_DAY_LIMIT_OPTIONS,
  arrivalDayLimitDescription,
  arrivalDayLimitValue,
  parseArrivalDayLimit,
} from '../lib/arrivalDay';

interface ArrivalDayLimitSelectProps {
  id: string;
  label: string;
  value: ArrivalDayLimit;
  onChange: (value: ArrivalDayLimit) => void;
}

export function ArrivalDayLimitSelect({
  id,
  label,
  value,
  onChange,
}: ArrivalDayLimitSelectProps) {
  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-gray-500 mb-1.5">
        {label}
      </label>
      <select
        id={id}
        value={arrivalDayLimitValue(value)}
        onChange={(event) => onChange(parseArrivalDayLimit(event.target.value))}
        className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
      >
        {ARRIVAL_DAY_LIMIT_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <p className="mt-1 text-[11px] leading-4 text-gray-400">
        {arrivalDayLimitDescription(value)}
      </p>
    </div>
  );
}
