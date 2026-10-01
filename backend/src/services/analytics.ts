import { query } from '../db.js';
import { getLatestPredictionForPlayer, type PlayerPrediction } from './predictions.js';
import { getPlayerVsUsual, type PlayerVsUsual } from './slate.js';


export const ANALYTICS_STATS = [
  'pts',
  'reb',
  'ast',
  'stl',
  'blk',
  'fg3m',
  'tov',
  'fg_impact',
  'ft_impact',
  'minutes',
] as const;

export type AnalyticsStat = (typeof ANALYTICS_STATS)[number];

const REVERSED_STATS: ReadonlySet<string> = new Set<AnalyticsStat>(['tov']);

export const TREND_STATS = [
  'pts',
  'reb',
  'ast',
  'stl',
  'blk',
  'fg3m',
  'tov',
  'minutes',
] as const;

export type TrendStat = (typeof TREND_STATS)[number];

export const POOL_KEY = 'rotation';
export const POOL_LABEL = 'Rotation players';
export const POOL_MIN_GAMES = 15;
export const POOL_MIN_MINUTES = 12;

export const POOL_DEFINITION = 'GP >= 15 and MPG >= 12 this season';

export const BUCKET_COUNT = 20;

export const TREND_GAME_COUNT = 20;

export const MIN_GAMES_FOR_Z = 15;

const TTL_MS = 60 * 60 * 1000; // 1 hour, matching benchmarks.ts

export type StatValues = Record<AnalyticsStat, number>;
export type TrendValues = Record<TrendStat, number>;

export interface AnalyticsPool {
  key: string;
  label: string;
  definition: string;
  sample_size: number;
}

export interface StatPercentile {
  stat: AnalyticsStat;
  value: number;
  percentile: number;
}

export interface DistributionBucket {
  lo: number;
  hi: number;
  count: number;
}

export interface StatDistribution {
  stat: AnalyticsStat;
  mean: number;
  stddev: number;
  buckets: DistributionBucket[];
  player_value: number;
}

export interface Last10Comparison {
  stat: TrendStat;
  last10: number;
  season: number;
  delta: number;
  z: number | null;
}

export interface TrendGame extends TrendValues {
  game_date: string | null;
  opponent_team_abbr: string | null;
  is_home: boolean | null;
  fga: number;
  fg3a: number;
  fgm: number;
  ftm: number;
  fta: number;
}

export interface RollingPoint {
  game_date: string | null;
  [rollingKey: string]: number | string | null;
}

export interface PlayerAnalytics {
  player: {
    id: number;
    nba_id: string | null;
    name: string;
    team: string | null;
    position: string | null;
    headshot_url: string | null;
    injury_status: string | null;
    injury_detail: string | null;
  };
  as_of: { logs: string | null; distributions: string };
  pool: AnalyticsPool;
  percentiles: StatPercentile[];
  distributions: StatDistribution[];
  trends: {
    games: TrendGame[];
    rolling: RollingPoint[];
    last10_vs_season: Last10Comparison[];
  };
  prediction: AnalyticsPrediction | null;
}

// vs_usual is optional so a missing baseline or an older server leaves the card unchanged
export interface AnalyticsPrediction extends PlayerPrediction {
  vs_usual?: PlayerVsUsual;
}

export interface LeagueDistribution {
  stat: AnalyticsStat;
  pool: AnalyticsPool;
  mean: number;
  stddev: number;
  buckets: DistributionBucket[];
  players: Array<{ id: number; name: string; value: number; percentile: number }>;
}

function round(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

export function mean(values: number[]): number {
  if (values.length === 0) return 0;
  let total = 0;
  for (const v of values) total += v;
  return total / values.length;
}

export function stddev(values: number[]): number {
  if (values.length === 0) return 0;
  const m = mean(values);
  let sum = 0;
  for (const v of values) sum += (v - m) ** 2;
  return Math.sqrt(sum / values.length);
}

export function percentRank(values: number[], value: number, reversed = false): number {
  if (values.length === 0) return 50;

  let below = 0;
  let equal = 0;
  for (const v of values) {
    if (v < value) below += 1;
    else if (v === value) equal += 1;
  }

  const pct = ((below + equal / 2) / values.length) * 100;
  return round(reversed ? 100 - pct : pct, 1);
}

export function buildBuckets(values: number[], bucketCount: number = BUCKET_COUNT): DistributionBucket[] {
  if (values.length === 0 || bucketCount < 1) return [];

  let lo = values[0];
  let hi = values[0];
  for (const v of values) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (hi === lo) return [{ lo: round(lo, 3), hi: round(hi, 3), count: values.length }];

  const width = (hi - lo) / bucketCount;
  const counts = new Array<number>(bucketCount).fill(0);
  for (const v of values) {
    const index = Math.min(bucketCount - 1, Math.floor((v - lo) / width));
    counts[index] += 1;
  }

  return counts.map((count, i) => ({
    lo: round(lo + i * width, 3),
    hi: round(lo + (i + 1) * width, 3),
    count,
  }));
}

export function rollingMean(values: number[], window: number): Array<number | null> {
  if (window < 1) return values.map(() => null);

  const out: Array<number | null> = [];
  let sum = 0;
  for (let i = 0; i < values.length; i += 1) {
    sum += values[i];
    if (i >= window) sum -= values[i - window];
    out.push(i + 1 < window ? null : round(sum / window, 2));
  }
  return out;
}

export function attemptWeightedImpact(
  made: number,
  attempted: number,
  poolPct: number,
  games: number
): number {
  if (games <= 0) return 0;
  return round((made - attempted * poolPct) / games, 3);
}

export function last10VsSeason(
  games: TrendValues[],
  minGamesForZ: number = MIN_GAMES_FOR_Z
): Last10Comparison[] {
  if (games.length === 0) return [];

  const recent = games.slice(-10);

  return TREND_STATS.map((stat) => {
    const all = games.map((g) => g[stat]);
    const last10 = mean(recent.map((g) => g[stat]));
    const season = mean(all);
    const sd = stddev(all);
    const delta = last10 - season;
    const z = games.length < minGamesForZ || sd === 0 ? null : round(delta / sd, 2);

    return {
      stat,
      last10: round(last10, 2),
      season: round(season, 2),
      delta: round(delta, 2),
      z,
    };
  });
}

export interface PoolPlayer {
  id: number;
  name: string;
  values: StatValues;
}

export interface PoolSnapshot {
  fetchedAt: number;
  players: PoolPlayer[];
  fgPct: number;
  ftPct: number;
  teamAbbrById: Map<string, string>;
}

export function poolDescriptor(sampleSize: number): AnalyticsPool {
  return {
    key: POOL_KEY,
    label: POOL_LABEL,
    definition: POOL_DEFINITION,
    sample_size: sampleSize,
  };
}

function poolValues(snapshot: PoolSnapshot, stat: AnalyticsStat): number[] {
  return snapshot.players.map((p) => p.values[stat]);
}

export function buildPercentiles(snapshot: PoolSnapshot, values: StatValues): StatPercentile[] {
  return ANALYTICS_STATS.map((stat) => ({
    stat,
    value: round(values[stat], 3),
    percentile: percentRank(poolValues(snapshot, stat), values[stat], REVERSED_STATS.has(stat)),
  }));
}

export function buildDistributions(snapshot: PoolSnapshot, values: StatValues): StatDistribution[] {
  return ANALYTICS_STATS.map((stat) => {
    const pool = poolValues(snapshot, stat);
    return {
      stat,
      mean: round(mean(pool), 3),
      stddev: round(stddev(pool), 3),
      buckets: buildBuckets(pool),
      player_value: round(values[stat], 3),
    };
  });
}

export function parseAnalyticsStat(raw: unknown): AnalyticsStat | null {
  if (typeof raw === 'string' && (ANALYTICS_STATS as readonly string[]).includes(raw)) {
    return raw as AnalyticsStat;
  }
  return null;
}

export function isValidPoolKey(raw: unknown): boolean {
  return raw === undefined || raw === null || raw === '' || raw === POOL_KEY;
}

export function parsePlayerId(raw: unknown): number | null {
  const parsed = Number.parseInt(String(raw ?? ''), 10);
  if (!Number.isFinite(parsed) || parsed < 1) return null;
  return parsed;
}

function num(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
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

let cache: PoolSnapshot | null = null;

export function clearAnalyticsCache(): void {
  cache = null;
}

const CURRENT_SEASON = '(SELECT MAX(season) FROM player_game_logs)';

export async function getPoolSnapshot(): Promise<PoolSnapshot> {
  if (cache && Date.now() - cache.fetchedAt < TTL_MS) return cache;

  const [seasonResult, totalsResult, teamsResult] = await Promise.all([
    query(
      `SELECT id,
              nba_id,
              name,
              points_per_game::float     AS pts,
              rebounds_per_game::float   AS reb,
              assists_per_game::float    AS ast,
              steals_per_game::float     AS stl,
              blocks_per_game::float     AS blk,
              three_pointers_made::float AS fg3m,
              turnovers_per_game::float  AS tov,
              minutes_per_game::float    AS minutes
       FROM players
       WHERE games_played >= $1
         AND minutes_per_game >= $2
       ORDER BY id`,
      [POOL_MIN_GAMES, POOL_MIN_MINUTES]
    ),
    query(
      `SELECT g.nba_player_id,
              SUM(g.fgm)::float AS fgm,
              SUM(g.fga)::float AS fga,
              SUM(g.ftm)::float AS ftm,
              SUM(g.fta)::float AS fta,
              COUNT(*)::int     AS games
       FROM player_game_logs g
       JOIN players p ON p.nba_id = g.nba_player_id
       WHERE p.games_played >= $1
         AND p.minutes_per_game >= $2
         AND g.season = ${CURRENT_SEASON}
         AND g.season_type = 'Regular Season'
       GROUP BY g.nba_player_id`,
      [POOL_MIN_GAMES, POOL_MIN_MINUTES]
    ),
    query(
      `SELECT DISTINCT team_id, team_abbr
       FROM player_game_logs
       WHERE team_abbr IS NOT NULL`
    ),
  ]);

  interface Totals {
    fgm: number;
    fga: number;
    ftm: number;
    fta: number;
    games: number;
  }

  const totalsByNbaId = new Map<string, Totals>();
  let poolFgm = 0;
  let poolFga = 0;
  let poolFtm = 0;
  let poolFta = 0;

  for (const row of totalsResult.rows) {
    const totals: Totals = {
      fgm: num(row.fgm),
      fga: num(row.fga),
      ftm: num(row.ftm),
      fta: num(row.fta),
      games: num(row.games),
    };
    totalsByNbaId.set(String(row.nba_player_id), totals);
    poolFgm += totals.fgm;
    poolFga += totals.fga;
    poolFtm += totals.ftm;
    poolFta += totals.fta;
  }

  const fgPct = poolFga > 0 ? poolFgm / poolFga : 0;
  const ftPct = poolFta > 0 ? poolFtm / poolFta : 0;

  const players: PoolPlayer[] = seasonResult.rows.map((row) => {
    const totals = totalsByNbaId.get(String(row.nba_id));
    return {
      id: num(row.id),
      name: String(row.name ?? ''),
      values: {
        pts: num(row.pts),
        reb: num(row.reb),
        ast: num(row.ast),
        stl: num(row.stl),
        blk: num(row.blk),
        fg3m: num(row.fg3m),
        tov: num(row.tov),
        minutes: num(row.minutes),
        fg_impact: totals
          ? attemptWeightedImpact(totals.fgm, totals.fga, fgPct, totals.games)
          : 0,
        ft_impact: totals
          ? attemptWeightedImpact(totals.ftm, totals.fta, ftPct, totals.games)
          : 0,
      },
    };
  });

  const teamAbbrById = new Map<string, string>();
  for (const row of teamsResult.rows) {
    teamAbbrById.set(String(row.team_id), String(row.team_abbr));
  }

  cache = { fetchedAt: Date.now(), players, fgPct, ftPct, teamAbbrById };
  return cache;
}

interface GameLogRow extends TrendValues {
  game_date: string | null;
  opponent_team_id: string | null;
  is_home: boolean | null;
  fgm: number;
  fga: number;
  fg3a: number;
  ftm: number;
  fta: number;
}

async function fetchPlayerLogs(nbaPlayerId: string | null): Promise<GameLogRow[]> {
  const result = await query(
    `SELECT game_date,
            opponent_team_id,
            is_home,
            minutes::float AS minutes,
            pts, reb, ast, stl, blk, tov,
            fgm, fga, fg3m, fg3a, ftm, fta
     FROM player_game_logs
     WHERE nba_player_id = $1
       AND season = ${CURRENT_SEASON}
       AND season_type = 'Regular Season'
     ORDER BY game_date ASC`,
    [nbaPlayerId]
  );

  return result.rows.map((row) => ({
    game_date: toIsoDay(row.game_date),
    opponent_team_id: row.opponent_team_id === null || row.opponent_team_id === undefined
      ? null
      : String(row.opponent_team_id),
    is_home: typeof row.is_home === 'boolean' ? row.is_home : null,
    minutes: num(row.minutes),
    pts: num(row.pts),
    reb: num(row.reb),
    ast: num(row.ast),
    stl: num(row.stl),
    blk: num(row.blk),
    tov: num(row.tov),
    fg3m: num(row.fg3m),
    fgm: num(row.fgm),
    fga: num(row.fga),
    fg3a: num(row.fg3a),
    ftm: num(row.ftm),
    fta: num(row.fta),
  }));
}

async function withVsUsual(
  nbaId: string | null,
  prediction: PlayerPrediction | null
): Promise<AnalyticsPrediction | null> {
  if (!nbaId || !prediction) return prediction;
  try {
    const vsUsual = await getPlayerVsUsual(nbaId, prediction.game_date);
    return vsUsual ? { ...prediction, vs_usual: vsUsual } : prediction;
  } catch {
    // the comparison is garnish; the projection itself still renders without it.
    return prediction;
  }
}

export async function getPlayerAnalytics(playerId: number): Promise<PlayerAnalytics | null> {
  const playerResult = await query(
    `SELECT id, nba_id, name, team, position, headshot_url,
            injury_status, injury_detail,
            points_per_game::float     AS pts,
            rebounds_per_game::float   AS reb,
            assists_per_game::float    AS ast,
            steals_per_game::float     AS stl,
            blocks_per_game::float     AS blk,
            three_pointers_made::float AS fg3m,
            turnovers_per_game::float  AS tov,
            minutes_per_game::float    AS minutes
     FROM players
     WHERE id = $1`,
    [playerId]
  );

  const row = playerResult.rows[0];
  if (!row) return null;

  const nbaId = row.nba_id === null || row.nba_id === undefined ? null : String(row.nba_id);
  const snapshot = await getPoolSnapshot();
  const logs = await fetchPlayerLogs(nbaId);
  const prediction = await withVsUsual(nbaId, await getLatestPredictionForPlayer(nbaId));

  let fgm = 0;
  let fga = 0;
  let ftm = 0;
  let fta = 0;
  for (const log of logs) {
    fgm += log.fgm;
    fga += log.fga;
    ftm += log.ftm;
    fta += log.fta;
  }

  const values: StatValues = {
    pts: num(row.pts),
    reb: num(row.reb),
    ast: num(row.ast),
    stl: num(row.stl),
    blk: num(row.blk),
    fg3m: num(row.fg3m),
    tov: num(row.tov),
    minutes: num(row.minutes),
    fg_impact: attemptWeightedImpact(fgm, fga, snapshot.fgPct, logs.length),
    ft_impact: attemptWeightedImpact(ftm, fta, snapshot.ftPct, logs.length),
  };

  const rollingAll: Record<string, Array<number | null>> = {};
  for (const stat of TREND_STATS) {
    const series = logs.map((g) => g[stat]);
    const key = stat === 'minutes' ? 'min' : stat;
    rollingAll[`${key}_r5`] = rollingMean(series, 5);
    rollingAll[`${key}_r10`] = rollingMean(series, 10);
  }

  const windowStart = Math.max(0, logs.length - TREND_GAME_COUNT);
  const recent = logs.slice(windowStart);

  const games: TrendGame[] = recent.map((g) => ({
    game_date: g.game_date,
    opponent_team_abbr: g.opponent_team_id
      ? snapshot.teamAbbrById.get(g.opponent_team_id) ?? null
      : null,
    is_home: g.is_home,
    minutes: g.minutes,
    pts: g.pts,
    reb: g.reb,
    ast: g.ast,
    stl: g.stl,
    blk: g.blk,
    tov: g.tov,
    fgm: g.fgm,
    fga: g.fga,
    fg3m: g.fg3m,
    fg3a: g.fg3a,
    ftm: g.ftm,
    fta: g.fta,
  }));

  const rolling: RollingPoint[] = recent.map((g, i) => {
    const point: RollingPoint = { game_date: g.game_date };
    for (const [key, series] of Object.entries(rollingAll)) {
      point[key] = series[windowStart + i];
    }
    return point;
  });

  const lastLoggedDay = logs.length > 0 ? logs[logs.length - 1].game_date : null;

  return {
    player: {
      id: num(row.id),
      nba_id: nbaId,
      name: String(row.name ?? ''),
      team: row.team === null || row.team === undefined ? null : String(row.team),
      position: row.position === null || row.position === undefined ? null : String(row.position),
      headshot_url:
        row.headshot_url === null || row.headshot_url === undefined
          ? null
          : String(row.headshot_url),
      injury_status:
        row.injury_status === null || row.injury_status === undefined
          ? null
          : String(row.injury_status),
      injury_detail:
        row.injury_detail === null || row.injury_detail === undefined
          ? null
          : String(row.injury_detail),
    },
    as_of: {
      logs: lastLoggedDay ? `${lastLoggedDay}T00:00:00.000Z` : null,
      distributions: new Date(snapshot.fetchedAt).toISOString(),
    },
    pool: poolDescriptor(snapshot.players.length),
    percentiles: buildPercentiles(snapshot, values),
    distributions: buildDistributions(snapshot, values),
    trends: {
      games,
      rolling,
      last10_vs_season: last10VsSeason(logs),
    },
    prediction,
  };
}

export async function getStatDistribution(stat: AnalyticsStat): Promise<LeagueDistribution> {
  const snapshot = await getPoolSnapshot();
  const values = poolValues(snapshot, stat);
  const reversed = REVERSED_STATS.has(stat);

  const players = snapshot.players
    .map((p) => ({
      id: p.id,
      name: p.name,
      value: round(p.values[stat], 3),
      percentile: percentRank(values, p.values[stat], reversed),
    }))
    .sort((a, b) => b.percentile - a.percentile || a.name.localeCompare(b.name));

  return {
    stat,
    pool: poolDescriptor(snapshot.players.length),
    mean: round(mean(values), 3),
    stddev: round(stddev(values), 3),
    buckets: buildBuckets(values),
    players,
  };
}
