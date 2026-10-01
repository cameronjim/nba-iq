import type { PredictionRun } from './predictions';
import type { OutlookCategoryKey } from './weeklyOutlook';

export interface DecisionWindow {
  from: string;
  to: string;
  days: number;
}

export type StartSitStatus = 'ok' | 'empty_roster' | 'no_run' | 'no_games';

export interface StartSitEntry {
  player_id: number;
  nba_player_id: string;
  name: string;
  nba_game_id: string;
  opponent: string | null;
  home: boolean | null;
  impact: number | null;
  points_if_plays: number | null;
  minutes_if_plays: number | null;
  prob_active: number | null;
  start: boolean;
  sentence: string;
}

export interface StartSitDay {
  date: string;
  players: StartSitEntry[];
  recommendation: string;
}

export interface StartSitResponse {
  status: StartSitStatus;
  window: DecisionWindow;
  slots: number;
  run: PredictionRun | null;
  value_basis: string;
  days: StartSitDay[];
}

export type StreamersStatus = 'ok' | 'empty_roster' | 'no_candidates';

export interface Streamer {
  id: number;
  nba_id: string | null;
  name: string;
  team: string | null;
  position: string | null;
  games: number | null;
  score: number;
  drivers: Array<{ category: string; label: string; value: number }>;
  basis: 'projection' | 'season_average';
  mean_prob_play: number | null;
  sentence: string;
}

export interface StreamersResponse {
  status: StreamersStatus;
  window: DecisionWindow;
  run: PredictionRun | null;
  score_basis: string;
  streamers: Streamer[];
}

export type TradeCheckStatus = 'ok' | 'empty_roster' | 'no_run' | 'no_games';

export interface TradePlayer {
  id: number;
  nba_id: string | null;
  name: string;
  games: number;
}

export interface TradeCategoryDelta {
  category: OutlookCategoryKey;
  label: string;
  lower_is_better: boolean;
  before: number | null;
  after: number | null;
  delta: number | null;
}

export interface TradeCheckResponse {
  status: TradeCheckStatus;
  window: DecisionWindow;
  run: PredictionRun | null;
  seed: number;
  opponent_definition: string | null;
  give: TradePlayer[];
  get: TradePlayer[];
  before_expected_wins: number | null;
  after_expected_wins: number | null;
  delta_expected_wins: number | null;
  categories: TradeCategoryDelta[];
  verdict: string | null;
}
