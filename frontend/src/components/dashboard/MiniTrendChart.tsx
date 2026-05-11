import { LineChart, Line, ResponsiveContainer, ReferenceDot } from 'recharts';
import type { SparklinePoint } from '../../types';
import { COLORS } from '../../lib/constants';

interface MiniTrendChartProps {
  data: SparklinePoint[];
  direction: string;
}

export function MiniTrendChart({ data, direction }: MiniTrendChartProps) {
  if (data.length < 2) {
    return (
      <div className="h-[60px] flex items-center justify-center text-xs text-gray-300">
        数据不足
      </div>
    );
  }

  const color =
    direction === 'down' ? COLORS.success : direction === 'up' ? COLORS.danger : '#94a3b8';

  const lastPoint = data[data.length - 1];

  return (
    <div className="h-[60px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
          <Line
            type="monotone"
            dataKey="price"
            stroke={color}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
          <ReferenceDot
            x={lastPoint.date}
            y={lastPoint.price}
            r={3}
            fill={color}
            stroke="white"
            strokeWidth={1.5}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
