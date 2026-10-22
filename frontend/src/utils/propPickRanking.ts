import type { PropPick } from '../types';

function isBetter(a: PropPick, b: PropPick): boolean {
  if (a.ev !== b.ev) return a.ev > b.ev;
  if (a.model_prob !== b.model_prob) return a.model_prob > b.model_prob;
  return Math.abs(a.price) < Math.abs(b.price);
}

export function rankPicks(picks: PropPick[]): PropPick[] {
  return [...picks].sort((a, b) => b.ev - a.ev);
}

export function bestPickPerMarket(picks: PropPick[]): PropPick[] {
  const best = new Map<string, PropPick>();
  for (const pick of picks) {
    const key = `${pick.player_name}|${pick.game_date}|${pick.market}`;
    const current = best.get(key);
    if (!current || isBetter(pick, current)) best.set(key, pick);
  }
  return [...best.values()];
}
