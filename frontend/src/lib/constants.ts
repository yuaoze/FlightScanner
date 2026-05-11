export const COLORS = {
  primary: '#3B82F6',
  success: '#22C55E',
  warning: '#F59E0B',
  danger: '#EF4444',
} as const;

export const STATUS_CONFIG = {
  '建议购买': {
    color: COLORS.success,
    bgClass: 'bg-green-50',
    textClass: 'text-green-700',
    borderClass: 'border-green-200',
    label: '建议购买',
    description: '当前价格处于较低水平，适合立即购买',
    icon: '✓',
  },
  '建议观望': {
    color: COLORS.warning,
    bgClass: 'bg-amber-50',
    textClass: 'text-amber-700',
    borderClass: 'border-amber-200',
    label: '建议观望',
    description: '价格可能继续下降，建议持续关注',
    icon: '◷',
  },
  '价格偏高': {
    color: COLORS.danger,
    bgClass: 'bg-red-50',
    textClass: 'text-red-700',
    borderClass: 'border-red-200',
    label: '价格偏高',
    description: '当前价格较高，建议等待更低价格',
    icon: '↑',
  },
} as const;
