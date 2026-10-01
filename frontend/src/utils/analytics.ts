import type { AnalyticsStat, NumericLike } from '../types';
import { toStatNumber } from './stats';

const STAT_LABELS: Record<AnalyticsStat, string> = {
  pts: 'PTS',
  reb: 'REB',
  ast: 'AST',
  stl: 'STL',
  blk: 'BLK',
  fg3m: '3PM',
  tov: 'TOV',
  fg_impact: 'FG Impact',
  ft_impact: 'FT Impact',
  minutes: 'MIN',
};

const STAT_HINTS: Partial<Record<AnalyticsStat, string>> = {
  fg_impact:
    'Attempt-weighted excess makes: field goals made above what an average shooter would make on the same volume, so efficiency on real volume outranks a perfect 1-for-1.',
  ft_impact:
    'Attempt-weighted excess makes: free throws made above what an average shooter would make on the same volume, so efficiency on real volume outranks a perfect 1-for-1.',
  tov: 'Percentile is inverted for turnovers: a higher percentile means fewer giveaways.',
};

export function statLabel(stat: string): string {
  return STAT_LABELS[stat as AnalyticsStat] ?? stat.replace(/_/g, ' ').toUpperCase();
}

export function statHint(stat: string): string | null {
  return STAT_HINTS[stat as AnalyticsStat] ?? null;
}

export function chartNumber(value: NumericLike | null | undefined): number {
  return toStatNumber(value) ?? 0;
}

export function clampPercentile(value: NumericLike | null | undefined): number {
  const parsed = toStatNumber(value) ?? 0;
  return Math.min(100, Math.max(0, parsed));
}

export function ordinal(value: number): string {
  const n = Math.round(value);
  const lastTwo = n % 100;
  if (lastTwo >= 11 && lastTwo <= 13) return `${n}th`;
  switch (n % 10) {
    case 1: return `${n}st`;
    case 2: return `${n}nd`;
    case 3: return `${n}rd`;
    default: return `${n}th`;
  }
}

export function percentileTier(percentile: number): 'success' | 'primary' | 'error' {
  if (percentile >= 75) return 'success';
  if (percentile >= 25) return 'primary';
  return 'error';
}

// the parts are split out because `new Date('YYYY-MM-DD')` parses as utc midnight, one day early west of greenwich.
export function formatGameDate(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return iso;
  const [, y, m, d] = match;
  const date = new Date(Number(y), Number(m) - 1, Number(d));
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export function formatTimestamp(iso: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function parseInstant(iso: string | null): Date | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

// a bare clock time is ambiguous across viewers, so these always carry the zone.
export function formatTimestampWithZone(iso: string | null, timeZone?: string): string | null {
  const date = parseInstant(iso);
  if (!date) return null;
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
    timeZone,
  });
}

export function formatTimeWithZone(iso: string | null, timeZone?: string): string | null {
  const date = parseInstant(iso);
  if (!date) return null;
  return date.toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
    timeZone,
  });
}

// drops the date from `iso` when it falls on the same local day as `reference`.
export function formatTimestampBeside(
  iso: string | null,
  reference: string | null,
  timeZone?: string
): string | null {
  const date = parseInstant(iso);
  const ref = parseInstant(reference);
  if (!date) return null;
  const day = (d: Date): string => d.toLocaleDateString('en-CA', { timeZone });
  return ref && day(ref) === day(date)
    ? formatTimeWithZone(iso, timeZone)
    : formatTimestampWithZone(iso, timeZone);
}

export function bucketLabel(lo: NumericLike, hi: NumericLike): string {
  const loNum = chartNumber(lo);
  const hiNum = chartNumber(hi);
  const decimals = Number.isInteger(loNum) && Number.isInteger(hiNum) ? 0 : 1;
  return `${loNum.toFixed(decimals)}-${hiNum.toFixed(decimals)}`;
}

export interface DeltaDisplay {
  text: string;
  className: string;
  notable: boolean;
}

// a null `z` means the sample was too small to standardize, so the row stays grey.
export function deltaDisplay(
  delta: NumericLike,
  z: NumericLike | null,
  lowerIsBetter: boolean
): DeltaDisplay {
  const deltaNum = toStatNumber(delta) ?? 0;
  const zNum = toStatNumber(z);
  const text = `${deltaNum > 0 ? '+' : ''}${deltaNum.toFixed(1)}`;

  if (zNum === null) return { text, className: 'text-faint', notable: false };

  const notable = Math.abs(zNum) > 1;
  const improved = lowerIsBetter ? deltaNum < 0 : deltaNum > 0;
  if (!notable) return { text, className: 'text-muted', notable };
  return {
    text,
    className: improved ? 'text-success font-semibold' : 'text-error font-semibold',
    notable,
  };
}
