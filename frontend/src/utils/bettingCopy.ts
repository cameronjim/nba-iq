import { formatAmerican, formatLine } from './formatOdds';
import type {
  Bet, BetStatus, BettingGame, LedgerSummary, PropMarket, PropPick, SpreadMarket, TotalMarket,
} from '../types';

const MARKET_WORDS: Record<PropMarket, string> = {
  pts: 'points',
  reb: 'rebounds',
  ast: 'assists',
  fg3m: 'threes',
  pra: 'points + rebounds + assists',
  stl: 'steals',
  blk: 'blocks',
  tov: 'turnovers',
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

export function propMarketWords(market: PropMarket): string {
  return MARKET_WORDS[market];
}

export function propSentence(pick: PropPick): string {
  const bet = `${pick.player_name} ${pick.side} ${pick.line} ${propMarketWords(pick.market)}`;
  const price = `(${formatAmerican(pick.price)}, ${pick.bookmaker})`;
  const chance = pick.implied_prob_novig != null
    ? `the model gives this ${percent(pick.model_prob)}; the price implies ${percent(pick.implied_prob_novig)}`
    : `the model gives this ${percent(pick.model_prob)}`;
  const play = pick.prob_active != null ? ` · ${percent(pick.prob_active)} to play` : '';
  return `${bet} ${price} · ${chance}${play}`;
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
