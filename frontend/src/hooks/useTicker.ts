import { useEffect, useState } from 'react';

/** Triggers a re-render every `intervalMs` ms — use for live countdowns. */
export function useTicker(intervalMs = 60_000): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return tick;
}
