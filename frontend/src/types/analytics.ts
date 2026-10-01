import type { NumericLike } from './core';
import type { PlayerVsUsual } from './slate';

// fg_impact / ft_impact are attempt-weighted excess makes, not raw percentages.
export type AnalyticsStat =
  | 'pts'
  | 'reb'
  | 'ast'
  | 'stl'
  | 'blk'
  | 'fg3m'
  | 'tov'
  | 'fg_impact'
  | 'ft_impact'
  | 'minutes';

export interface AnalyticsPlayer {
  id: number;
  nba_id: string | null;
  name: string;
  team: string | null;
  position: string | null;
  headshot_url: string | null;
  injury_status?: string | null;
  injury_detail?: string | null;
}

export interface AnalyticsAsOf {
  logs: string | null;
  distributions: string;
}

export interface AnalyticsPool {
  key: string;
  label: string;
  definition: string;
  sample_size: NumericLike;
}

export interface StatPercentile {
  stat: AnalyticsStat;
  value: NumericLike;
  // always "higher is better"; the server has already reversed it for turnovers.
  percentile: NumericLike;
}

export interface DistributionBucket {
  lo: NumericLike;
  hi: NumericLike;
  count: NumericLike;
}

export interface StatDistribution {
  stat: AnalyticsStat;
  mean: NumericLike;
  stddev: NumericLike;
  buckets: DistributionBucket[];
  player_value: NumericLike;
}

export interface AnalyticsGameLog {
  game_date: string;
  opponent_team_abbr?: string | null;
  is_home: boolean;
  minutes: NumericLike;
  pts: NumericLike;
  reb: NumericLike;
  ast: NumericLike;
  stl: NumericLike;
  blk: NumericLike;
  tov: NumericLike;
  fgm: NumericLike;
  fga: NumericLike;
  fg3m: NumericLike;
  fg3a: NumericLike;
  ftm: NumericLike;
  fta: NumericLike;
}

export interface AnalyticsRollingPoint {
  game_date: string;
  // one `<stat>_r5` / `<stat>_r10` key per trend stat; minutes shortens to `min_`.
  [rollingKey: string]: NumericLike | null;
}

export interface Last10VsSeason {
  stat: AnalyticsStat;
  last10: NumericLike;
  season: NumericLike;
  delta: NumericLike;
  z: NumericLike | null;
}

export interface AnalyticsTrends {
  // oldest first, up to 20 games.
  games: AnalyticsGameLog[];
  rolling: AnalyticsRollingPoint[];
  last10_vs_season: Last10VsSeason[];
}

export interface ProjectedRange {
  p10: NumericLike;
  p50: NumericLike;
  p90: NumericLike;
}

export interface PlayerPrediction {
  summary?: string | null;
  projected?: Partial<Record<AnalyticsStat, NumericLike | ProjectedRange | null>>;
  confidence?: 'low' | 'medium' | 'high' | null;
  as_of?: string | null;
  model_version?: string | null;
  game_date?: string | null;
  prob_active?: NumericLike | null;
  conditional?: boolean;
  unconditional_pts?: NumericLike | null;
  // his next game against his usual; absent without enough history or from an older server.
  vs_usual?: PlayerVsUsual;
}

export interface PlayerAnalytics {
  player: AnalyticsPlayer;
  as_of: AnalyticsAsOf;
  pool: AnalyticsPool;
  percentiles: StatPercentile[];
  distributions: StatDistribution[];
  trends: AnalyticsTrends;
  prediction: PlayerPrediction | null;
}
