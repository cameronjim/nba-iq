import { useState } from 'react';
import { getWatchlist } from '../api/client';
import { todayInEastern } from '../utils/dates';
import { useCachedResource } from './useCachedResource';
import { useSlate } from './useSlate';
import type {
  SlateResponse,
  WatchlistPlayer,
  WatchlistPositionFilter,
  WatchlistResponse,
} from '../types';

export type ProjectionScope = 'tonight' | 'week';

export const WEEK_DAYS = 7;

export interface UseProjections {
  date: string;
  setDate: (date: string) => void;
  position: WatchlistPositionFilter | null;
  setPosition: (position: WatchlistPositionFilter | null) => void;
  team: string;
  setTeam: (team: string) => void;
  tonight: SlateResponse | null;
  week: WatchlistResponse | null;
  // the team filter is applied here, in the browser, since the week list is short.
  weekPlayers: WatchlistPlayer[];
  weekTeams: string[];
  loading: boolean;
  error: string;
  reload: () => Promise<void>;
}

export function useProjections(scope: ProjectionScope): UseProjections {
  const [position, setPosition] = useState<WatchlistPositionFilter | null>(null);
  const [team, setTeam] = useState('');
  const weekStart = todayInEastern();

  const slate = useSlate(scope === 'tonight');
  const watchlist = useCachedResource<WatchlistResponse>(
    `watchlist:${weekStart}:${WEEK_DAYS}:${position ?? 'any'}`,
    () => getWatchlist(weekStart, WEEK_DAYS, position),
    { enabled: scope === 'week', errorMessage: 'Failed to load the next 7 days' }
  );

  const active = scope === 'tonight' ? slate : watchlist;
  const players = watchlist.data?.players ?? [];
  const weekTeams = [
    ...new Set(players.map((p) => p.team_abbr).filter((t): t is string => t !== null)),
  ].sort();

  return {
    date: slate.date,
    setDate: slate.setDate,
    position,
    setPosition,
    team,
    setTeam,
    tonight: slate.data,
    week: watchlist.data,
    weekPlayers: team ? players.filter((p) => p.team_abbr === team) : players,
    weekTeams,
    // a scope that was just switched on has no data and no request in flight for one render.
    loading: active.loading || (active.data === null && active.error === ''),
    error: active.error,
    reload: active.reload,
  };
}
