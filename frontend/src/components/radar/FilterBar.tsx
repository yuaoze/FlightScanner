import { useId } from 'react';

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
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-full border px-4 py-2 text-xs font-medium transition-all ${
        active
          ? `${activeClass} border-transparent text-white shadow`
          : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
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
  const budgetId = useId();

  return (
    <div className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-gray-100 bg-white/60 p-4 backdrop-blur">
      <div className="flex min-w-[240px] flex-1 items-center gap-3">
        <label htmlFor={budgetId} className="whitespace-nowrap text-xs text-gray-500">💰 预算</label>
        <input
          id={budgetId}
          type="range"
          min="300"
          max="5000"
          step="100"
          value={maxBudget}
          onChange={(event) => onBudgetChange(Number(event.target.value))}
          aria-valuetext={`往返不超过 ${maxBudget} 元`}
          className="flex-1 accent-purple-600"
        />
        <output htmlFor={budgetId} className="w-20 text-right text-sm font-semibold text-gray-800">
          ≤ ¥{maxBudget}
        </output>
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
