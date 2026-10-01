export type BetMarket = 'spread' | 'total' | 'moneyline' | 'prop' | 'parlay' | 'custom';
export type StraightMarket = 'spread' | 'total' | 'moneyline';
export type BetSelection = 'home' | 'away' | 'over' | 'under';
export type BetStatus = 'pending' | 'won' | 'lost' | 'push';

export interface SpreadMarket {
  home_line: number;
  away_line: number;
  home_price: number | null;
  away_price: number | null;
  home_implied: number | null;
  away_implied: number | null;
}

export interface TotalMarket {
  line: number;
  over_price: number | null;
  under_price: number | null;
  over_implied: number | null;
  under_implied: number | null;
}

export interface MoneylineMarket {
  home: number;
  away: number;
  home_implied: number;
  away_implied: number;
}

export interface BettingGame {
  espn_event_id: string;
  home_team: string;
  away_team: string;
  home_abbrev: string;
  away_abbrev: string;
  game_date: string;
  tipoff: string;
  provider: string;
  markets: {
    spread?: SpreadMarket;
    total?: TotalMarket;
    moneyline?: MoneylineMarket;
  };
}

export type PropMarket = 'pts' | 'reb' | 'ast' | 'fg3m' | 'pra' | 'stl' | 'blk' | 'tov';

export interface PropPick {
  player_name: string;
  team: string;
  opponent: string;
  game_date: string;
  market: PropMarket;
  line: number;
  side: 'over' | 'under';
  bookmaker: string;
  price: number;
  model_prob: number;
  implied_prob_novig: number | null;
  ev: number;
  prob_active: number | null;
  void_rule: string;
}

export interface PropPicksRun {
  predicted_at: string;
  information_as_of: string;
}

export interface PropPicksResponse {
  run: PropPicksRun | null;
  picks: PropPick[];
}

export interface PropRefreshResult {
  snapshots: number;
  candidates: number;
  recorded: number;
}

export interface PropSettleResult {
  examined: number;
  settled: number;
  by_result: { win: number; loss: number; push: number; void: number };
}

export interface PropMarketSummary {
  market: PropMarket | 'all';
  picks: number;
  settled: number;
  wins: number;
  losses: number;
  pushes: number;
  voids: number;
  hit_rate: number | null;
  avg_ev: number | null;
  clv_count: number;
  avg_clv_points: number | null;
}

export type WagerType = 'cash' | 'bonus_bet' | 'odds_boost';

export interface Bet {
  id: number;
  market: BetMarket;
  nba_game_id: string | null;
  home_team: string | null;
  away_team: string | null;
  game_date: string | null;
  selection: BetSelection | null;
  line: number | null;
  american_odds: number | null;
  description: string | null;
  stake: number | null;
  wager_type: WagerType;
  status: BetStatus;
  created_at: string;
  settled_at: string | null;
  net: number | null;
  to_win?: number | null;
}

export interface NewBet {
  market: BetMarket;
  nba_game_id?: string;
  selection?: BetSelection;
  line?: number | null;
  american_odds: number;
  description?: string;
  stake: number;
  wager_type?: WagerType;
}

export type NewBetGameRef = Pick<Bet, 'home_team' | 'away_team' | 'game_date'>;

export interface LedgerSummary {
  wins: number;
  losses: number;
  pushes: number;
  pending: number;
  net: number;
}
