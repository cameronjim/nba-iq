import type { NumericLike } from './core';
import type { BaselineDescriptor, PredictionRun, SlatePool } from './predictions';
import type { VsUsual, WatchlistEvidence, WatchlistReason } from './watchlist';

export type SlateSort = 'impact' | 'edge';

export type VsUsualCategory = 'reb' | 'ast' | 'stl' | 'blk' | 'fg3m';

export interface CategoryVsUsual {
  stat: VsUsualCategory;
  usual: NumericLike;
  projected: NumericLike;
  delta: NumericLike;
}

// both sides are "if he plays", so a game-time decision never reads as a lost role.
export interface PlayerVsUsual {
  minutes: VsUsual;
  points: VsUsual;
}

export interface SlateVsUsual extends PlayerVsUsual {
  // the two categories that moved furthest from his usual, biggest first.
  categories: CategoryVsUsual[];
}

export interface SlateProjectedCategories {
  reb: NumericLike | null;
  ast: NumericLike | null;
  stl: NumericLike | null;
  blk: NumericLike | null;
  tov: NumericLike | null;
  fg3m: NumericLike | null;
}

export interface SlatePlayer {
  nba_player_id: string;
  name: string;
  // true when `name` is a stand-in built from the NBA id; render it as an id, not a person.
  name_is_placeholder: boolean;
  team_abbr: string | null;
  prob_active: NumericLike | null;
  // unconditional: availability is already priced in.
  proj_pts: NumericLike | null;
  // conditional: given he plays, the same basis as proj_min_p50 and projected.
  proj_pts_cond: NumericLike | null;
  proj_min_p50: NumericLike | null;
  projected: SlateProjectedCategories;
  // null means he has too little history to have a usual, which is not "unchanged".
  usual_min: NumericLike | null;
  usual_pts: NumericLike | null;
  // min_vs_usual compares two per-appearance numbers; pts_vs_usual also carries availability.
  min_vs_usual: NumericLike | null;
  pts_vs_usual: NumericLike | null;
  baseline_games: number;
  // summed z-scores across the nine categories; 0 is an average night on the slate.
  impact: NumericLike | null;
  // how far tonight departs from his usual in either direction; null without a usual.
  edge: NumericLike | null;
  vs_usual: SlateVsUsual | null;
  reasons: WatchlistReason[];
  evidence: WatchlistEvidence;
  spotlight: boolean;
  slate_spotlight: boolean;
  // the current injury report, which can be newer than the projection.
  injury_status?: string | null;
  injury_status_raw?: string | null;
  injury_detail?: string | null;
  injury_as_of?: string | null;
  injury_changed_after_run?: boolean;
}

export interface SlateGame {
  nba_game_id: string;
  game_status: string | null;
  home_team_id: string | null;
  home_team_abbr: string | null;
  away_team_id: string | null;
  away_team_abbr: string | null;
  // preseason rows take their minutes from a tier prior, not the regular-season model.
  preseason: boolean;
  top_impact: NumericLike | null;
  top_edge: NumericLike | null;
  players: SlatePlayer[];
}

export interface SlateRun extends PredictionRun {
  // the injury-report instant the run could see, falling back to its forecast cutoff.
  information_as_of: string | null;
  // first and last game dates the run projected; each daily run looks a week ahead.
  covers_from: string | null;
  covers_to: string | null;
}

export interface SlateResponse {
  date: string;
  sort: SlateSort;
  run: SlateRun | null;
  // false when the date falls outside the run's projected days, or there is no run.
  covered: boolean;
  pool: SlatePool;
  baseline: BaselineDescriptor;
  games: SlateGame[];
}
