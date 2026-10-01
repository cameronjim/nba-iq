import type { PropMarketSummary, PropRefreshResult, PropSettleResult } from '../types';

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}

export function refreshSentence(result: PropRefreshResult): string {
  return `Recorded ${plural(result.recorded, 'pick')} from ${plural(result.candidates, 'candidate')} across ${plural(result.snapshots, 'odds snapshot')}.`;
}

export function settleSentence(result: PropSettleResult): string {
  if (result.settled === 0) return 'No prop picks were ready to settle.';
  const { win, loss, push, void: voided } = result.by_result;
  const parts = [`${win} won`, `${loss} lost`, `${push} pushed`];
  if (voided > 0) parts.push(`${voided} voided`);
  return `Settled ${plural(result.settled, 'pick')}: ${parts.join(', ')}.`;
}

export function percentOrPlaceholder(value: number | null, placeholder: string): string {
  return value === null ? placeholder : `${(value * 100).toFixed(1)}%`;
}

export function clvOrPlaceholder(value: number | null, placeholder: string): string {
  if (value === null) return placeholder;
  return `${value > 0 ? '+' : ''}${value.toFixed(1)} pts`;
}

export function hasSettledPicks(markets: PropMarketSummary[]): boolean {
  return markets.some((m) => m.settled > 0);
}
