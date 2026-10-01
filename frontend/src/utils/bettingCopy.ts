import { formatAmerican, formatLine } from './formatOdds';
import type {
  Bet, BetSelection, BetStatus, BettingGame, BettingPick, LedgerSummary,
  SpreadMarket, StraightMarket, TotalMarket,
} from '../types';

const CATEGORY_LABELS: Record<BettingPick['category'], string> = {
  best_value: 'Best value',
  safe: 'Safer',
  hail_mary: 'Long shot',
};

const CONFIDENCE_WORDS: Record<BettingPick['confidence'], string> = {
  low: 'Low confidence',
  medium: 'Medium confidence',
  high: 'High confidence',
};

const RESULT_WORDS: Record<BetStatus, string> = {
  pending: 'Pending',
  won: 'Won',
  lost: 'Lost',
  push: 'Push',
};

export function priceText(price: number | null | undefined): string {
  return price == null ? 'not posted' : formatAmerican(price);
}

export function favoredText(spread: SpreadMarket | undefined, homeTeam: string, awayTeam: string): string {
  if (!spread) return 'spread not posted';
  if (spread.home_line === 0) return 'rated even';
  // a negative home line means the home team gives points, so it is the favorite.
  const favorite = spread.home_line < 0 ? homeTeam : awayTeam;
  return `${favorite} favored by ${Math.abs(spread.home_line)}`;
}

export function totalText(total: TotalMarket | undefined): string {
  return total ? `total ${total.line}` : 'total not posted';
}

export function gameSentence(game: BettingGame): string {
  const { spread, total, moneyline } = game.markets;
  const head = `${game.away_team} at ${game.home_team} · ${game.tipoff}`;
  if (!spread && !total && !moneyline) return `${head} · odds not posted`;
  return `${head} · ${favoredText(spread, game.home_team, game.away_team)} · ${totalText(total)}`;
}

function percent(p: number): string {
  return `${Math.round(p * 100)}%`;
}

export function chanceSentence(estimate: number, implied: number, impliedNoVig: number | null): string {
  // the no-vig price is the fairer comparison, so it wins whenever both sides were priced.
  const priceChance = impliedNoVig ?? implied;
  return `Claude thinks this hits about ${percent(estimate)} of the time; the price implies ${percent(priceChance)}.`;
}

export function teamFromMatchup(matchup: string, selection: BetSelection): string | null {
  const [away, home] = matchup.split(' @ ');
  if (!away || !home) return null;
  if (selection === 'home') return home;
  if (selection === 'away') return away;
  return null;
}

export function matchupWords(matchup: string): string {
  return matchup.replace(' @ ', ' at ');
}

interface StraightBetFields {
  market: StraightMarket;
  selection: BetSelection;
  selection_label: string;
  matchup: string;
  american_odds: number;
}

export function straightBetText(bet: StraightBetFields): string {
  const odds = formatAmerican(bet.american_odds);
  if (bet.market === 'moneyline') {
    const team = teamFromMatchup(bet.matchup, bet.selection);
    return team ? `${team} to win (${odds})` : bet.selection_label;
  }
  return `${bet.selection_label} (${odds})`;
}

export function categoryLabel(category: BettingPick['category']): string {
  return CATEGORY_LABELS[category];
}

export function confidenceWord(confidence: BettingPick['confidence']): string {
  return CONFIDENCE_WORDS[confidence];
}

export function resultWord(status: BetStatus): string {
  return RESULT_WORDS[status];
}

export function moneyText(amount: number): string {
  const abs = Math.abs(amount);
  const digits = Number.isInteger(abs) ? abs.toString() : abs.toFixed(2);
  return amount < 0 ? `-$${digits}` : `$${digits}`;
}

export function ledgerSummarySentence(summary: LedgerSummary, hasMoney: boolean): string {
  const settled = summary.wins + summary.losses + summary.pushes;
  if (settled === 0) {
    return summary.pending > 0 ? `No settled bets yet, ${summary.pending} pending.` : 'No settled bets yet.';
  }
  const noun = settled === 1 ? 'settled bet' : 'settled bets';
  const pendingClause = summary.pending > 0 ? `, ${summary.pending} pending` : '';
  if (!hasMoney) return `You have ${settled} ${noun}, ${summary.wins} won${pendingClause}.`;
  const net = summary.net;
  const standing = net > 0 ? `up ${moneyText(net)}` : net < 0 ? `down ${moneyText(-net)}` : 'even';
  return `You are ${standing} on ${settled} ${noun}${pendingClause}.`;
}

export function ledgerBetText(bet: Bet): string {
  const odds = bet.american_odds != null ? ` (${formatAmerican(bet.american_odds)})` : '';
  if (bet.description) return `${bet.description}${odds}`;
  if (bet.market === 'total') {
    return `${bet.selection === 'over' ? 'Over' : 'Under'} ${bet.line ?? ''}${odds}`;
  }
  const team = (bet.selection === 'home' ? bet.home_team : bet.away_team) ?? 'Unknown team';
  if (bet.market === 'moneyline') return `${team} to win${odds}`;
  return `${team}${bet.line != null ? ` ${formatLine(bet.line)}` : ''}${odds}`;
}
