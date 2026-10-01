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

export interface BettingPick {
  game_id: string;
  category: 'best_value' | 'safe' | 'hail_mary';
  market: StraightMarket;
  selection: BetSelection;
  matchup: string;
  game_date: string;
  tipoff: string;
  selection_label: string;
  line: number | null;
  american_odds: number;
  implied_prob: number;
  implied_prob_novig: number | null;
  estimated_win_prob: number;
  estimate_source: 'claude';
  edge: number;
  rationale: string;
  confidence: 'low' | 'medium' | 'high';
}

export interface ParlayLeg {
  game_id: string;
  market: StraightMarket;
  selection: BetSelection;
  selection_label: string;
  matchup: string;
  american_odds: number;
}

export interface ParlaySuggestion {
  legs: ParlayLeg[];
  combined_american: number;
  combined_implied_prob: number;
  rationale: string;
  ev_note: string;
}

export interface BettingPicksResponse {
  picks: BettingPick[];
  parlay: ParlaySuggestion | null;
  summary: string;
  cached?: boolean;
  cached_at?: string;
  no_games?: boolean;
  stale?: boolean;
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
