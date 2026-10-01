import { query } from '../db.js';
import { etIsoDate } from './dates.js';
import { mean, stddev } from './analytics.js';
import {
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
  type VsUsual,
} from './projectionReasons.js';


export const TOP_PLAYERS_PER_GAME = 8;

export const SLATE_SORTS = ['impact', 'edge'] as const;

export type SlateSort = (typeof SLATE_SORTS)[number];

export const DEFAULT_SLATE_SORT: SlateSort = 'impact';

export const VS_USUAL_CATEGORIES = ['reb', 'ast', 'stl', 'blk', 'fg3m'] as const;

export type VsUsualCategory = (typeof VS_USUAL_CATEGORIES)[number];

export const VS_USUAL_CATEGORIES_SHOWN = 2;

export const COMPLETE_RUN_STATUS = 'complete';

// shadow and challenger runs share the table; only production runs are served.
export const PRODUCTION_CHANNEL = 'production';

export const UNCOND_SUFFIX = '_uncond';

export function uncondStat(stat: string): string {
  return `${stat}${UNCOND_SUFFIX}`;
}

export const PROB_ACTIVE_STAT = 'prob_active';
export const MINUTES_STAT = 'minutes';
export const POINTS_STAT = 'pts';
export const POINTS_UNCOND_STAT = uncondStat(POINTS_STAT);

export const MINUTES_QUANTILE = 0.5;

export const PRESEASON_SEASON_TYPE = 'Pre Season';

export function isPreseason(seasonType: unknown): boolean {
  return seasonType === PRESEASON_SEASON_TYPE;
}

export const PROJECTED_STATS = [
  'pts',
  'reb',
  'ast',
  'stl',
  'blk',
  'tov',
  'fg3m',
  'fgm',
  'fga',
  'ftm',
  'fta',
] as const;

export type ProjectedStat = (typeof PROJECTED_STATS)[number];

export const CONDITIONAL_STATS = ['pts', 'reb', 'ast', 'stl', 'blk', 'fg3m', 'fga'] as const;

export type ConditionalStat = (typeof CONDITIONAL_STATS)[number];

export const DISPLAY_CATEGORIES = ['reb', 'ast', 'stl', 'blk', 'tov', 'fg3m'] as const;

export type DisplayCategory = (typeof DISPLAY_CATEGORIES)[number];

export const IMPACT_CATEGORIES = [
  'pts',
  'reb',
  'ast',
  'stl',
  'blk',
  'fg3m',
  'fg',
  'ft',
  'tov',
] as const;

export type ImpactCategory = (typeof IMPACT_CATEGORIES)[number];

export const REVERSED_IMPACT_CATEGORIES: ReadonlySet<ImpactCategory> = new Set<ImpactCategory>([
  'tov',
]);

export const SPOTLIGHT_PER_GAME = 3;
export const SLATE_SPOTLIGHT_COUNT = 10;

export const IMPACT_POOL_KEY = 'slate';
export const IMPACT_POOL_LABEL = "Tonight's slate";
export const IMPACT_POOL_DEFINITION =
  "every player the run projects for this date, across all of the date's games";

export const PLACEHOLDER_NAME_SUFFIX = '(new roster)';

export interface SlateRun {
  model_version: string;
  predicted_at: string | null;
}

export interface SlatePool {
  key: string;
  label: string;
  definition: string;
  sample_size: number;
}

export type SlateProjectedCategories = Record<DisplayCategory, number | null>;

export interface CategoryVsUsual {
  stat: VsUsualCategory;
  usual: number;
  projected: number;
  delta: number;
}

export interface PlayerVsUsual {
  minutes: VsUsual;
  points: VsUsual;
}

export interface SlateVsUsual extends PlayerVsUsual {
  categories: CategoryVsUsual[];
}

export interface SlatePlayer {
  nba_player_id: string;
  name: string;
  name_is_placeholder: boolean;
  team_abbr: string | null;
  prob_active: number | null;
  proj_pts: number | null;
  proj_pts_cond: number | null;
  proj_min_p50: number | null;
  projected: SlateProjectedCategories;
  usual_min: number | null;
  usual_pts: number | null;
  min_vs_usual: number | null;
  pts_vs_usual: number | null;
  baseline_games: number;
  impact: number | null;
  edge: number | null;
  vs_usual: SlateVsUsual | null;
  reasons: ReasonCode[];
  evidence: ProjectionEvidence;
  spotlight: boolean;
  slate_spotlight: boolean;
  injury_status: string | null;
  injury_status_raw: string | null;
  injury_detail: string | null;
  injury_as_of: string | null;
  injury_changed_after_run: boolean;
}

export interface SlateGame {
  nba_game_id: string;
  game_status: string | null;
  home_team_id: string | null;
  home_team_abbr: string | null;
  away_team_id: string | null;
  away_team_abbr: string | null;
  preseason: boolean;
  top_impact: number | null;
  top_edge: number | null;
  players: SlatePlayer[];
}

export interface SlateResponse {
  date: string;
  sort: SlateSort;
  run: SlateRun | null;
  pool: SlatePool;
  baseline: BaselineDescriptor;
  games: SlateGame[];
}

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

// absent date means "today in ET" since NBA game days are anchored to the eastern calendar; a non-calendar date like 2026-02-31 is rejected rather than clamped
export function parsePredictionDate(raw: unknown): string | null {
  if (raw === undefined || raw === null || raw === '') return etIsoDate(0);
  if (typeof raw !== 'string' || !ISO_DAY.test(raw)) return null;
  const [y, m, d] = raw.split('-').map(Number);
  const asUtc = new Date(Date.UTC(y, m - 1, d));
  if (Number.isNaN(asUtc.getTime())) return null;
  return asUtc.toISOString().slice(0, 10) === raw ? raw : null;
}

export function num(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function round(value: number | null, digits: number): number | null {
  if (value === null) return null;
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

export function toIsoDay(value: unknown): string | null {
  if (value instanceof Date) {
    const y = String(value.getFullYear()).padStart(4, '0');
    const m = String(value.getMonth() + 1).padStart(2, '0');
    const d = String(value.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  }
  if (typeof value === 'string') {
    const match = /^(\d{4}-\d{2}-\d{2})/.exec(value);
    return match ? match[1] : null;
  }
  return null;
}

export function parseSlateSort(raw: unknown): SlateSort | null {
  if (raw === undefined || raw === null || raw === '') return DEFAULT_SLATE_SORT;
  if (typeof raw !== 'string') return null;
  const value = raw.trim().toLowerCase();
  return (SLATE_SORTS as readonly string[]).includes(value) ? (value as SlateSort) : null;
}

function text(value: unknown): string | null {
  return value === null || value === undefined ? null : String(value);
}

export function isMissingRelation(err: unknown): boolean {
  const code = (err as { code?: unknown })?.code;
  return code === '42P01' || code === '42703';
}

export async function rowsOrEmpty<T>(fn: () => Promise<{ rows: unknown[] }>): Promise<T[]> {
  try {
    return (await fn()).rows as T[];
  } catch (err) {
    if (isMissingRelation(err)) return [];
    throw err;
  }
}

interface RunRow {
  id: unknown;
  model_version: unknown;
  predicted_at: unknown;
}

function toIsoTimestamp(value: unknown): string | null {
  if (value instanceof Date) return value.toISOString();
  if (value === null || value === undefined) return null;
  return String(value);
}

export async function getLatestCompleteRun(): Promise<(SlateRun & { id: number }) | null> {
  const rows = await rowsOrEmpty<RunRow>(() =>
    query(
      `SELECT id, model_version, predicted_at
       FROM prediction_runs
       WHERE status = $1
         AND channel = $2
       ORDER BY predicted_at DESC, id DESC
       LIMIT 1`,
      [COMPLETE_RUN_STATUS, PRODUCTION_CHANNEL]
    )
  );

  const row = rows[0];
  if (!row) return null;

  return {
    id: Number(row.id),
    model_version: String(row.model_version ?? ''),
    predicted_at: toIsoTimestamp(row.predicted_at),
  };
}

interface ScheduleRow {
  nba_game_id: unknown;
  season_type: unknown;
  game_status: unknown;
  home_team_id: unknown;
  away_team_id: unknown;
}

async function fetchSchedule(date: string): Promise<ScheduleRow[]> {
  return rowsOrEmpty<ScheduleRow>(() =>
    query(
      `SELECT nba_game_id, season_type, game_status, home_team_id, away_team_id
       FROM nba_schedule
       WHERE game_date = $1
       ORDER BY nba_game_id`,
      [date]
    )
  );
}

async function fetchTeamAbbrs(): Promise<Map<string, string>> {
  const rows = await rowsOrEmpty<{ team_id: unknown; team_abbr: unknown }>(() =>
    query(
      `SELECT DISTINCT team_id, team_abbr
       FROM player_game_logs
       WHERE team_abbr IS NOT NULL`
    )
  );

  const map = new Map<string, string>();
  for (const row of rows) map.set(String(row.team_id), String(row.team_abbr));
  return map;
}

export const RUN_KNOWLEDGE_WINDOW_HOURS = 48;

interface InjuryOverlayRow {
  nba_id: unknown;
  status_raw: unknown;
  detail: unknown;
  current_normalized: unknown;
  current_captured_at: unknown;
  run_normalized: unknown;
}

async function fetchInjuryOverlay(
  playerIds: string[],
  predictedAt: string | null
): Promise<Map<string, InjuryOverlayRow>> {
  const rows = await rowsOrEmpty<InjuryOverlayRow>(() =>
    query(
      `SELECT p.nba_id,
              p.injury_status AS status_raw,
              p.injury_detail AS detail,
              cur.status_normalized AS current_normalized,
              cur.captured_at       AS current_captured_at,
              at_run.status_normalized AS run_normalized
       FROM players p
       LEFT JOIN LATERAL (
         SELECT r.status_normalized, r.captured_at
         FROM player_injury_reports r
         WHERE r.nba_player_id = p.nba_id
         ORDER BY r.captured_at DESC
         LIMIT 1
       ) cur ON true
       LEFT JOIN LATERAL (
         SELECT r.status_normalized
         FROM player_injury_reports r
         WHERE r.nba_player_id = p.nba_id
           AND r.captured_at <= $2::timestamptz
           AND r.captured_at > $2::timestamptz - make_interval(hours => $3::int)
         ORDER BY r.captured_at DESC
         LIMIT 1
       ) at_run ON true
       WHERE p.nba_id = ANY($1)`,
      [playerIds, predictedAt, RUN_KNOWLEDGE_WINDOW_HOURS]
    )
  );

  const map = new Map<string, InjuryOverlayRow>();
  for (const row of rows) map.set(String(row.nba_id), row);
  return map;
}

export type SlatePlayerInjury = Pick<
  SlatePlayer,
  | 'injury_status'
  | 'injury_status_raw'
  | 'injury_detail'
  | 'injury_as_of'
  | 'injury_changed_after_run'
>;

const NO_INJURY: SlatePlayerInjury = {
  injury_status: null,
  injury_status_raw: null,
  injury_detail: null,
  injury_as_of: null,
  injury_changed_after_run: false,
};

export function injuryOverlayFields(
  row: InjuryOverlayRow | undefined,
  runPredictedAt: string | null
): SlatePlayerInjury {
  if (!row) return NO_INJURY;

  const listed = row.status_raw !== null && row.status_raw !== undefined;
  const current = listed ? (text(row.current_normalized) ?? 'unknown') : null;
  const knownAtRun = text(row.run_normalized);
  const changed =
    runPredictedAt !== null && (current ?? 'none') !== (knownAtRun ?? 'none');

  if (!listed && !changed) return NO_INJURY;
  return {
    injury_status: current,
    injury_status_raw: listed ? text(row.status_raw) : null,
    injury_detail: listed ? text(row.detail) : null,
    injury_as_of: listed ? toIsoTimestamp(row.current_captured_at) : null,
    injury_changed_after_run: changed,
  };
}

type PredictionRow = {
  nba_game_id: unknown;
  nba_player_id: unknown;
  name: unknown;
  team_abbr: unknown;
  prob_active: unknown;
  proj_min_p50: unknown;
} & { [K in ProjectedStat]: unknown } & { [K in ProjectedStat as `c_${K}`]: unknown };

const PROJECTED_STAT_PARAM_OFFSET = 6;
const CONDITIONAL_STAT_PARAM_OFFSET = PROJECTED_STAT_PARAM_OFFSET + PROJECTED_STATS.length;
const PLAYER_FILTER_PARAM = CONDITIONAL_STAT_PARAM_OFFSET + PROJECTED_STATS.length;

// the bare stat names are the conditional ("if he plays") twins the headline and vs-usual read
const CONDITIONAL_PIVOT_SQL = PROJECTED_STATS.map(
  (stat, i) =>
    `MAX(CASE WHEN pgp.stat = $${CONDITIONAL_STAT_PARAM_OFFSET + i} AND pgp.quantile IS NULL
                         AND pgp.conditional
                       THEN pgp.value END)::float AS c_${stat}`
).join(',\n              ');

const PROJECTED_PIVOT_SQL = PROJECTED_STATS.map(
  (stat, i) =>
    `MAX(CASE WHEN pgp.stat = $${PROJECTED_STAT_PARAM_OFFSET + i} AND pgp.quantile IS NULL
                       THEN pgp.value END)::float AS ${stat}`
).join(',\n              ');

async function fetchPredictions(
  runId: number,
  date: string,
  nbaPlayerId: string | null = null
): Promise<PredictionRow[]> {
  const playerFilter =
    nbaPlayerId === null ? '' : `\n         AND pgp.nba_player_id = $${PLAYER_FILTER_PARAM}`;
  return rowsOrEmpty<PredictionRow>(() =>
    query(
      `SELECT pgp.nba_game_id,
              pgp.nba_player_id,
              MAX(p.name) AS name,
              MAX(p.team) AS team_abbr,
              MAX(CASE WHEN pgp.stat = $3 AND pgp.quantile IS NULL
                       THEN pgp.value END)::float AS prob_active,
              MAX(CASE WHEN pgp.stat = $4 AND pgp.quantile = $5
                       THEN pgp.value END)::float AS proj_min_p50,
              ${PROJECTED_PIVOT_SQL},
              ${CONDITIONAL_PIVOT_SQL}
       FROM player_game_predictions pgp
       LEFT JOIN players p ON p.nba_id = pgp.nba_player_id
       WHERE pgp.prediction_run_id = $1
         AND pgp.game_date = $2${playerFilter}
       GROUP BY pgp.nba_game_id, pgp.nba_player_id`,
      [
        runId,
        date,
        PROB_ACTIVE_STAT,
        MINUTES_STAT,
        MINUTES_QUANTILE,
        ...PROJECTED_STATS.map(uncondStat),
        ...PROJECTED_STATS,
        ...(nbaPlayerId === null ? [] : [nbaPlayerId]),
      ]
    )
  );
}

export type ImpactInput = Record<ProjectedStat, number | null>;

export function resolvePlayerName(
  raw: unknown,
  nbaPlayerId: string
): { name: string; placeholder: boolean } {
  const trimmed = raw === null || raw === undefined ? '' : String(raw).trim();
  if (trimmed !== '') return { name: trimmed, placeholder: false };
  return { name: `NBA #${nbaPlayerId} ${PLACEHOLDER_NAME_SUFFIX}`, placeholder: true };
}

function poolTotal(pool: ImpactInput[], stat: ProjectedStat): number {
  let total = 0;
  for (const entry of pool) total += entry[stat] ?? 0;
  return total;
}

export function poolRates(pool: ImpactInput[]): { fg: number; ft: number } {
  const fga = poolTotal(pool, 'fga');
  const fta = poolTotal(pool, 'fta');
  return {
    fg: fga === 0 ? 0 : poolTotal(pool, 'fgm') / fga,
    ft: fta === 0 ? 0 : poolTotal(pool, 'ftm') / fta,
  };
}

export function categoryValues(
  entry: ImpactInput,
  rates: { fg: number; ft: number }
): Record<ImpactCategory, number | null> {
  const excess = (made: number | null, attempted: number | null, rate: number): number | null =>
    made === null || attempted === null ? null : made - attempted * rate;

  return {
    pts: entry.pts,
    reb: entry.reb,
    ast: entry.ast,
    stl: entry.stl,
    blk: entry.blk,
    fg3m: entry.fg3m,
    fg: excess(entry.fgm, entry.fga, rates.fg),
    ft: excess(entry.ftm, entry.fta, rates.ft),
    tov: entry.tov,
  };
}

export function impactScores(pool: ImpactInput[]): Array<number | null> {
  if (pool.length === 0) return [];

  const rates = poolRates(pool);
  const values = pool.map((entry) => categoryValues(entry, rates));

  const inPlay: ImpactCategory[] = [];
  const shape = new Map<ImpactCategory, { m: number; sd: number }>();
  for (const cat of IMPACT_CATEGORIES) {
    const present = values
      .map((v) => v[cat])
      .filter((v): v is number => v !== null && Number.isFinite(v));
    if (present.length === 0) continue;
    inPlay.push(cat);
    shape.set(cat, { m: mean(present), sd: stddev(present) });
  }

  if (inPlay.length === 0) return pool.map(() => null);

  return values.map((v) => {
    let total = 0;
    for (const cat of inPlay) {
      const value = v[cat];
      if (value === null || !Number.isFinite(value)) return null;
      const { m, sd } = shape.get(cat)!;
      const z = sd === 0 ? 0 : (value - m) / sd;
      total += REVERSED_IMPACT_CATEGORIES.has(cat) ? -z : z;
    }
    return round(total, 2);
  });
}

function byValueDesc(a: number | null, b: number | null): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return b - a;
}

export function rankSlatePlayers(
  players: SlatePlayer[],
  limit: number = TOP_PLAYERS_PER_GAME
): SlatePlayer[] {
  return [...players]
    .sort((a, b) => {
      const byImpact = byValueDesc(a.impact, b.impact);
      if (byImpact !== 0) return byImpact;
      const byPoints = byValueDesc(a.proj_pts, b.proj_pts);
      if (byPoints !== 0) return byPoints;
      if (a.name_is_placeholder !== b.name_is_placeholder) return a.name_is_placeholder ? 1 : -1;
      return a.name.localeCompare(b.name) || a.nba_player_id.localeCompare(b.nba_player_id);
    })
    .slice(0, limit);
}

export function topImpactIds(players: SlatePlayer[], count: number): Set<string> {
  return new Set(
    [...players]
      .filter((p) => p.impact !== null)
      .sort(
        (a, b) =>
          byValueDesc(a.impact, b.impact) ||
          a.name.localeCompare(b.name) ||
          a.nba_player_id.localeCompare(b.nba_player_id)
      )
      .slice(0, count)
      .map((p) => p.nba_player_id)
  );
}

export function poolDescriptor(sampleSize: number): SlatePool {
  return {
    key: IMPACT_POOL_KEY,
    label: IMPACT_POOL_LABEL,
    definition: IMPACT_POOL_DEFINITION,
    sample_size: sampleSize,
  };
}

function roundedVsUsual(value: VsUsual): VsUsual {
  return {
    usual: round(value.usual, 1),
    projected: round(value.projected, 1),
    delta: round(value.delta, 1),
  };
}

export function playerVsUsualOf(input: ReasonInput): PlayerVsUsual {
  return { minutes: roundedVsUsual(input.minutes), points: roundedVsUsual(input.points) };
}

// ranked in units of the slate's spread so a block and a rebound compare fairly
export function topCategoryDeltas(
  conditional: ConditionalLine,
  usual: PlayerBaseline['avg'],
  scales: Map<DeviationStat, number>,
  count: number = VS_USUAL_CATEGORIES_SHOWN
): CategoryVsUsual[] {
  const ranked: Array<{ category: CategoryVsUsual; weight: number }> = [];
  for (const stat of VS_USUAL_CATEGORIES) {
    const projected = conditional[stat];
    const base = usual[stat];
    const delta = deltaOf(projected, base);
    if (delta === null || projected === null || base === null) continue;
    const rounded = round(delta, 1) as number;
    if (rounded === 0) continue;
    const sd = scales.get(stat) ?? 0;
    ranked.push({
      category: {
        stat,
        usual: round(base, 1) as number,
        projected: round(projected, 1) as number,
        delta: rounded,
      },
      weight: sd > 0 ? Math.abs(delta) / sd : Math.abs(delta),
    });
  }
  return ranked
    .sort((a, b) => b.weight - a.weight)
    .slice(0, count)
    .map((entry) => entry.category);
}

// both directions count: a projected minutes cut is as much an edge as a bump
export function edgeOf(
  deltas: ReasonInput['deltas'],
  scales: Map<DeviationStat, number>
): number | null {
  const { upside } = upsideOf(deltas, scales);
  return upside === null ? null : round(Math.abs(upside), 3);
}

function byIdentity(a: SlatePlayer, b: SlatePlayer): number {
  if (a.name_is_placeholder !== b.name_is_placeholder) return a.name_is_placeholder ? 1 : -1;
  return a.name.localeCompare(b.name) || a.nba_player_id.localeCompare(b.nba_player_id);
}

export function rankSlatePlayersByEdge(
  players: SlatePlayer[],
  limit: number = TOP_PLAYERS_PER_GAME
): SlatePlayer[] {
  return [...players]
    .sort(
      (a, b) => byValueDesc(a.edge, b.edge) || byValueDesc(a.impact, b.impact) || byIdentity(a, b)
    )
    .slice(0, limit);
}

export function sortSlateGames(games: SlateGame[], sort: SlateSort): SlateGame[] {
  const key = (game: SlateGame): number | null =>
    sort === 'edge' ? game.top_edge : game.top_impact;
  return [...games].sort(
    (a, b) => byValueDesc(key(a), key(b)) || a.nba_game_id.localeCompare(b.nba_game_id)
  );
}

function conditionalLineOf(row: PredictionRow): ConditionalLine {
  const line = {} as ConditionalLine;
  for (const stat of CONDITIONAL_STATS) line[stat] = num(row[`c_${stat}`]);
  return line;
}

function maxOf(values: Array<number | null>): number | null {
  const present = values.filter((v): v is number => v !== null);
  return present.length === 0 ? null : Math.max(...present);
}

export async function getSlate(
  date: string,
  sort: SlateSort = DEFAULT_SLATE_SORT
): Promise<SlateResponse> {
  const schedule = await fetchSchedule(date);
  const run = await getLatestCompleteRun();
  const runSummary = run ? { model_version: run.model_version, predicted_at: run.predicted_at } : null;
  const baseline = baselineDescriptor();

  if (schedule.length === 0) {
    return { date, sort, run: runSummary, pool: poolDescriptor(0), baseline, games: [] };
  }

  const teamAbbrs = await fetchTeamAbbrs();
  const predictions = run ? await fetchPredictions(run.id, date) : [];
  const baselines =
    predictions.length > 0 ? await fetchBaselines(date) : new Map<string, PlayerBaseline>();
  const injuries =
    predictions.length > 0
      ? await fetchInjuryOverlay(
          [...new Set(predictions.map((row) => String(row.nba_player_id)))],
          runSummary?.predicted_at ?? null
        )
      : new Map<string, InjuryOverlayRow>();

  const inputs: ImpactInput[] = predictions.map((row) => {
    const entry = {} as ImpactInput;
    for (const stat of PROJECTED_STATS) entry[stat] = num(row[stat]);
    return entry;
  });
  const impacts = impactScores(inputs);

  const groups = groupTeammates(
    predictions.map((row) => {
      const id = String(row.nba_player_id);
      return {
        id,
        game_id: String(row.nba_game_id),
        team_abbr: text(row.team_abbr),
        name: resolvePlayerName(row.name, id).name,
        usual_minutes: baselines.get(id)?.avg.minutes ?? null,
        prob_active: num(row.prob_active),
      };
    })
  );

  const conditionals = predictions.map(conditionalLineOf);
  const reasonInputs: Array<ReasonInput | null> = predictions.map((row, i) => {
    const id = String(row.nba_player_id);
    const own = baselines.get(id);
    if (!hasUsableBaseline(own)) return null;
    return reasonInputFor({
      gameDate: date,
      probActive: num(row.prob_active),
      minutes: num(row.proj_min_p50),
      conditional: conditionals[i],
      baseline: own as PlayerBaseline,
      teammates: teammatesOf(
        { id, game_id: String(row.nba_game_id), team_abbr: text(row.team_abbr) },
        groups
      ),
    });
  });
  const scales = deviationScales(
    reasonInputs.filter((r): r is ReasonInput => r !== null).map((r) => r.deltas)
  );

  const players: SlatePlayer[] = predictions.map((row, i) => {
    const nbaPlayerId = String(row.nba_player_id);
    const { name, placeholder } = resolvePlayerName(row.name, nbaPlayerId);
    const projected = {} as SlateProjectedCategories;
    for (const cat of DISPLAY_CATEGORIES) projected[cat] = round(num(row[`c_${cat}`]), 1);

    const own = baselines.get(nbaPlayerId);
    const usable = hasUsableBaseline(own) ? (own as PlayerBaseline) : null;
    const projMin = num(row.proj_min_p50);
    const projPts = inputs[i].pts;
    const reasonInput = reasonInputs[i];
    const reasons = reasonInput ? reasonsFor(reasonInput) : [];

    return {
      nba_player_id: nbaPlayerId,
      name,
      name_is_placeholder: placeholder,
      team_abbr: text(row.team_abbr),
      prob_active: round(num(row.prob_active), 3),
      proj_pts: round(projPts, 1),
      proj_pts_cond: round(num(row.c_pts), 1),
      proj_min_p50: round(projMin, 1),
      projected,
      usual_min: round(usable?.avg.minutes ?? null, 1),
      usual_pts: round(usable?.avg.pts ?? null, 1),
      min_vs_usual: round(deltaOf(projMin, usable?.avg.minutes ?? null), 1),
      pts_vs_usual: round(deltaOf(projPts, usable?.avg.pts ?? null), 1),
      baseline_games: usable?.games ?? 0,
      impact: impacts[i],
      edge: reasonInput ? edgeOf(reasonInput.deltas, scales) : null,
      vs_usual:
        reasonInput && usable
          ? {
              ...playerVsUsualOf(reasonInput),
              categories: topCategoryDeltas(conditionals[i], usable.avg, scales),
            }
          : null,
      reasons,
      evidence: reasonInput ? evidenceFor(reasonInput, reasons) : {},
      spotlight: false,
      slate_spotlight: false,
      ...injuryOverlayFields(injuries.get(nbaPlayerId), runSummary?.predicted_at ?? null),
    };
  });

  const slateSpotlight = topImpactIds(players, SLATE_SPOTLIGHT_COUNT);
  for (const player of players) {
    player.slate_spotlight = slateSpotlight.has(player.nba_player_id);
  }

  const byGame = new Map<string, SlatePlayer[]>();
  for (let i = 0; i < players.length; i += 1) {
    const gameId = String(predictions[i].nba_game_id);
    const list = byGame.get(gameId) ?? [];
    list.push(players[i]);
    byGame.set(gameId, list);
  }

  const games: SlateGame[] = schedule.map((row) => {
    const homeId = text(row.home_team_id);
    const awayId = text(row.away_team_id);
    const gameId = String(row.nba_game_id);
    const inGame = byGame.get(gameId) ?? [];

    const gameSpotlight = topImpactIds(inGame, SPOTLIGHT_PER_GAME);
    for (const player of inGame) player.spotlight = gameSpotlight.has(player.nba_player_id);

    const ranked = sort === 'edge' ? rankSlatePlayersByEdge(inGame) : rankSlatePlayers(inGame);

    return {
      nba_game_id: gameId,
      game_status: text(row.game_status),
      home_team_id: homeId,
      home_team_abbr: homeId ? teamAbbrs.get(homeId) ?? null : null,
      away_team_id: awayId,
      away_team_abbr: awayId ? teamAbbrs.get(awayId) ?? null : null,
      preseason: isPreseason(row.season_type),
      top_impact: maxOf(ranked.map((p) => p.impact)),
      top_edge: maxOf(ranked.map((p) => p.edge)),
      players: ranked,
    };
  });

  return {
    date,
    sort,
    run: runSummary,
    pool: poolDescriptor(players.length),
    baseline,
    games: sortSlateGames(games, sort),
  };
}

// the same minutes and points comparison his slate row carries, without building the whole slate
export async function getPlayerVsUsual(
  nbaPlayerId: string,
  date: string
): Promise<PlayerVsUsual | null> {
  const run = await getLatestCompleteRun();
  if (!run) return null;
  const [row] = await fetchPredictions(run.id, date, nbaPlayerId);
  if (!row) return null;
  const own = (await fetchBaselines(date, [nbaPlayerId])).get(nbaPlayerId);
  if (!hasUsableBaseline(own)) return null;

  return playerVsUsualOf(
    reasonInputFor({
      gameDate: date,
      probActive: num(row.prob_active),
      minutes: num(row.proj_min_p50),
      conditional: conditionalLineOf(row),
      baseline: own as PlayerBaseline,
      teammates: [],
    })
  );
}
