import type { PredictionRun } from './predictions';

export type OutlookCategoryKey =
  | 'pts'
  | 'reb'
  | 'ast'
  | 'stl'
  | 'blk'
  | 'fg3m'
  | 'fg_pct'
  | 'ft_pct'
  | 'tov';

export type WeeklyOutlookStatus = 'ok' | 'empty_roster' | 'no_run' | 'no_games';

export interface OutlookCategory {
  category: OutlookCategoryKey;
  lower_is_better: boolean;
  mean: number | null;
  p10: number | null;
  p50: number | null;
  p90: number | null;
  opponent: number | null;
  win_probability: number | null;
}

export interface OutlookPlayer {
  player_id: number;
  nba_player_id: string | null;
  name: string;
  games_scheduled: number;
  expected_games: number;
  // probability he misses at least one scheduled game in the window.
  miss_risk: number;
  simulated_miss_risk: number;
  fallback_stats: string[];
}

export interface OutlookOpponent {
  definition: string;
  league_teams: number;
  pool_size: number;
  players_used: number;
  totals: Record<OutlookCategoryKey, number | null>;
}

export interface WeeklyOutlookResponse {
  status: WeeklyOutlookStatus;
  window: { from: string; to: string; days: number };
  roster_size: number;
  run: PredictionRun | null;
  simulation: { n: number; seed: number; dependence_rho: number } | null;
  opponent: OutlookOpponent | null;
  categories: OutlookCategory[];
  players: OutlookPlayer[];
  provenance: {
    model_version: string | null;
    predicted_at: string | null;
    fallback_spread_players: Array<{ nba_player_id: string; name: string; stats: string[] }>;
  };
}
