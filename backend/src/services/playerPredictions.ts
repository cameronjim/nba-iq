import { query } from '../db.js';
import { COMPLETE_RUN_STATUS, PRODUCTION_CHANNEL, rowsOrEmpty } from './slate.js';

// the stat vocabulary is not hardcoded here on purpose, since the emitted stat list grows over time; this pivots by whatever `stat` values come back
const UNCOND_SUFFIX = '_uncond';

const PROB_ACTIVE = 'prob_active';
const PROB_ACTIVE_MODEL = 'prob_active_model';

const AVAILABILITY_STATS = new Set<string>([PROB_ACTIVE, PROB_ACTIVE_MODEL]);

const STAT_ORDER = [
  'minutes',
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

export const DEFAULT_UPCOMING_LIMIT = 14;
export const MAX_UPCOMING_LIMIT = 60;

const P10 = 0.1;
const P50 = 0.5;
const P90 = 0.9;

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

export interface PredictionStatLine {
  expected: number | null;
  p10: number | null;
  p50: number | null;
  p90: number | null;
  unconditional: number | null;
}

export interface UpcomingGamePrediction {
  nba_game_id: string;
  game_date: string;
  opponent_abbr: string | null;
  is_home: boolean | null;
  game_status: string | null;
  prob_active: number | null;
  prob_active_model: number | null;
  stats: Record<string, PredictionStatLine>;
}

export interface PredictionRunMeta {
  id: number;
  model_version: string;
  feature_version: string | null;
  predicted_at: string | null;
  forecast_cutoff_at: string | null;
  horizon: string | null;
}

export interface PlayerPredictionsResponse {
  player_id: number;
  nba_player_id: string | null;
  run: PredictionRunMeta | null;
  stats: string[];
  games: UpcomingGamePrediction[];
}

export interface UpcomingOptions {
  teamAbbr?: string | null;
  from?: string | null;
  limit?: number;
}

export interface UpcomingPredictionRow {
  nba_game_id: unknown;
  game_date: unknown;
  home_team_abbr: unknown;
  away_team_abbr: unknown;
  game_status: unknown;
  stat: unknown;
  quantile: unknown;
  value: unknown;
  conditional: unknown;
}

export function parseFromDate(raw: unknown): string | null | false {
  if (raw === undefined || raw === null || raw === '') return null;
  if (typeof raw !== 'string' || !ISO_DAY.test(raw)) return false;
  const [y, m, d] = raw.split('-').map(Number);
  const asUtc = new Date(Date.UTC(y, m - 1, d));
  if (Number.isNaN(asUtc.getTime())) return false;
  return asUtc.toISOString().slice(0, 10) === raw ? raw : false;
}

export function parseLimit(raw: unknown): number | false {
  if (raw === undefined || raw === null || raw === '') return DEFAULT_UPCOMING_LIMIT;
  if (typeof raw !== 'string' && typeof raw !== 'number') return false;
  const parsed = Number(raw);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > MAX_UPCOMING_LIMIT) return false;
  return parsed;
}

function num(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function round(value: number | null, digits: number): number | null {
  if (value === null) return null;
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function text(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const str = String(value);
  return str === '' ? null : str;
}

function toIsoDay(value: unknown): string | null {
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

function toIsoInstant(value: unknown): string | null {
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value.toISOString();
  if (value === null || value === undefined) return null;
  const parsed = new Date(String(value));
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

export function horizonFromNotes(notes: unknown): string | null {
  if (typeof notes !== 'string') return null;
  const match = /horizon\s*=\s*([^;\n]+)/i.exec(notes);
  return match ? match[1].trim() || null : null;
}

export function orderStatKeys(keys: Iterable<string>): string[] {
  const known: string[] = [];
  const unknown: string[] = [];
  const seen = new Set(keys);
  for (const key of STAT_ORDER) if (seen.has(key)) known.push(key);
  for (const key of seen) if (!STAT_ORDER.includes(key as (typeof STAT_ORDER)[number])) unknown.push(key);
  unknown.sort();
  return [...known, ...unknown];
}

function emptyLine(): PredictionStatLine {
  return { expected: null, p10: null, p50: null, p90: null, unconditional: null };
}

export function pivotUpcomingRows(
  rows: UpcomingPredictionRow[],
  playerTeamAbbr: string | null
): UpcomingGamePrediction[] {
  const byGame = new Map<string, UpcomingGamePrediction>();
  const quantiles = new Map<string, Map<string, Map<number, number>>>();

  for (const row of rows) {
    const gameId = text(row.nba_game_id);
    const gameDate = toIsoDay(row.game_date);
    if (!gameId || !gameDate) continue;

    let game = byGame.get(gameId);
    if (!game) {
      const home = text(row.home_team_abbr);
      const away = text(row.away_team_abbr);
      const isHome = playerTeamAbbr && home === playerTeamAbbr
        ? true
        : playerTeamAbbr && away === playerTeamAbbr
          ? false
          : null;
      game = {
        nba_game_id: gameId,
        game_date: gameDate,
        opponent_abbr: isHome === null ? null : isHome ? away : home,
        is_home: isHome,
        game_status: text(row.game_status),
        prob_active: null,
        prob_active_model: null,
        stats: {},
      };
      byGame.set(gameId, game);
      quantiles.set(gameId, new Map());
    }

    const stat = text(row.stat);
    const value = num(row.value);
    if (!stat || value === null) continue;
    const quantile = num(row.quantile);

    if (quantile === null && AVAILABILITY_STATS.has(stat)) {
      const clamped = Math.min(Math.max(value, 0), 1);
      if (stat === PROB_ACTIVE) game.prob_active = round(clamped, 4);
      else game.prob_active_model = round(clamped, 4);
      continue;
    }

    const isUncond = stat.endsWith(UNCOND_SUFFIX);
    const base = isUncond ? stat.slice(0, -UNCOND_SUFFIX.length) : stat;
    if (!base) continue;

    const line = game.stats[base] ?? emptyLine();
    game.stats[base] = line;

    if (quantile === null) {
      if (isUncond) line.unconditional = round(value, 2);
      else line.expected = round(value, 2);
      continue;
    }

    if (isUncond) continue;
    const forGame = quantiles.get(gameId)!;
    const forStat = forGame.get(base) ?? new Map<number, number>();
    forStat.set(quantile, value);
    forGame.set(base, forStat);
  }

  for (const [gameId, game] of byGame) {
    for (const [stat, byQuantile] of quantiles.get(gameId) ?? []) {
      const line = game.stats[stat] ?? emptyLine();
      game.stats[stat] = line;
      const p10 = byQuantile.get(P10);
      const p50 = byQuantile.get(P50);
      const p90 = byQuantile.get(P90);
      line.p10 = p10 === undefined ? null : round(p10, 2);
      line.p50 = p50 === undefined ? null : round(p50, 2);
      line.p90 = p90 === undefined ? null : round(p90, 2);
    }
  }

  return [...byGame.values()].sort(
    (a, b) => a.game_date.localeCompare(b.game_date) || a.nba_game_id.localeCompare(b.nba_game_id)
  );
}

export function collectStatKeys(games: UpcomingGamePrediction[]): string[] {
  const keys = new Set<string>();
  for (const game of games) for (const key of Object.keys(game.stats)) keys.add(key);
  return orderStatKeys(keys);
}

interface RunRow {
  id: unknown;
  model_version: unknown;
  feature_version: unknown;
  predicted_at: unknown;
  forecast_cutoff_at: unknown;
  notes: unknown;
}

export async function getLatestRunMeta(): Promise<PredictionRunMeta | null> {
  const rows = await rowsOrEmpty<RunRow>(() =>
    query(
      `SELECT id, model_version, feature_version, predicted_at, forecast_cutoff_at, notes
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
    feature_version: text(row.feature_version),
    predicted_at: toIsoInstant(row.predicted_at),
    forecast_cutoff_at: toIsoInstant(row.forecast_cutoff_at),
    horizon: horizonFromNotes(row.notes),
  };
}

const UPCOMING_SQL = `
  WITH games AS (
    SELECT nba_game_id, MIN(game_date) AS game_date
    FROM player_game_predictions
    WHERE prediction_run_id = $1
      AND nba_player_id = $2
      AND ($3::date IS NULL OR game_date >= $3::date)
    GROUP BY nba_game_id
    ORDER BY MIN(game_date) ASC, nba_game_id ASC
    LIMIT $4
  )
  SELECT g.nba_game_id,
         g.game_date,
         s.home_team_abbr,
         s.away_team_abbr,
         s.game_status,
         p.stat,
         p.quantile::float AS quantile,
         p.value::float    AS value,
         p.conditional
  FROM games g
  JOIN player_game_predictions p
    ON p.prediction_run_id = $1
   AND p.nba_player_id = $2
   AND p.nba_game_id = g.nba_game_id
  LEFT JOIN nba_schedule s ON s.nba_game_id = g.nba_game_id
  ORDER BY g.game_date ASC, g.nba_game_id ASC, p.stat ASC
`;

interface CacheEntry {
  data: Omit<PlayerPredictionsResponse, 'player_id' | 'nba_player_id'>;
  fetchedAt: number;
}

const TTL_MS = 5 * 60 * 1000;

const cache = new Map<string, CacheEntry>();

export function clearUpcomingPredictionsCache(): void {
  cache.clear();
}

export async function getUpcomingPredictionsForPlayer(
  nbaPlayerId: string | null,
  options: UpcomingOptions = {}
): Promise<Omit<PlayerPredictionsResponse, 'player_id' | 'nba_player_id'>> {
  const from = options.from ?? null;
  const limit = options.limit ?? DEFAULT_UPCOMING_LIMIT;
  if (!nbaPlayerId) return { run: null, stats: [], games: [] };

  const cacheKey = `${nbaPlayerId}|${from ?? ''}|${limit}`;
  const cached = cache.get(cacheKey);
  if (cached && Date.now() - cached.fetchedAt < TTL_MS) return cached.data;

  const run = await getLatestRunMeta();
  if (!run) {
    const empty = { run: null, stats: [], games: [] };
    cache.set(cacheKey, { data: empty, fetchedAt: Date.now() });
    return empty;
  }

  const rows = await rowsOrEmpty<UpcomingPredictionRow>(() =>
    query(UPCOMING_SQL, [run.id, nbaPlayerId, from, limit])
  );

  const games = pivotUpcomingRows(rows, options.teamAbbr ?? null);
  const data = { run, stats: collectStatKeys(games), games };
  cache.set(cacheKey, { data, fetchedAt: Date.now() });
  return data;
}
