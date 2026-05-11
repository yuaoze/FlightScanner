interface Props {
  maxBudget: number;
  onBudgetChange: (v: number) => void;
  visaFreeOnly: boolean;
  onToggleVisaFree: (v: boolean) => void;
  excludeRedEye: boolean;
  onToggleRedEye: (v: boolean) => void;
}

function Toggle({
  active,
  label,
  onClick,
  activeClass,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  activeClass: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 rounded-full text-xs font-medium transition-all border ${
        active
          ? `${activeClass} text-white border-transparent shadow`
          : 'bg-white text-gray-600 border-gray-200 hover:border-gray-300'
      }`}
    >
      {label}
    </button>
  );
}

export function FilterBar({
  maxBudget,
  onBudgetChange,
  visaFreeOnly,
  onToggleVisaFree,
  excludeRedEye,
  onToggleRedEye,
}: Props) {
  return (
    <div className="bg-white/60 backdrop-blur rounded-xl border border-gray-100 p-4 mb-5 flex flex-wrap items-center gap-3">
      <div className="flex items-center gap-3 flex-1 min-w-[240px]">
        <span className="text-xs text-gray-500 whitespace-nowrap">💰 预算</span>
        <input
          type="range"
          min="300"
          max="5000"
          step="100"
          value={maxBudget}
          onChange={(e) => onBudgetChange(Number(e.target.value))}
          className="flex-1 accent-purple-600"
        />
        <span className="text-sm font-semibold text-gray-800 w-20 text-right">
          ≤ ¥{maxBudget}
        </span>
      </div>

      <div className="flex items-center gap-2">
        <Toggle
          active={visaFreeOnly}
          label="🌍 仅免签/落地签"
          onClick={() => onToggleVisaFree(!visaFreeOnly)}
          activeClass="bg-gradient-to-r from-emerald-500 to-teal-500"
        />
        <Toggle
          active={excludeRedEye}
          label="🛏️ 拒绝红眼"
          onClick={() => onToggleRedEye(!excludeRedEye)}
          activeClass="bg-gradient-to-r from-indigo-500 to-purple-500"
        />
      </div>
    </div>
  );
}
