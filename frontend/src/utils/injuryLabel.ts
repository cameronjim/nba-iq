export type InjuryTone = 'error' | 'warning' | 'success' | 'muted';

export interface InjuryLabel {
  short: string;
  tone: InjuryTone;
  detail: string | null;
}

const EXACT: Record<string, { short: string; tone: InjuryTone }> = {
  out: { short: 'OUT', tone: 'error' },
  'out for season': { short: 'OUT', tone: 'error' },
  'out indefinitely': { short: 'OUT', tone: 'error' },
  suspended: { short: 'OUT', tone: 'error' },
  'not with team': { short: 'OUT', tone: 'error' },
  doubtful: { short: 'DOUBTFUL', tone: 'error' },
  questionable: { short: 'GTD', tone: 'warning' },
  'game time decision': { short: 'GTD', tone: 'warning' },
  gtd: { short: 'GTD', tone: 'warning' },
  'day to day': { short: 'GTD', tone: 'warning' },
  probable: { short: 'PROB', tone: 'success' },
  'g league': { short: 'G LEAGUE', tone: 'muted' },
};

// bare labels say nothing the badge does not, so they never become a detail line.
const BARE_LABELS = new Set([
  'out',
  'suspended',
  'doubtful',
  'questionable',
  'game time decision',
  'gtd',
  'day to day',
  'probable',
  'g league',
]);

function classify(normalized: string): { short: string; tone: InjuryTone } {
  const exact = EXACT[normalized];
  if (exact) return exact;
  if (/^(expected to be out|out for|out until|out indefinitely)\b/.test(normalized)) {
    return { short: 'OUT', tone: 'error' };
  }
  if (normalized.startsWith('g league')) return { short: 'G LEAGUE', tone: 'muted' };
  return { short: 'INJ', tone: 'warning' };
}

export function injuryLabel(status: string | null, detail?: string | null): InjuryLabel | null {
  const original = status?.replace(/_/g, ' ').replace(/\s+/g, ' ').trim() ?? '';
  if (!original) return null;
  const normalized = original.toLowerCase().replace(/-/g, ' ');
  const suppliedDetail = detail?.trim() || null;
  const phrase = BARE_LABELS.has(normalized) ? null : original;
  return { ...classify(normalized), detail: suppliedDetail ?? phrase };
}
