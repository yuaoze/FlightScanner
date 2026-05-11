import type { MonitorStatus } from '../../types';
import { STATUS_CONFIG } from '../../lib/constants';

interface DecisionBadgeProps {
  status: MonitorStatus;
}

export function DecisionBadge({ status }: DecisionBadgeProps) {
  const config = STATUS_CONFIG[status];
  return (
    <span
      className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium border ${config.bgClass} ${config.textClass} ${config.borderClass}`}
    >
      <span>{config.icon}</span>
      <span>{config.label}</span>
    </span>
  );
}
