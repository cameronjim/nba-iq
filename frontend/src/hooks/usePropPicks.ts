import { useEffect, useState } from 'react';
import { getPropPicks } from '../api/client';
import type { PropPick } from '../types';

interface UsePropPicks {
  picks: PropPick[];
  loading: boolean;
}

export function usePropPicks(): UsePropPicks {
  const [picks, setPicks] = useState<PropPick[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getPropPicks()
      .then((data) => {
        if (!cancelled) setPicks(data.picks ?? []);
      })
      // a 404 or outage means prop odds are not connected yet, which the section shows as its empty state.
      .catch(() => {
        if (!cancelled) setPicks([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { picks, loading };
}
