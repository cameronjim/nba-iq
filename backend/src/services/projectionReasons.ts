import { stddev } from './analytics.js';
import { round, type ConditionalStat } from './slate.js';
import {
  NOTABLE_MINUTES_DELTA,
  daysSince,
  deltaOf,
  type PlayerBaseline,
} from './baselines.js';


export const REASON_CODES = [
  'ROLE_INCREASE',
  'SHOT_VOLUME_SURGE',
  'RETURNING_FROM_ABSENCE',
  'HOT_STREAK',
  'TEAMMATE_ABSENCE',
] as const;

export type ReasonCode = (typeof REASON_CODES)[number];

export const DEVIATION_STATS = [
  'minutes',
  'pts',
  'reb',
  'ast',
  'stl',
  'blk',
  'fg3m',
] as const;

export type DeviationStat = (typeof DEVIATION_STATS)[number];

export const DEVIATION_WEIGHTS: Record<DeviationStat, number> = {
  minutes: 2,
  pts: 1.5,
  reb: 1,
  ast: 1,
  stl: 1,
  blk: 1,
  fg3m: 1,
};

export type ConditionalLine = Record<ConditionalStat, number | null>;

export const ROLE_INCREASE_MIN_DELTA = NOTABLE_MINUTES_DELTA;

export const SHOT_VOLUME_SURGE_FGA_DELTA = 2.5;

export const RETURN_GAP_DAYS = 7;

export const RETURN_GAP_MAX_DAYS = 45;

export const RETURN_MIN_PROB_ACTIVE = 0.6;

export const HOT_STREAK_STDDEV_MULTIPLE = 1.5;

export const TEAMMATE_ABSENCE_MIN_MINUTES = 28;

export const TEAMMATE_ABSENCE_MAX_PROB_ACTIVE = 0.35;

export interface VsUsual {
  usual: number | null;
  projected: number | null;
  delta: number | null;
}

export interface AbsentTeammate {
  name: string;
  usual_minutes: number;
  prob_active: number;
}

export interface ProjectionEvidence {
  fga_usual?: number;
  fga_projected?: number;
  fga_delta?: number;
  days_since_played?: number;
  last_played_date?: string;
  pts_recent?: number;
  pts_sd?: number;
  pts_recent_delta?: number;
  teammate_out?: string;
  teammate_out_minutes?: number;
  teammate_out_prob_active?: number;
}

export interface ReasonInput {
  prob_active: number | null;
  baseline_games: number;
  deltas: Partial<Record<DeviationStat, number>>;
  minutes: VsUsual;
  points: VsUsual;
  shots: VsUsual;
  days_since_played: number | null;
  last_played_date: string | null;
  pts_recent: number | null;
  pts_sd: number | null;
  teammate_out: AbsentTeammate | null;
}

export interface UpsideDriver {
  stat: DeviationStat;
  delta: number;
  scaled: number;
}

export interface Teammate {
  name: string;
  usual_minutes: number | null;
  prob_active: number | null;
}

export function hasRoleIncrease(delta: number | null): boolean {
  return delta !== null && delta >= ROLE_INCREASE_MIN_DELTA;
}

export function hasShotVolumeSurge(delta: number | null): boolean {
  return delta !== null && delta >= SHOT_VOLUME_SURGE_FGA_DELTA;
}

export function isReturningFromAbsence(
  daysSincePlayed: number | null,
  probActive: number | null
): boolean {
  if (daysSincePlayed === null) return false;
  if (daysSincePlayed < RETURN_GAP_DAYS || daysSincePlayed > RETURN_GAP_MAX_DAYS) return false;
  return probActive !== null && probActive >= RETURN_MIN_PROB_ACTIVE;
}

export function isHotStreak(
  ptsRecent: number | null,
  ptsUsual: number | null,
  ptsSd: number | null
): boolean {
  if (ptsRecent === null || ptsUsual === null || ptsSd === null || ptsSd <= 0) return false;
  return ptsRecent - ptsUsual >= HOT_STREAK_STDDEV_MULTIPLE * ptsSd;
}

export function findAbsentTeammate(teammates: Teammate[]): AbsentTeammate | null {
  let best: AbsentTeammate | null = null;
  for (const mate of teammates) {
    const minutes = mate.usual_minutes;
    const prob = mate.prob_active;
    if (prob === null || prob > TEAMMATE_ABSENCE_MAX_PROB_ACTIVE) continue;
    if (minutes === null || minutes < TEAMMATE_ABSENCE_MIN_MINUTES) continue;
    if (!best || minutes > best.usual_minutes) {
      best = { name: mate.name, usual_minutes: minutes, prob_active: prob };
    }
  }
  return best;
}

export function reasonsFor(input: ReasonInput): ReasonCode[] {
  const reasons: ReasonCode[] = [];
  if (hasRoleIncrease(input.minutes.delta)) reasons.push('ROLE_INCREASE');
  if (hasShotVolumeSurge(input.shots.delta)) reasons.push('SHOT_VOLUME_SURGE');
  if (isReturningFromAbsence(input.days_since_played, input.prob_active)) {
    reasons.push('RETURNING_FROM_ABSENCE');
  }
  if (isHotStreak(input.pts_recent, input.points.usual, input.pts_sd)) {
    reasons.push('HOT_STREAK');
  }
  if (input.teammate_out !== null) reasons.push('TEAMMATE_ABSENCE');
  return reasons;
}

export function evidenceFor(input: ReasonInput, reasons: ReasonCode[]): ProjectionEvidence {
  const evidence: ProjectionEvidence = {};
  const set = new Set<ReasonCode>(reasons);

  if (set.has('SHOT_VOLUME_SURGE') && input.shots.delta !== null) {
    evidence.fga_usual = round(input.shots.usual, 1) as number;
    evidence.fga_projected = round(input.shots.projected, 1) as number;
    evidence.fga_delta = round(input.shots.delta, 1) as number;
  }
  if (set.has('RETURNING_FROM_ABSENCE') && input.days_since_played !== null) {
    evidence.days_since_played = input.days_since_played;
    if (input.last_played_date) evidence.last_played_date = input.last_played_date;
  }
  if (
    set.has('HOT_STREAK') &&
    input.pts_recent !== null &&
    input.points.usual !== null &&
    input.pts_sd !== null
  ) {
    evidence.pts_recent = round(input.pts_recent, 1) as number;
    evidence.pts_sd = round(input.pts_sd, 1) as number;
    evidence.pts_recent_delta = round(input.pts_recent - input.points.usual, 1) as number;
  }
  if (set.has('TEAMMATE_ABSENCE') && input.teammate_out) {
    evidence.teammate_out = input.teammate_out.name;
    evidence.teammate_out_minutes = round(input.teammate_out.usual_minutes, 1) as number;
    evidence.teammate_out_prob_active = round(input.teammate_out.prob_active, 3) as number;
  }

  return evidence;
}

export function vsUsualOf(projected: number | null, usual: number | null): VsUsual {
  return { usual, projected, delta: deltaOf(projected, usual) };
}

// minutes are the conditional p50 and the rest conditional means, so every delta reads "if he plays".
export function reasonInputFor(params: {
  gameDate: string;
  probActive: number | null;
  minutes: number | null;
  conditional: ConditionalLine;
  baseline: PlayerBaseline;
  teammates: Teammate[];
}): ReasonInput {
  const { gameDate, probActive, minutes, conditional, baseline, teammates } = params;
  const usual = baseline.avg;

  const deltas: Partial<Record<DeviationStat, number>> = {};
  for (const stat of DEVIATION_STATS) {
    const projected = stat === 'minutes' ? minutes : conditional[stat];
    const delta = deltaOf(projected, usual[stat]);
    if (delta !== null) deltas[stat] = delta;
  }

  return {
    prob_active: probActive,
    baseline_games: baseline.games,
    deltas,
    minutes: vsUsualOf(minutes, usual.minutes),
    points: vsUsualOf(conditional.pts, usual.pts),
    shots: vsUsualOf(conditional.fga, usual.fga),
    days_since_played: daysSince(gameDate, baseline.last_played_date),
    last_played_date: baseline.last_played_date,
    pts_recent: baseline.pts_recent,
    pts_sd: baseline.pts_sd,
    teammate_out: findAbsentTeammate(teammates),
  };
}

export function deviationScales(
  pool: Array<Partial<Record<DeviationStat, number>>>
): Map<DeviationStat, number> {
  const scales = new Map<DeviationStat, number>();
  for (const stat of DEVIATION_STATS) {
    const present = pool
      .map((deltas) => deltas[stat])
      .filter((v): v is number => v !== undefined && Number.isFinite(v));
    if (present.length === 0) continue;
    scales.set(stat, stddev(present));
  }
  return scales;
}

export function upsideOf(
  deltas: Partial<Record<DeviationStat, number>>,
  scales: Map<DeviationStat, number>
): { upside: number | null; drivers: UpsideDriver[] } {
  let weighted = 0;
  let weight = 0;
  const drivers: UpsideDriver[] = [];

  for (const [stat, sd] of scales) {
    const value = deltas[stat];
    if (value === undefined || !Number.isFinite(value)) continue;
    const scaled = sd === 0 ? 0 : value / sd;
    weighted += DEVIATION_WEIGHTS[stat] * scaled;
    weight += DEVIATION_WEIGHTS[stat];
    if (scaled > 0) {
      drivers.push({ stat, delta: round(value, 1) as number, scaled: round(scaled, 3) as number });
    }
  }

  if (weight === 0) return { upside: null, drivers: [] };
  drivers.sort(
    (a, b) => DEVIATION_WEIGHTS[b.stat] * b.scaled - DEVIATION_WEIGHTS[a.stat] * a.scaled
  );
  return { upside: round(weighted / weight, 3), drivers };
}

export function teammateKey(gameId: string, teamAbbr: string): string {
  return `${gameId}|${teamAbbr}`;
}

export interface RosterEntry extends Teammate {
  id: string;
  game_id: string;
  team_abbr: string | null;
}

export function groupTeammates(roster: RosterEntry[]): Map<string, RosterEntry[]> {
  const groups = new Map<string, RosterEntry[]>();
  for (const entry of roster) {
    if (!entry.team_abbr) continue;
    const key = teammateKey(entry.game_id, entry.team_abbr);
    const list = groups.get(key) ?? [];
    list.push(entry);
    groups.set(key, list);
  }
  return groups;
}

export function teammatesOf(
  entry: Pick<RosterEntry, 'id' | 'game_id' | 'team_abbr'>,
  groups: Map<string, RosterEntry[]>
): Teammate[] {
  if (!entry.team_abbr) return [];
  return (groups.get(teammateKey(entry.game_id, entry.team_abbr)) ?? [])
    .filter((mate) => mate.id !== entry.id)
    .map((mate) => ({
      name: mate.name,
      usual_minutes: mate.usual_minutes,
      prob_active: mate.prob_active,
    }));
}
