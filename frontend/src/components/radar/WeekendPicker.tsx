import type { WeekendOption } from '../../types/radar';

interface Props {
  weekends: WeekendOption[];
  selected: string | null;
  onSelect: (outbound: string | null) => void;
}

export function WeekendPicker({ weekends, selected, onSelect }: Props) {
  return (
    <div className="mb-4 -mx-6 px-6 md:mx-0 md:px-0">
      <div className="flex items-center gap-2 overflow-x-auto pb-1 scrollbar-thin">
        <button
          onClick={() => onSelect(null)}
          className={`flex-shrink-0 px-4 py-2 rounded-full text-xs font-medium transition-all flex items-center gap-1 ${
            selected === null
              ? 'bg-gradient-to-r from-purple-600 to-pink-600 text-white shadow-md'
              : 'bg-white text-purple-700 border border-purple-200 hover:border-purple-400'
          }`}
        >
          <span>✨</span>
          <span>精选行程</span>
        </button>

        {weekends.length > 0 && (
          <span className="flex-shrink-0 text-[10px] text-gray-300 px-1">|</span>
        )}

        {weekends.map((w) => {
          const active = w.outbound_date === selected;
          return (
            <button
              key={w.outbound_date}
              onClick={() => onSelect(w.outbound_date)}
              className={`flex-shrink-0 px-4 py-2 rounded-full text-xs transition-all flex items-center gap-1.5 ${
                active
                  ? 'bg-gray-900 text-white shadow-md'
                  : 'bg-white text-gray-700 border border-gray-200 hover:border-purple-300'
              }`}
            >
              <span className="font-medium">{w.label.split(' / ')[0]}</span>
              <span className={`text-[10px] ${active ? 'text-gray-300' : 'text-gray-400'}`}>
                {w.deal_count}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
