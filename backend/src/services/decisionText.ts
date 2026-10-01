import type { OutlookCategory } from './weeklySimulation.js';

export const OUTLOOK_LABELS: Record<OutlookCategory, string> = {
  pts: 'PTS',
  reb: 'REB',
  ast: 'AST',
  stl: 'STL',
  blk: 'BLK',
  fg3m: '3PM',
  fg_pct: 'FG%',
  ft_pct: 'FT%',
  tov: 'TO',
};

// below a tenth of a win a second decimal keeps a real +0.04 from printing as +0.0.
export function formatSignedWins(value: number): string {
  const digits = Math.abs(value) >= 0.095 ? 1 : 2;
  const fixed = Math.abs(value).toFixed(digits);
  const sign = value < 0 && Number(fixed) !== 0 ? '-' : '+';
  return `${sign}${fixed}`;
}

export function joinWords(words: readonly string[]): string {
  if (words.length <= 1) return words.join('');
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`;
}

export function windowPhrase(days: number): string {
  return days === 7 ? 'this week' : `in the next ${days} days`;
}
