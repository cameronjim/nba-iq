import { query } from '../db.js';
import { COMPLETE_RUN_STATUS, PRODUCTION_CHANNEL } from './slate.js';


const UNCOND_SUFFIX = '_uncond';

const PROB_ACTIVE = 'prob_active';

const POINT_STATS = ['reb', 'ast', 'stl', 'blk', 'tov', 'fg3m'] as const;

type PointStat = (typeof POINT_STATS)[number];

const P10 = 0.1;
const P50 = 0.5;
const P90 = 0.9;

export interface QuantileRange {
  p10: number;
  p50: number;
  p90: number;
}

export interface ProjectedStats extends Record<PointStat, number | null> {
  minutes: QuantileRange | null;
  pts: QuantileRange | null;
}

export interface PlayerPrediction {
  as_of: string;
  model_version: string;
  game_date: string;
  prob_active: number | null;
  projected: ProjectedStats;
  conditional: true;
  unconditional_pts: number | null;
  summary: string | null;
}

export interface PredictionRow {
  model_version: string;
  predicted_at: Date | string;
  game_date: Date | string;
  stat: string;
  quantile: number | string | null;
  value: number | string;
  conditional: boolean;
}

interface CacheEntry {
  data: PlayerPrediction | null;
  fetchedAt: number;
}

const TTL_MS = 5 * 60 * 1000;

const cache = new Map<string, CacheEntry>();

export function clearPredictionsCache(): void {
  cache.clear();
}

function num(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
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
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value.toISOString();
  }
  if (typeof value === 'string') {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
  }
  return null;
}

function round(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function toRange(byQuantile: Map<number, number>): QuantileRange | null {
  const p10 = byQuantile.get(P10);
  const p50 = byQuantile.get(P50);
  const p90 = byQuantile.get(P90);
  if (p10 === undefined || p50 === undefined || p90 === undefined) return null;

  const [lo, mid, hi] = [p10, p50, p90].sort((a, b) => a - b);
  return { p10: round(lo, 2), p50: round(mid, 2), p90: round(hi, 2) };
}

function formatRange(range: QuantileRange | null, unit: string): string | null {
  if (!range) return null;
  return `${range.p50.toFixed(1)} ${unit} (${range.p10.toFixed(1)}-${range.p90.toFixed(1)})`;
}

function buildSummary(prediction: Omit<PlayerPrediction, 'summary'>): string | null {
  const parts: string[] = [];
  if (prediction.prob_active !== null) {
    parts.push(`${Math.round(prediction.prob_active * 100)}% to play`);
  }
  const minutes = formatRange(prediction.projected.minutes, 'min');
  if (minutes) parts.push(minutes);
  const points = formatRange(prediction.projected.pts, 'pts');
  if (points) parts.push(points);
  if (parts.length === 0) return null;

  const tail =
    prediction.unconditional_pts === null
      ? ''
      : `, ${prediction.unconditional_pts.toFixed(1)} pts averaged over the schedule`;
  return `${parts.join(', ')} if he plays${tail}.`;
}

export function pivotPredictionRows(rows: PredictionRow[]): PlayerPrediction | null {
  if (rows.length === 0) return null;

  const first = rows[0];
  const gameDate = toIsoDay(first.game_date);
  const asOf = toIsoInstant(first.predicted_at);
  if (!gameDate || !asOf) return null;

  const expected = new Map<string, number>();
  const quantiles = new Map<string, Map<number, number>>();
  let probActive: number | null = null;

  for (const row of rows) {
    const value = num(row.value);
    if (value === null) continue;
    const quantile = num(row.quantile);

    if (quantile === null) {
      if (row.stat === PROB_ACTIVE) {
        probActive = Math.min(Math.max(value, 0), 1);
        continue;
      }
      expected.set(row.stat, value);
      continue;
    }

    const forStat = quantiles.get(row.stat) ?? new Map<number, number>();
    forStat.set(quantile, value);
    quantiles.set(row.stat, forStat);
  }

  const minutesQuantiles = quantiles.get('minutes');
  const ptsQuantiles = quantiles.get('pts');
  const projected: ProjectedStats = {
    minutes: minutesQuantiles ? toRange(minutesQuantiles) : null,
    pts: ptsQuantiles ? toRange(ptsQuantiles) : null,
    reb: null,
    ast: null,
    stl: null,
    blk: null,
    tov: null,
    fg3m: null,
  };

  for (const stat of POINT_STATS) {
    const value = expected.get(stat);
    projected[stat] = value === undefined ? null : round(value, 2);
  }

  const unconditionalPts = expected.get(`pts${UNCOND_SUFFIX}`);
  const base: Omit<PlayerPrediction, 'summary'> = {
    as_of: asOf,
    model_version: String(first.model_version ?? ''),
    game_date: gameDate,
    prob_active: probActive === null ? null : round(probActive, 4),
    projected,
    conditional: true,
    unconditional_pts: unconditionalPts === undefined ? null : round(unconditionalPts, 2),
  };

  return { ...base, summary: buildSummary(base) };
}

const LATEST_PREDICTION_SQL = `
  WITH latest_run AS (
    SELECT id, model_version, predicted_at
    FROM prediction_runs
    WHERE status = $2
      AND channel = $3
    ORDER BY predicted_at DESC
    LIMIT 1
  ),
  next_game AS (
    SELECT p.game_date
    FROM player_game_predictions p
    JOIN latest_run r ON r.id = p.prediction_run_id
    WHERE p.nba_player_id = $1
      AND p.game_date >= CURRENT_DATE
    ORDER BY p.game_date ASC
    LIMIT 1
  )
  SELECT r.model_version,
         r.predicted_at,
         p.game_date,
         p.stat,
         p.quantile::float AS quantile,
         p.value::float    AS value,
         p.conditional
  FROM player_game_predictions p
  JOIN latest_run r ON r.id = p.prediction_run_id
  JOIN next_game g  ON g.game_date = p.game_date
  WHERE p.nba_player_id = $1
`;

export async function getLatestPredictionForPlayer(
  nbaPlayerId: string | null
): Promise<PlayerPrediction | null> {
  if (!nbaPlayerId) return null;

  const cached = cache.get(nbaPlayerId);
  if (cached && Date.now() - cached.fetchedAt < TTL_MS) return cached.data;

  let data: PlayerPrediction | null = null;
  try {
    const result = await query(LATEST_PREDICTION_SQL, [
      nbaPlayerId,
      COMPLETE_RUN_STATUS,
      PRODUCTION_CHANNEL,
    ]);
    data = pivotPredictionRows(result.rows as PredictionRow[]);
  } catch {
    data = null;
  }

  cache.set(nbaPlayerId, { data, fetchedAt: Date.now() });
  return data;
}
