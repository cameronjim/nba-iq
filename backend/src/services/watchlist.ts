import { query } from '../db.js';
import { percentRank } from './analytics.js';
import {
  CONDITIONAL_STATS,
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
  type ConditionalStat,
  type ImpactInput,
  type ProjectedStat,
  type SlatePool,
  type SlateRun,
} from './slate.js';
import {
  MIN_BASELINE_GAMES,
  baselineDescriptor,
  deltaOf,
  fetchBaselines,
  hasUsableBaseline,
  type BaselineDescriptor,
  type PlayerBaseline,
} from './baselines.js';
import {
  deviationScales,
  evidenceFor,
  groupTeammates,
  reasonInputFor,
  reasonsFor,
  teammatesOf,
  upsideOf,
  type ConditionalLine,
  type DeviationStat,
  type ProjectionEvidence,
  type ReasonCode,
  type ReasonInput,
  type UpsideDriver,
  type VsUsual,
} from './projectionReasons.js';

export {
  DEVIATION_STATS,
  DEVIATION_WEIGHTS,
  HOT_STREAK_STDDEV_MULTIPLE,
  REASON_CODES,
  RETURN_GAP_DAYS,
  RETURN_GAP_MAX_DAYS,
  RETURN_MIN_PROB_ACTIVE,
  ROLE_INCREASE_MIN_DELTA,
  SHOT_VOLUME_SURGE_FGA_DELTA,
  TEAMMATE_ABSENCE_MAX_PROB_ACTIVE,
  TEAMMATE_ABSENCE_MIN_MINUTES,
  deviationScales,
  evidenceFor,
  findAbsentTeammate,
  hasRoleIncrease,
  hasShotVolumeSurge,
  isHotStreak,
  isReturningFromAbsence,
  reasonsFor,
  upsideOf,
  type AbsentTeammate,
  type DeviationStat,
  type ReasonCode,
  type UpsideDriver,
  type VsUsual,
} from './projectionReasons.js';


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


export interface WatchlistCandidate extends ReasonInput {
  nba_player_id: string;
  name: string;
  name_is_placeholder: boolean;
  team_abbr: string | null;
  position: PlayerPositions;
  opponent_team_abbr: string | null;
  nba_game_id: string;
  game_date: string;
  impact: number | null;
  proj_pts_uncond: number | null;
  uncond: ImpactInput;
}

export type WatchlistEvidence = ProjectionEvidence;

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

  const teamOf = (row: PredictionRow): string | null =>
    row.team_abbr === null || row.team_abbr === undefined ? null : String(row.team_abbr);

  const groups = groupTeammates(
    rows.map((row) => {
      const id = String(row.nba_player_id);
      return {
        id,
        game_id: String(row.nba_game_id),
        team_abbr: teamOf(row),
        name: resolvePlayerName(row.name, id).name,
        usual_minutes: baselines.get(id)?.avg.minutes ?? null,
        prob_active: num(row.prob_active),
      };
    })
  );

  const candidates: WatchlistCandidate[] = [];

  rows.forEach((row, i) => {
    const id = String(row.nba_player_id);
    const baseline = baselines.get(id);
    if (!hasUsableBaseline(baseline)) return;
    const gameId = String(row.nba_game_id);
    const team = teamOf(row);

    const conditional = {} as ConditionalLine;
    for (const stat of CONDITIONAL_STATS) {
      conditional[stat] = num((row as Record<string, unknown>)[`c_${stat}`]);
    }

    const { name, placeholder } = resolvePlayerName(row.name, id);

    candidates.push({
      nba_player_id: id,
      name,
      name_is_placeholder: placeholder,
      team_abbr: team,
      position: parsePositions(row.position),
      opponent_team_abbr: opponentOf(team, gameTeams.get(gameId)),
      nba_game_id: gameId,
      game_date: dates[i],
      impact: impacts[i],
      proj_pts_uncond: inputs[i].pts,
      uncond: inputs[i],
      ...reasonInputFor({
        gameDate: dates[i],
        probActive: num(row.prob_active),
        minutes: num(row.proj_min_p50),
        conditional,
        baseline: baseline as PlayerBaseline,
        teammates: teammatesOf({ id, game_id: gameId, team_abbr: team }, groups),
      }),
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
