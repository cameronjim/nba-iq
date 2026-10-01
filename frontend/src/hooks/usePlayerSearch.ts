import { useEffect, useState } from 'react';
import { getPlayers } from '../api/client';
import type { Player } from '../types';

const DEBOUNCE_MS = 300;
const MAX_RESULTS = 8;

interface UsePlayerSearch {
  results: Player[];
  searching: boolean;
  failed: boolean;
}

export function usePlayerSearch(term: string, excludeIds: ReadonlySet<number>): UsePlayerSearch {
  const [results, setResults] = useState<Player[]>([]);
  const [searching, setSearching] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const trimmed = term.trim();
    if (!trimmed) {
      setResults([]);
      setFailed(false);
      setSearching(false);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      setSearching(true);
      getPlayers({ search: trimmed })
        .then((players) => {
          if (cancelled) return;
          setFailed(false);
          setResults(players.filter((p) => !excludeIds.has(p.id)).slice(0, MAX_RESULTS));
        })
        .catch(() => {
          if (cancelled) return;
          setFailed(true);
          setResults([]);
        })
        .finally(() => {
          if (!cancelled) setSearching(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [term, excludeIds]);

  return { results, searching, failed };
}
