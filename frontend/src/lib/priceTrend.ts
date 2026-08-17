import type { PriceHistoryPoint } from '../types';

export type PriceGranularity = 'hour' | 'day';

export interface PriceChartDatum {
  time: string;
  [source: string]: number | string;
}

export interface PriceChartSeries {
  sources: string[];
  chartData: PriceChartDatum[];
  pointCounts: Record<string, number>;
}

/** Build the per-platform minimum-price series displayed by the trend chart. */
export function buildPriceChartSeries(
  points: PriceHistoryPoint[],
  granularity: PriceGranularity,
): PriceChartSeries {
  const sources: string[] = [];
  const pointCounts: Record<string, number> = {};
  const bucketMap = new Map<string, PriceChartDatum>();

  for (const point of points) {
    if (!(point.source in pointCounts)) {
      sources.push(point.source);
      pointCounts[point.source] = 0;
    }

    const key =
      granularity === 'hour'
        ? `${point.date.slice(0, 13).replace('T', ' ')}:00`
        : point.date.slice(0, 10);
    const bucket = bucketMap.get(key) ?? { time: key };
    const previousPrice = bucket[point.source];

    if (typeof previousPrice !== 'number') {
      pointCounts[point.source] += 1;
    }
    if (typeof previousPrice !== 'number' || point.price < previousPrice) {
      bucket[point.source] = point.price;
    }
    bucketMap.set(key, bucket);
  }

  const chartData = Array.from(bucketMap.values()).sort((a, b) =>
    a.time.localeCompare(b.time),
  );

  return { sources, chartData, pointCounts };
}
