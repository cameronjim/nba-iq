import { useCallback } from 'react';
import { getBettingOdds } from '../api/client';
import { useCachedResource } from './useCachedResource';
import { CACHE_KEYS } from '../api/resourceCache';
import type { BettingGame } from '../types';

interface UseBettingOdds {
  odds: BettingGame[];
  oddsLoading: boolean;
  oddsError: string;
  reloadOdds: () => void;
}

export function useBettingOdds(): UseBettingOdds {
  const { data, loading, error, reload } = useCachedResource(CACHE_KEYS.odds, getBettingOdds, {
    errorMessage: 'Failed to load odds. ESPN may be unavailable. Try again in a minute.',
  });
  const reloadOdds = useCallback((): void => {
    void reload();
  }, [reload]);

  return { odds: data?.games ?? [], oddsLoading: loading, oddsError: error, reloadOdds };
}
