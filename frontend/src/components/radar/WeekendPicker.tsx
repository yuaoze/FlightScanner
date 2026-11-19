import type { WeekendOption } from '../../types/radar';

interface Props {
  weekends: WeekendOption[];
  selected: string | null;
  onSelect: (outbound: string | null) => void;
}

export function WeekendPicker({ weekends, selected, onSelect }: Props) {
  return (
    <div className="-mx-6 mb-4 px-6 md:mx-0 md:px-0">
      <div className="scrollbar-thin flex items-center gap-2 overflow-x-auto pb-1" role="group" aria-label="选择出行周末">
        <button
          type="button"
          onClick={() => onSelect(null)}
          aria-pressed={selected === null}
          className={`flex flex-shrink-0 items-center gap-1 rounded-full px-4 py-2 text-xs font-medium transition-all ${
            selected === null
              ? 'bg-gradient-to-r from-purple-600 to-pink-600 text-white shadow-md'
              : 'border border-purple-200 bg-white text-purple-700 hover:border-purple-400'
          }`}
        >
          <span>✨</span>
          <span>精选行程</span>
        </button>

        {weekends.length > 0 && (
          <span className="flex-shrink-0 px-1 text-[10px] text-gray-300">|</span>
        )}

        {weekends.map((weekend) => {
          const active = weekend.outbound_date === selected;
          return (
            <button
              type="button"
              key={weekend.outbound_date}
              onClick={() => onSelect(weekend.outbound_date)}
              aria-pressed={active}
              aria-label={`${weekend.label}，${weekend.deal_count} 个目的地`}
              className={`flex flex-shrink-0 items-center gap-1.5 rounded-full px-4 py-2 text-xs transition-all ${
                active
                  ? 'bg-gray-900 text-white shadow-md'
                  : 'border border-gray-200 bg-white text-gray-700 hover:border-purple-300'
              }`}
            >
              <span className="font-medium">{weekend.label.split(' / ')[0]}</span>
              <span className={`text-[10px] ${active ? 'text-gray-300' : 'text-gray-400'}`}>
                {weekend.deal_count}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
