import { query } from '../db.js';
import { percentRank, stddev } from './analytics.js';
import {
  MINUTES_QUANTILE,
  MINUTES_STAT,
  PROB_ACTIVE_STAT,
  PROJECTED_STATS,
  getLatestCompleteRun,
  impactScores,
  num,
  poolDescriptor,
  resolvePlayerName,
  round,
  rowsOrEmpty,
  toIsoDay,
  uncondStat,
  type ImpactInput,
  type ProjectedStat,
  type SlatePool,
  type SlateRun,
} from './slate.js';
import {
  MIN_BASELINE_GAMES,
  NOTABLE_MINUTES_DELTA,
  baselineDescriptor,
  daysSince,
  deltaOf,
  fetchBaselines,
  hasUsableBaseline,
  type BaselineDescriptor,
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

export const IMPACT_PERCENTILE_FLOOR = 70;

export const WATCHLIST_LIMIT = 20;

export const UPSIDE_DRIVERS_SHOWN = 3;

export const DEFAULT_WINDOW_DAYS = 1;

export const MAX_WINDOW_DAYS = 14;


export const SPECIFIC_POSITIONS = ['PG', 'SG', 'SF', 'PF', 'C'] as const;

export type SpecificPosition = (typeof SPECIFIC_POSITIONS)[number];

export const POSITION_BUCKETS = ['G', 'F', 'C'] as const;

export type PositionBucket = (typeof POSITION_BUCKETS)[number];

export const POSITION_BUCKET_OF: Record<SpecificPosition, PositionBucket> = {
  PG: 'G',
  SG: 'G',
  SF: 'F',
  PF: 'F',
  C: 'C',
};

export const POSITION_FILTERS = ['G', 'F', 'C', 'PG', 'SG', 'SF', 'PF'] as const;

export type PositionFilter = (typeof POSITION_FILTERS)[number];

export interface PlayerPositions {
  positions: SpecificPosition[];
  buckets: PositionBucket[];
  label: string | null;
}

const NO_POSITIONS: PlayerPositions = { positions: [], buckets: [], label: null };

export function parsePositions(raw: unknown): PlayerPositions {
  if (raw === null || raw === undefined) return NO_POSITIONS;
  const tokens = String(raw)
    .toUpperCase()
    .split(/[,/\-|\s]+/)
    .map((token) => token.trim())
    .filter((token) => token.length > 0);

  const positions: SpecificPosition[] = [];
  const buckets = new Set<PositionBucket>();

  for (const token of tokens) {
    if ((SPECIFIC_POSITIONS as readonly string[]).includes(token)) {
      const specific = token as SpecificPosition;
      if (!positions.includes(specific)) positions.push(specific);
      buckets.add(POSITION_BUCKET_OF[specific]);
    } else if ((POSITION_BUCKETS as readonly string[]).includes(token)) {
      buckets.add(token as PositionBucket);
    }
  }

  const ordered = POSITION_BUCKETS.filter((bucket) => buckets.has(bucket));
  const label =
    positions.length > 0 ? positions.join('/') : ordered.length > 0 ? ordered.join('/') : null;

  return { positions, buckets: ordered, label };
}

export function matchesPosition(
  player: PlayerPositions,
  filter: PositionFilter | null
): boolean {
  if (filter === null) return true;
  if ((POSITION_BUCKETS as readonly string[]).includes(filter)) {
    return player.buckets.includes(filter as PositionBucket);
  }
  return player.positions.includes(filter as SpecificPosition);
}

export function parsePositionFilter(raw: unknown): PositionFilter | null | false {
  if (raw === undefined || raw === null || raw === '') return null;
  if (typeof raw !== 'string') return false;
  const value = raw.trim().toUpperCase();
  if (value === '' || value === 'ANY' || value === 'ALL') return null;
  return (POSITION_FILTERS as readonly string[]).includes(value)
    ? (value as PositionFilter)
    : false;
}

export function parseWindowDays(raw: unknown): number | null {
  if (raw === undefined || raw === null || raw === '') return DEFAULT_WINDOW_DAYS;
  if (typeof raw !== 'string' && typeof raw !== 'number') return null;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1 || value > MAX_WINDOW_DAYS) return null;
  return value;
}

// shifts in UTC so no local DST shift can move the date
export function shiftIsoDate(date: string, days: number): string {
  const base = Date.parse(`${date}T00:00:00Z`);
  if (Number.isNaN(base)) return date;
  return new Date(base + days * 86_400_000).toISOString().slice(0, 10);
}

export function windowRange(date: string, days: number): WatchlistWindow {
  return { from: date, to: shiftIsoDate(date, days - 1), days };
}

export function watchlistPool(sampleSize: number, days: number): SlatePool {
  const pool = poolDescriptor(sampleSize);
  if (days <= 1) return pool;
  return {
    ...pool,
    label: "Each night's slate",
    definition:
      "every player the run projects for a date, across all of that date's games; " +
      'each night in the window is scored against its own slate',
  };
}


export const ROLE_INCREASE_MIN_DELTA = NOTABLE_MINUTES_DELTA;

export const SHOT_VOLUME_SURGE_FGA_DELTA = 2.5;

export const RETURN_GAP_DAYS = 7;

export const RETURN_GAP_MAX_DAYS = 45;

export const RETURN_MIN_PROB_ACTIVE = 0.6;

export const HOT_STREAK_STDDEV_MULTIPLE = 1.5;

export const TEAMMATE_ABSENCE_MIN_MINUTES = 28;

export const TEAMMATE_ABSENCE_MAX_PROB_ACTIVE = 0.35;

const CONDITIONAL_STATS = ['pts', 'reb', 'ast', 'stl', 'blk', 'fg3m', 'fga'] as const;

type ConditionalStat = (typeof CONDITIONAL_STATS)[number];

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

export interface WatchlistCandidate {
  nba_player_id: string;
  name: string;
  name_is_placeholder: boolean;
  team_abbr: string | null;
  position: PlayerPositions;
  opponent_team_abbr: string | null;
  nba_game_id: string;
  game_date: string;
  prob_active: number | null;
  impact: number | null;
  proj_pts_uncond: number | null;
  uncond: ImpactInput;
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

export interface WatchlistEvidence {
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

export interface WatchlistGame {
  game_date: string;
  nba_game_id: string;
  opponent_team_abbr: string | null;
  minutes_p50: number | null;
  proj_pts: number | null;
  impact: number | null;
  score: number;
}

export interface WatchlistPlayer {
  nba_player_id: string;
  name: string;
  name_is_placeholder: boolean;
  team_abbr: string | null;
  position: string | null;
  game_date: string;
  nba_game_id: string;
  opponent_team_abbr: string | null;
  games_count: number;
  games: WatchlistGame[];
  score: number;
  score_per_game: number;
  upside: number;
  drivers: UpsideDriver[];
  relevance: number;
  impact: number | null;
  impact_percentile: number;
  prob_active: number | null;
  minutes: VsUsual;
  points: VsUsual;
  totals: Partial<Record<ProjectedStat, number>>;
  baseline_games: number;
  reasons: ReasonCode[];
  evidence: WatchlistEvidence;
}

export interface WatchlistWindow {
  from: string;
  to: string;
  days: number;
}

export interface PositionCoverage {
  known: number;
  unknown: number;
}

export interface WatchlistResponse {
  date: string;
  window: WatchlistWindow;
  run: SlateRun | null;
  pool: SlatePool;
  baseline: BaselineDescriptor;
  position: PositionFilter | null;
  position_options: PositionFilter[];
  position_coverage: PositionCoverage;
  players: WatchlistPlayer[];
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

export interface UpsideDriver {
  stat: DeviationStat;
  delta: number;
  scaled: number;
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

export function upsideScores(
  pool: Array<Partial<Record<DeviationStat, number>>>
): Array<number | null> {
  if (pool.length === 0) return [];
  const scales = deviationScales(pool);
  return pool.map((deltas) => upsideOf(deltas, scales).upside);
}

export function relevanceFor(impact: number | null, poolImpacts: number[]): number | null {
  if (impact === null) return null;
  const pct = percentRank(poolImpacts, impact);
  if (pct <= IMPACT_PERCENTILE_FLOOR) return 0;
  return round((pct - IMPACT_PERCENTILE_FLOOR) / (100 - IMPACT_PERCENTILE_FLOOR), 3) as number;
}

export function watchlistScore(upside: number | null, relevance: number | null): number | null {
  if (upside === null || relevance === null) return null;
  return round(Math.max(0, upside) * relevance, 3) as number;
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

export function findAbsentTeammate(
  teammates: Array<{ name: string; usual_minutes: number | null; prob_active: number | null }>
): AbsentTeammate | null {
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

export function reasonsFor(candidate: WatchlistCandidate): ReasonCode[] {
  const reasons: ReasonCode[] = [];
  if (hasRoleIncrease(candidate.minutes.delta)) reasons.push('ROLE_INCREASE');
  if (hasShotVolumeSurge(candidate.shots.delta)) reasons.push('SHOT_VOLUME_SURGE');
  if (isReturningFromAbsence(candidate.days_since_played, candidate.prob_active)) {
    reasons.push('RETURNING_FROM_ABSENCE');
  }
  if (isHotStreak(candidate.pts_recent, candidate.points.usual, candidate.pts_sd)) {
    reasons.push('HOT_STREAK');
  }
  if (candidate.teammate_out !== null) reasons.push('TEAMMATE_ABSENCE');
  return reasons;
}

export function evidenceFor(
  candidate: WatchlistCandidate,
  reasons: ReasonCode[]
): WatchlistEvidence {
  const evidence: WatchlistEvidence = {};
  const set = new Set<ReasonCode>(reasons);

  if (set.has('SHOT_VOLUME_SURGE') && candidate.shots.delta !== null) {
    evidence.fga_usual = round(candidate.shots.usual, 1) as number;
    evidence.fga_projected = round(candidate.shots.projected, 1) as number;
    evidence.fga_delta = round(candidate.shots.delta, 1) as number;
  }
  if (set.has('RETURNING_FROM_ABSENCE') && candidate.days_since_played !== null) {
    evidence.days_since_played = candidate.days_since_played;
    if (candidate.last_played_date) evidence.last_played_date = candidate.last_played_date;
  }
  if (
    set.has('HOT_STREAK') &&
    candidate.pts_recent !== null &&
    candidate.points.usual !== null &&
    candidate.pts_sd !== null
  ) {
    evidence.pts_recent = round(candidate.pts_recent, 1) as number;
    evidence.pts_sd = round(candidate.pts_sd, 1) as number;
    evidence.pts_recent_delta = round(candidate.pts_recent - candidate.points.usual, 1) as number;
  }
  if (set.has('TEAMMATE_ABSENCE') && candidate.teammate_out) {
    evidence.teammate_out = candidate.teammate_out.name;
    evidence.teammate_out_minutes = round(candidate.teammate_out.usual_minutes, 1) as number;
    evidence.teammate_out_prob_active = round(candidate.teammate_out.prob_active, 3) as number;
  }

  return evidence;
}

export interface ScoredCandidate {
  candidate: WatchlistCandidate;
  upside: number | null;
  drivers: UpsideDriver[];
  relevance: number | null;
  score: number | null;
  impact_percentile: number;
}

export function scoreCandidates(candidates: WatchlistCandidate[]): ScoredCandidate[] {
  const scales = deviationScales(candidates.map((c) => c.deltas));
  const poolImpacts = candidates
    .map((c) => c.impact)
    .filter((v): v is number => v !== null && Number.isFinite(v));

  return candidates.map((candidate) => {
    const { upside, drivers } = upsideOf(candidate.deltas, scales);
    const relevance = relevanceFor(candidate.impact, poolImpacts);
    return {
      candidate,
      upside,
      drivers,
      relevance,
      score: watchlistScore(upside, relevance),
      impact_percentile:
        candidate.impact === null ? 0 : percentRank(poolImpacts, candidate.impact),
    };
  });
}

export function groupByDate(
  candidates: WatchlistCandidate[]
): Array<{ date: string; candidates: WatchlistCandidate[] }> {
  const byDate = new Map<string, WatchlistCandidate[]>();
  for (const candidate of candidates) {
    const list = byDate.get(candidate.game_date) ?? [];
    list.push(candidate);
    byDate.set(candidate.game_date, list);
  }
  return [...byDate.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([date, group]) => ({ date, candidates: group }));
}

class Mean {
  private sum = 0;
  private count = 0;

  add(value: number | null | undefined): void {
    if (value === null || value === undefined || !Number.isFinite(value)) return;
    this.sum += value;
    this.count += 1;
  }

  get value(): number | null {
    return this.count === 0 ? null : this.sum / this.count;
  }
}

interface WindowAccumulator {
  best: ScoredCandidate;
  scoreTotal: number;
  upside: Mean;
  relevance: Mean;
  percentile: Mean;
  probActive: Mean;
  minutesProjected: Mean;
  pointsProjected: Mean;
  impactTotal: number;
  impactKnown: boolean;
  totals: Partial<Record<ProjectedStat, number>>;
  games: WatchlistGame[];
}

export function rankCandidates(
  candidates: WatchlistCandidate[],
  limit: number = WATCHLIST_LIMIT,
  position: PositionFilter | null = null
): WatchlistPlayer[] {
  const accumulators = new Map<string, WindowAccumulator>();

  for (const { candidates: nightly } of groupByDate(candidates)) {
    for (const scored of scoreCandidates(nightly)) {
      const { candidate, score } = scored;
      const id = candidate.nba_player_id;
      const contribution = score ?? 0;

      let accumulator = accumulators.get(id);
      if (!accumulator) {
        accumulator = {
          best: scored,
          scoreTotal: 0,
          upside: new Mean(),
          relevance: new Mean(),
          percentile: new Mean(),
          probActive: new Mean(),
          minutesProjected: new Mean(),
          pointsProjected: new Mean(),
          impactTotal: 0,
          impactKnown: false,
          totals: {},
          games: [],
        };
        accumulators.set(id, accumulator);
      } else if (contribution > (accumulator.best.score ?? 0)) {
        accumulator.best = scored;
      }

      accumulator.scoreTotal += contribution;
      accumulator.upside.add(scored.upside === null ? null : Math.max(0, scored.upside));
      accumulator.relevance.add(scored.relevance);
      accumulator.percentile.add(scored.impact_percentile);
      accumulator.probActive.add(candidate.prob_active);
      accumulator.minutesProjected.add(candidate.minutes.projected);
      accumulator.pointsProjected.add(candidate.points.projected);
      if (candidate.impact !== null) {
        accumulator.impactTotal += candidate.impact;
        accumulator.impactKnown = true;
      }
      for (const stat of PROJECTED_STATS) {
        const value = candidate.uncond[stat];
        if (value === null || !Number.isFinite(value)) continue;
        accumulator.totals[stat] = (accumulator.totals[stat] ?? 0) + value;
      }
      accumulator.games.push({
        game_date: candidate.game_date,
        nba_game_id: candidate.nba_game_id,
        opponent_team_abbr: candidate.opponent_team_abbr,
        minutes_p50: round(candidate.minutes.projected, 1),
        proj_pts: round(candidate.uncond.pts, 1),
        impact: candidate.impact,
        score: round(contribution, 3) as number,
      });
    }
  }

  const ranked: WatchlistPlayer[] = [];

  for (const accumulator of accumulators.values()) {
    if (accumulator.scoreTotal <= 0) continue;
    const candidate = accumulator.best.candidate;
    if (!matchesPosition(candidate.position, position)) continue;

    const gamesCount = accumulator.games.length;
    const reasons = reasonsFor(candidate);
    const totals: Partial<Record<ProjectedStat, number>> = {};
    for (const stat of PROJECTED_STATS) {
      const value = accumulator.totals[stat];
      if (value !== undefined) totals[stat] = round(value, 1) as number;
    }
    const minutesProjected = accumulator.minutesProjected.value;
    const pointsProjected = accumulator.pointsProjected.value;
    const usualMinutes = candidate.minutes.usual;
    const usualPoints = candidate.points.usual;

    ranked.push({
      nba_player_id: candidate.nba_player_id,
      name: candidate.name,
      name_is_placeholder: candidate.name_is_placeholder,
      team_abbr: candidate.team_abbr,
      position: candidate.position.label,
      opponent_team_abbr: candidate.opponent_team_abbr,
      nba_game_id: candidate.nba_game_id,
      game_date: candidate.game_date,
      games_count: gamesCount,
      games: [...accumulator.games].sort((a, b) => a.game_date.localeCompare(b.game_date)),
      score: round(accumulator.scoreTotal, 3) as number,
      score_per_game: round(accumulator.scoreTotal / gamesCount, 3) as number,
      upside: round(accumulator.upside.value ?? 0, 3) as number,
      drivers: accumulator.best.drivers.slice(0, UPSIDE_DRIVERS_SHOWN),
      relevance: round(accumulator.relevance.value ?? 0, 3) as number,
      impact: accumulator.impactKnown ? (round(accumulator.impactTotal, 2) as number) : null,
      impact_percentile: round(accumulator.percentile.value ?? 0, 1) as number,
      prob_active: round(accumulator.probActive.value, 3),
      minutes: {
        usual: round(usualMinutes, 1),
        projected: round(minutesProjected, 1),
        delta: round(deltaOf(minutesProjected, usualMinutes), 1),
      },
      points: {
        usual: round(usualPoints, 1),
        projected: round(pointsProjected, 1),
        delta: round(deltaOf(pointsProjected, usualPoints), 1),
      },
      totals,
      baseline_games: candidate.baseline_games,
      reasons,
      evidence: evidenceFor(candidate, reasons),
    });
  }

  return ranked
    .sort(
      (a, b) =>
        b.score - a.score ||
        (b.impact ?? 0) - (a.impact ?? 0) ||
        (a.name_is_placeholder === b.name_is_placeholder ? 0 : a.name_is_placeholder ? 1 : -1) ||
        a.name.localeCompare(b.name) ||
        a.nba_player_id.localeCompare(b.nba_player_id)
    )
    .slice(0, limit);
}

export type WindowPredictionRow = {
  game_date: unknown;
  nba_game_id: unknown;
  nba_player_id: unknown;
  name: unknown;
  team_abbr: unknown;
  position: unknown;
  prob_active: unknown;
  proj_min_p50: unknown;
} & { [K in ProjectedStat as `u_${K}`]: unknown } & {
  [K in ConditionalStat as `c_${K}`]: unknown;
};

// u_* is the unconditional stat (<stat>_uncond, availability priced in); c_* is conditional (bare stat name, "given he plays"); minutes always use the conditional p50 quantile
const UNCOND_PARAM_OFFSET = 7;
const COND_PARAM_OFFSET = UNCOND_PARAM_OFFSET + PROJECTED_STATS.length;

const UNCOND_PIVOT_SQL = PROJECTED_STATS.map(
  (stat, i) =>
    `MAX(CASE WHEN pgp.stat = $${UNCOND_PARAM_OFFSET + i} AND pgp.quantile IS NULL
                       THEN pgp.value END)::float AS u_${stat}`
).join(',\n              ');

const COND_PIVOT_SQL = CONDITIONAL_STATS.map(
  (stat, i) =>
    `MAX(CASE WHEN pgp.stat = $${COND_PARAM_OFFSET + i} AND pgp.quantile IS NULL
                       THEN pgp.value END)::float AS c_${stat}`
).join(',\n              ');

export async function fetchWindowPredictionRows(
  runId: number,
  from: string,
  to: string
): Promise<WindowPredictionRow[]> {
  return rowsOrEmpty<WindowPredictionRow>(() =>
    query(
      `SELECT pgp.game_date,
              pgp.nba_game_id,
              pgp.nba_player_id,
              MAX(p.name) AS name,
              MAX(p.team) AS team_abbr,
              MAX(p.position) AS position,
              MAX(CASE WHEN pgp.stat = $4 AND pgp.quantile IS NULL
                       THEN pgp.value END)::float AS prob_active,
              MAX(CASE WHEN pgp.stat = $5 AND pgp.quantile = $6
                       THEN pgp.value END)::float AS proj_min_p50,
              ${UNCOND_PIVOT_SQL},
              ${COND_PIVOT_SQL}
       FROM player_game_predictions pgp
       LEFT JOIN players p ON p.nba_id = pgp.nba_player_id
       WHERE pgp.prediction_run_id = $1
         AND pgp.game_date >= $2
         AND pgp.game_date <= $3
       GROUP BY pgp.game_date, pgp.nba_game_id, pgp.nba_player_id`,
      [
        runId,
        from,
        to,
        PROB_ACTIVE_STAT,
        MINUTES_STAT,
        MINUTES_QUANTILE,
        ...PROJECTED_STATS.map(uncondStat),
        ...CONDITIONAL_STATS,
      ]
    )
  );
}

interface GameRow {
  nba_game_id: unknown;
  home_team_abbr: unknown;
  away_team_abbr: unknown;
}

export async function fetchGameTeams(
  from: string,
  to: string
): Promise<Map<string, [string | null, string | null]>> {
  const rows = await rowsOrEmpty<GameRow>(() =>
    query(
      `SELECT nba_game_id, home_team_abbr, away_team_abbr
       FROM nba_schedule
       WHERE game_date >= $1
         AND game_date <= $2`,
      [from, to]
    )
  );

  const map = new Map<string, [string | null, string | null]>();
  for (const row of rows) {
    map.set(String(row.nba_game_id), [
      row.home_team_abbr === null || row.home_team_abbr === undefined
        ? null
        : String(row.home_team_abbr),
      row.away_team_abbr === null || row.away_team_abbr === undefined
        ? null
        : String(row.away_team_abbr),
    ]);
  }
  return map;
}

export function opponentOf(
  teamAbbr: string | null,
  teams: [string | null, string | null] | undefined
): string | null {
  if (!teamAbbr || !teams) return null;
  const [home, away] = teams;
  if (teamAbbr === home) return away;
  if (teamAbbr === away) return home;
  return null;
}

export function buildCandidates(
  rows: WindowPredictionRow[],
  baselines: Map<string, PlayerBaseline>,
  gameTeams: Map<string, [string | null, string | null]>,
  date: string
): WatchlistCandidate[] {
  const dates = rows.map((row) => toIsoDay(row.game_date) ?? date);

  const inputs: ImpactInput[] = rows.map((row) => {
    const entry = {} as ImpactInput;
    for (const stat of PROJECTED_STATS) {
      entry[stat] = num((row as Record<string, unknown>)[`u_${stat}`]);
    }
    return entry;
  });

  const impacts: Array<number | null> = new Array(rows.length).fill(null);
  const indexByDate = new Map<string, number[]>();
  dates.forEach((day, i) => {
    const list = indexByDate.get(day) ?? [];
    list.push(i);
    indexByDate.set(day, list);
  });
  for (const indices of indexByDate.values()) {
    const nightly = impactScores(indices.map((i) => inputs[i]));
    indices.forEach((i, n) => {
      impacts[i] = nightly[n];
    });
  }

  const usualMinutes = new Map<string, number | null>();
  for (const row of rows) {
    const id = String(row.nba_player_id);
    usualMinutes.set(id, baselines.get(id)?.avg.minutes ?? null);
  }

  const byGameTeam = new Map<string, Array<{ id: string; name: string; prob: number | null }>>();
  rows.forEach((row) => {
    const team = row.team_abbr === null || row.team_abbr === undefined ? null : String(row.team_abbr);
    if (!team) return;
    const key = `${String(row.nba_game_id)}|${team}`;
    const list = byGameTeam.get(key) ?? [];
    const id = String(row.nba_player_id);
    list.push({ id, name: resolvePlayerName(row.name, id).name, prob: num(row.prob_active) });
    byGameTeam.set(key, list);
  });

  const candidates: WatchlistCandidate[] = [];

  rows.forEach((row, i) => {
    const id = String(row.nba_player_id);
    const baseline = baselines.get(id);
    if (!hasUsableBaseline(baseline)) return;
    const usual = (baseline as PlayerBaseline).avg;
    const gameDate = dates[i];

    const projMinutes = num(row.proj_min_p50);
    const deltas: Partial<Record<DeviationStat, number>> = {};
    for (const stat of DEVIATION_STATS) {
      const projected =
        stat === 'minutes' ? projMinutes : num((row as Record<string, unknown>)[`c_${stat}`]);
      const delta = deltaOf(projected, usual[stat]);
      if (delta !== null) deltas[stat] = delta;
    }

    const team = row.team_abbr === null || row.team_abbr === undefined ? null : String(row.team_abbr);
    const projPts = num((row as Record<string, unknown>).c_pts);
    const projFga = num((row as Record<string, unknown>).c_fga);

    const teammates = (team ? byGameTeam.get(`${String(row.nba_game_id)}|${team}`) ?? [] : [])
      .filter((mate) => mate.id !== id)
      .map((mate) => ({
        name: mate.name,
        usual_minutes: usualMinutes.get(mate.id) ?? null,
        prob_active: mate.prob,
      }));

    const { name, placeholder } = resolvePlayerName(row.name, id);

    candidates.push({
      nba_player_id: id,
      name,
      name_is_placeholder: placeholder,
      team_abbr: team,
      position: parsePositions(row.position),
      opponent_team_abbr: opponentOf(team, gameTeams.get(String(row.nba_game_id))),
      nba_game_id: String(row.nba_game_id),
      game_date: gameDate,
      prob_active: num(row.prob_active),
      impact: impacts[i],
      proj_pts_uncond: inputs[i].pts,
      uncond: inputs[i],
      baseline_games: (baseline as PlayerBaseline).games,
      deltas,
      minutes: {
        usual: usual.minutes,
        projected: projMinutes,
        delta: deltaOf(projMinutes, usual.minutes),
      },
      points: { usual: usual.pts, projected: projPts, delta: deltaOf(projPts, usual.pts) },
      shots: { usual: usual.fga, projected: projFga, delta: deltaOf(projFga, usual.fga) },
      days_since_played: daysSince(gameDate, (baseline as PlayerBaseline).last_played_date),
      last_played_date: (baseline as PlayerBaseline).last_played_date,
      pts_recent: (baseline as PlayerBaseline).pts_recent,
      pts_sd: (baseline as PlayerBaseline).pts_sd,
      teammate_out: findAbsentTeammate(teammates),
    });
  });

  return candidates;
}

export interface WatchlistOptions {
  run?: (SlateRun & { id: number }) | null;
  limit?: number;
  days?: number;
  position?: PositionFilter | null;
}

export interface WatchlistCandidateWindow {
  pool_size: number;
  position_coverage: PositionCoverage;
  candidates: WatchlistCandidate[];
}

export async function fetchWatchlistWindow(
  window: WatchlistWindow,
  runId: number
): Promise<WatchlistCandidateWindow> {
  const empty: WatchlistCandidateWindow = {
    pool_size: 0,
    position_coverage: { known: 0, unknown: 0 },
    candidates: [],
  };

  const rows = await fetchWindowPredictionRows(runId, window.from, window.to);
  if (rows.length === 0) return empty;

  const baselines = await fetchBaselines(window.from);
  const gameTeams = await fetchGameTeams(window.from, window.to);
  const candidates = buildCandidates(rows, baselines, gameTeams, window.from);

  const known = new Set<string>();
  const unknown = new Set<string>();
  for (const candidate of candidates) {
    (candidate.position.label === null ? unknown : known).add(candidate.nba_player_id);
  }

  return {
    pool_size: rows.length,
    position_coverage: { known: known.size, unknown: unknown.size },
    candidates,
  };
}

export async function fetchWatchlistCandidates(
  date: string,
  runId: number
): Promise<{ pool_size: number; candidates: WatchlistCandidate[] }> {
  const { pool_size, candidates } = await fetchWatchlistWindow(windowRange(date, 1), runId);
  return { pool_size, candidates };
}

export async function getWatchlist(
  date: string,
  options: WatchlistOptions = {}
): Promise<WatchlistResponse> {
  const days = options.days ?? DEFAULT_WINDOW_DAYS;
  const position = options.position ?? null;
  const window = windowRange(date, days);
  const run = options.run !== undefined ? options.run : await getLatestCompleteRun();
  const runSummary = run
    ? { model_version: run.model_version, predicted_at: run.predicted_at }
    : null;
  const baseline = baselineDescriptor();
  const shared = {
    date,
    window,
    baseline,
    position,
    position_options: [...POSITION_FILTERS],
  };

  if (!run) {
    return {
      ...shared,
      run: null,
      pool: watchlistPool(0, days),
      position_coverage: { known: 0, unknown: 0 },
      players: [],
    };
  }

  const { pool_size, position_coverage, candidates } = await fetchWatchlistWindow(window, run.id);

  return {
    ...shared,
    run: runSummary,
    pool: watchlistPool(pool_size, days),
    position_coverage,
    players: rankCandidates(candidates, options.limit ?? WATCHLIST_LIMIT, position),
  };
}

export { MIN_BASELINE_GAMES };
