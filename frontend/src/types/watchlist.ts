import type { NumericLike } from './core';
import type { BaselineDescriptor, PredictionRun, SlatePool } from './predictions';

// reasons explain a row; they do not score it.
export type WatchlistReason =
  | 'ROLE_INCREASE'
  | 'SHOT_VOLUME_SURGE'
  | 'RETURNING_FROM_ABSENCE'
  | 'HOT_STREAK'
  | 'TEAMMATE_ABSENCE';

export interface VsUsual {
  usual: NumericLike | null;
  projected: NumericLike | null;
  delta: NumericLike | null;
}

export type DeviationStat = 'minutes' | 'pts' | 'reb' | 'ast' | 'stl' | 'blk' | 'fg3m';

export interface UpsideDriver {
  stat: DeviationStat;
  delta: NumericLike;
  // that delta in units of the pool's spread, i.e. its contribution to `upside`.
  scaled: NumericLike;
}

// only the keys for whichever reasons fired are present.
export interface WatchlistEvidence {
  fga_usual?: NumericLike;
  fga_projected?: NumericLike;
  fga_delta?: NumericLike;
  days_since_played?: NumericLike;
  last_played_date?: string;
  pts_recent?: NumericLike;
  pts_sd?: NumericLike;
  pts_recent_delta?: NumericLike;
  teammate_out?: string;
  teammate_out_minutes?: NumericLike;
  teammate_out_prob_active?: NumericLike;
}

// the first three are roster slots, the rest exact positions; C is both.
export type WatchlistPositionFilter = 'G' | 'F' | 'C' | 'PG' | 'SG' | 'SF' | 'PF';

export interface WatchlistGame {
  game_date: string;
  nba_game_id: string;
  opponent_team_abbr: string | null;
  preseason: boolean;
  minutes_p50: NumericLike | null;
  proj_pts: NumericLike | null;
  impact: NumericLike | null;
  // this game's contribution to the window total; 0 for a flat night.
  score: NumericLike;
}

export interface WatchlistPlayer {
  nba_player_id: string;
  name: string;
  name_is_placeholder: boolean;
  team_abbr: string | null;
  // null is "unknown", not "none", which is why a specific position filter excludes him.
  position: string | null;
  // these five describe the window's BEST-SCORING game, so they agree with each other.
  game_date: string;
  nba_game_id: string;
  opponent_team_abbr: string | null;
  preseason: boolean;
  games_count: number;
  games: WatchlistGame[];
  // the window TOTAL, summed over games, so more games can outrank a better player.
  score: NumericLike;
  score_per_game: NumericLike;
  upside: NumericLike;
  drivers: UpsideDriver[];
  relevance: NumericLike;
  // summed over the window, not per game.
  impact: NumericLike | null;
  impact_percentile: NumericLike;
  prob_active: NumericLike | null;
  // usual is the one baseline; projected and delta are means over the window.
  minutes: VsUsual;
  points: VsUsual;
  totals: Partial<Record<string, NumericLike>>;
  baseline_games: number;
  reasons: WatchlistReason[];
  evidence: WatchlistEvidence;
}

export interface WatchlistWindow {
  from: string;
  // inclusive, and equal to `from` for a one-day window.
  to: string;
  days: number;
}

// counted BEFORE the position filter is applied.
export interface WatchlistPositionCoverage {
  known: number;
  unknown: number;
}

export interface WatchlistResponse {
  date: string;
  window: WatchlistWindow;
  run: PredictionRun | null;
  pool: SlatePool;
  baseline: BaselineDescriptor;
  position: WatchlistPositionFilter | null;
  // every filter the server honours, so the page never offers one it will not.
  position_options: WatchlistPositionFilter[];
  position_coverage: WatchlistPositionCoverage;
  players: WatchlistPlayer[];
}
