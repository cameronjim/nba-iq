import { query } from '../db.js';
import { americanToDecimal, americanToImpliedProb, noVigProbabilities } from './oddsMath.js';
import { pivotUpcomingRows, type UpcomingPredictionRow } from './playerPredictions.js';
import {
  DEFAULT_VOID_RULE,
  PRA_COMPONENTS,
  PROP_MARKETS,
  PROP_SIDES,
  isPropMarket,
  priceProp,
  type PlayerGameStats,
  type PropMarket,
  type PropSide,
  type VoidRule,
} from './propProbability.js';
import { getLatestSlateRun, num, round, rowsOrEmpty, toIsoDay } from './slate.js';

export const MIN_PICK_EV = 0.02;

export const MIN_PICK_PROB_ACTIVE = 0.6;

// a line the book has not re-posted within this long of its newest capture has been taken down or moved.
export const SUPERSEDED_LINE_MS = 10 * 60 * 1000;

export type PickResult = 'win' | 'loss' | 'push' | 'void';

export interface PropSnapshot {
  nba_player_id: string;
  player_name: string | null;
  nba_game_id: string;
  game_date: string;
  bookmaker: string;
  market: PropMarket;
  line: number;
  over_price: number | null;
  under_price: number | null;
  captured_at: string;
}

export interface PlayerGameForecast {
  prob_active: number | null;
  stats: PlayerGameStats;
}

export interface PropPick {
  nba_player_id: string;
  player_name: string | null;
  nba_game_id: string;
  game_date: string;
  market: PropMarket;
  line: number;
  side: PropSide;
  bookmaker: string;
  price: number;
  implied_prob: number;
  implied_prob_novig: number | null;
  model_prob: number;
  model_prob_plays: number;
  prob_active: number;
  void_rule: VoidRule;
  ev: number;
  kelly_fraction: number;
  // model_prob minus the no-vig implied probability, or the raw implied when only one side is posted
  edge: number;
}

export interface PickThresholds {
  minEv: number;
  minProbActive: number;
}

export const DEFAULT_THRESHOLDS: PickThresholds = {
  minEv: MIN_PICK_EV,
  minProbActive: MIN_PICK_PROB_ACTIVE,
};

export function forecastKey(nbaPlayerId: string, nbaGameId: string): string {
  return `${nbaPlayerId}|${nbaGameId}`;
}

export function dropSupersededLines(snapshots: PropSnapshot[]): PropSnapshot[] {
  const newest = new Map<string, number>();
  const groupOf = (s: PropSnapshot): string =>
    `${s.nba_player_id}|${s.nba_game_id}|${s.market}|${s.bookmaker}`;
  for (const snap of snapshots) {
    const at = Date.parse(snap.captured_at);
    const key = groupOf(snap);
    if (!Number.isNaN(at) && at > (newest.get(key) ?? -Infinity)) newest.set(key, at);
  }
  return snapshots.filter((snap) => {
    const at = Date.parse(snap.captured_at);
    const latest = newest.get(groupOf(snap));
    return latest !== undefined && !Number.isNaN(at) && latest - at <= SUPERSEDED_LINE_MS;
  });
}

function priceFor(snap: PropSnapshot, side: PropSide): { price: number | null; other: number | null } {
  return side === 'over'
    ? { price: snap.over_price, other: snap.under_price }
    : { price: snap.under_price, other: snap.over_price };
}

export function candidatePicks(
  snapshots: PropSnapshot[],
  forecasts: Map<string, PlayerGameForecast>,
  voidRule: VoidRule = DEFAULT_VOID_RULE
): PropPick[] {
  const picks: PropPick[] = [];
  for (const snap of snapshots) {
    const forecast = forecasts.get(forecastKey(snap.nba_player_id, snap.nba_game_id));
    if (!forecast || forecast.prob_active === null) continue;
    for (const side of PROP_SIDES) {
      const { price, other } = priceFor(snap, side);
      if (price === null) continue;
      const pricing = priceProp({
        market: snap.market,
        line: snap.line,
        side,
        price,
        stats: forecast.stats,
        probActive: forecast.prob_active,
        voidRule,
      });
      if (!pricing) continue;
      const implied = americanToImpliedProb(price);
      const novig =
        other === null ? null : noVigProbabilities(implied, americanToImpliedProb(other))[0];
      picks.push({
        nba_player_id: snap.nba_player_id,
        player_name: snap.player_name,
        nba_game_id: snap.nba_game_id,
        game_date: snap.game_date,
        market: snap.market,
        line: snap.line,
        side,
        bookmaker: snap.bookmaker,
        price,
        implied_prob: implied,
        implied_prob_novig: novig,
        model_prob: pricing.model_prob,
        model_prob_plays: pricing.model_prob_plays,
        prob_active: pricing.prob_active,
        void_rule: pricing.void_rule,
        ev: pricing.ev,
        kelly_fraction: pricing.kelly_fraction,
        edge: pricing.model_prob - (novig ?? implied),
      });
    }
  }
  return picks;
}

export function selectPicks(
  candidates: PropPick[],
  thresholds: PickThresholds = DEFAULT_THRESHOLDS
): PropPick[] {
  return candidates
    .filter((pick) => pick.ev > thresholds.minEv && pick.prob_active > thresholds.minProbActive)
    .sort((a, b) => b.ev - a.ev);
}

export interface GameOutcome {
  played: boolean;
  stats: Partial<Record<string, number | null>>;
}

export function actualFor(market: PropMarket, stats: GameOutcome['stats']): number | null {
  const parts = market === 'pra' ? [...PRA_COMPONENTS] : [market];
  let total = 0;
  for (const stat of parts) {
    const value = stats[stat];
    if (value === null || value === undefined || !Number.isFinite(value)) return null;
    total += value;
  }
  return total;
}

export interface GradedPick {
  result: PickResult;
  actual: number | null;
}

// null means the pick cannot be graded yet: he played but the stat is missing.
export function gradePick(
  pick: { market: PropMarket; line: number; side: PropSide; void_rule: VoidRule },
  outcome: GameOutcome
): GradedPick | null {
  if (!outcome.played) {
    return { result: pick.void_rule === 'dnp_void' ? 'void' : 'loss', actual: null };
  }
  const actual = actualFor(pick.market, outcome.stats);
  if (actual === null) return null;
  if (actual === pick.line) return { result: 'push', actual };
  const overWins = actual > pick.line;
  const won = pick.side === 'over' ? overWins : !overWins;
  return { result: won ? 'win' : 'loss', actual };
}

// closing implied minus surfaced implied in percentage points; positive means the market moved toward the pick.
export function clvPoints(
  surfaced: { price: number; line: number },
  closing: { price: number | null; line: number | null }
): number | null {
  if (closing.price === null || closing.line === null || closing.line !== surfaced.line) return null;
  return (americanToImpliedProb(closing.price) - americanToImpliedProb(surfaced.price)) * 100;
}

export interface SummaryRow {
  market: PropMarket;
  result: PickResult | null;
  price: number;
  line: number;
  ev: number;
  model_prob: number;
  closing_price: number | null;
  closing_line: number | null;
}

export interface MarketSummary {
  market: PropMarket | 'all';
  picks: number;
  settled: number;
  wins: number;
  losses: number;
  pushes: number;
  voids: number;
  // wins over graded picks with action (win, loss, push), comparable to avg_model_prob
  hit_rate: number | null;
  avg_model_prob: number | null;
  calibration_gap_points: number | null;
  avg_ev: number | null;
  // realized profit per $1 risked on graded picks with action
  roi: number | null;
  clv_count: number;
  avg_clv_points: number | null;
}

function average(values: number[]): number | null {
  if (values.length === 0) return null;
  return values.reduce((acc, v) => acc + v, 0) / values.length;
}

function summarizeGroup(market: PropMarket | 'all', rows: SummaryRow[]): MarketSummary {
  const action = rows.filter((r) => r.result === 'win' || r.result === 'loss' || r.result === 'push');
  const wins = rows.filter((r) => r.result === 'win').length;
  const losses = rows.filter((r) => r.result === 'loss').length;
  const pushes = rows.filter((r) => r.result === 'push').length;
  const voids = rows.filter((r) => r.result === 'void').length;
  const hitRate = action.length > 0 ? wins / action.length : null;
  const avgModel = average(action.map((r) => r.model_prob));
  let profit = 0;
  for (const r of action) {
    if (r.result === 'win') profit += americanToDecimal(r.price) - 1;
    else if (r.result === 'loss') profit -= 1;
  }
  const clvs = rows
    .filter((r) => r.result !== null)
    .map((r) => clvPoints(r, { price: r.closing_price, line: r.closing_line }))
    .filter((v): v is number => v !== null);
  return {
    market,
    picks: rows.length,
    settled: rows.filter((r) => r.result !== null).length,
    wins,
    losses,
    pushes,
    voids,
    hit_rate: round(hitRate, 4),
    avg_model_prob: round(avgModel, 4),
    calibration_gap_points:
      hitRate === null || avgModel === null ? null : round((hitRate - avgModel) * 100, 2),
    avg_ev: round(average(action.map((r) => r.ev)), 4),
    roi: action.length > 0 ? round(profit / action.length, 4) : null,
    clv_count: clvs.length,
    avg_clv_points: round(average(clvs), 2),
  };
}

export function summarizePicks(rows: SummaryRow[]): MarketSummary[] {
  if (rows.length === 0) return [];
  const byMarket = PROP_MARKETS.map((market) => ({
    market,
    rows: rows.filter((r) => r.market === market),
  }))
    .filter((group) => group.rows.length > 0)
    .map((group) => summarizeGroup(group.market, group.rows));
  return [summarizeGroup('all', rows), ...byMarket];
}

export interface PropRunMeta {
  id: number;
  model_version: string;
  predicted_at: string | null;
  information_as_of: string | null;
}

export interface RefreshResult {
  run: PropRunMeta | null;
  snapshots: number;
  candidates: number;
  recorded: number;
  picks: PropPick[];
}

const FORECAST_STATS = [...PROP_MARKETS.filter((m) => m !== 'pra'), 'prob_active'];

const SNAPSHOT_SQL = `
  SELECT DISTINCT ON (o.nba_player_id, o.nba_game_id, o.market, o.bookmaker, o.line)
         o.nba_player_id,
         o.player_name,
         o.nba_game_id,
         o.game_date,
         o.bookmaker,
         o.market,
         o.line::float AS line,
         o.over_price,
         o.under_price,
         o.captured_at
  FROM prop_odds_snapshots o
  LEFT JOIN nba_schedule s ON s.nba_game_id = o.nba_game_id
  WHERE o.nba_player_id IS NOT NULL
    AND o.nba_game_id IS NOT NULL
    AND o.line IS NOT NULL
    AND o.game_date BETWEEN $1::date AND $2::date
    AND (s.scheduled_at IS NULL OR (s.scheduled_at > NOW() AND o.captured_at < s.scheduled_at))
  ORDER BY o.nba_player_id, o.nba_game_id, o.market, o.bookmaker, o.line, o.captured_at DESC
`;

const FORECAST_SQL = `
  SELECT nba_player_id,
         nba_game_id,
         game_date,
         stat,
         quantile::float AS quantile,
         value::float    AS value,
         conditional
  FROM player_game_predictions
  WHERE prediction_run_id = $1
    AND nba_player_id = ANY($2::text[])
    AND stat = ANY($3::text[])
`;

const INSERT_SQL = `
  INSERT INTO model_prop_picks (
    prediction_run_id, nba_player_id, player_name, nba_game_id, game_date, market, line, side,
    bookmaker, price, implied_prob, implied_prob_novig, model_prob, model_prob_plays,
    prob_active, void_rule, ev, kelly_fraction
  )
  SELECT $1, t.nba_player_id, t.player_name, t.nba_game_id, t.game_date, t.market, t.line, t.side,
         t.bookmaker, t.price, t.implied_prob, t.implied_prob_novig, t.model_prob, t.model_prob_plays,
         t.prob_active, t.void_rule, t.ev, t.kelly_fraction
  FROM unnest(
    $2::text[], $3::text[], $4::text[], $5::date[], $6::text[], $7::numeric[], $8::text[],
    $9::text[], $10::int[], $11::numeric[], $12::numeric[], $13::numeric[], $14::numeric[],
    $15::numeric[], $16::text[], $17::numeric[], $18::numeric[]
  ) AS t(
    nba_player_id, player_name, nba_game_id, game_date, market, line, side,
    bookmaker, price, implied_prob, implied_prob_novig, model_prob, model_prob_plays,
    prob_active, void_rule, ev, kelly_fraction
  )
  ON CONFLICT (prediction_run_id, nba_player_id, nba_game_id, market, line, side, bookmaker)
  DO NOTHING
`;

export interface SnapshotRow {
  nba_player_id: unknown;
  player_name: unknown;
  nba_game_id: unknown;
  game_date: unknown;
  bookmaker: unknown;
  market: unknown;
  line: unknown;
  over_price: unknown;
  under_price: unknown;
  captured_at: unknown;
}

export interface ForecastRow extends UpcomingPredictionRow {
  nba_player_id: unknown;
}

function text(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const str = String(value);
  return str === '' ? null : str;
}

function instant(value: unknown): string | null {
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value.toISOString();
  if (value === null || value === undefined) return null;
  const parsed = new Date(String(value));
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function price(value: unknown): number | null {
  const parsed = num(value);
  return parsed !== null && Number.isInteger(parsed) && Math.abs(parsed) >= 100 ? parsed : null;
}

export function parseSnapshotRows(rows: SnapshotRow[]): PropSnapshot[] {
  const snapshots: PropSnapshot[] = [];
  for (const row of rows) {
    const playerId = text(row.nba_player_id);
    const gameId = text(row.nba_game_id);
    const gameDate = toIsoDay(row.game_date);
    const bookmaker = text(row.bookmaker);
    const line = num(row.line);
    const capturedAt = instant(row.captured_at);
    if (!playerId || !gameId || !gameDate || !bookmaker || line === null || !capturedAt) continue;
    if (!isPropMarket(row.market)) continue;
    snapshots.push({
      nba_player_id: playerId,
      player_name: text(row.player_name),
      nba_game_id: gameId,
      game_date: gameDate,
      bookmaker,
      market: row.market,
      line,
      over_price: price(row.over_price),
      under_price: price(row.under_price),
      captured_at: capturedAt,
    });
  }
  return snapshots;
}

export function forecastsFromRows(rows: ForecastRow[]): Map<string, PlayerGameForecast> {
  const byPlayer = new Map<string, ForecastRow[]>();
  for (const row of rows) {
    const playerId = text(row.nba_player_id);
    if (!playerId) continue;
    const list = byPlayer.get(playerId) ?? [];
    list.push(row);
    byPlayer.set(playerId, list);
  }
  const forecasts = new Map<string, PlayerGameForecast>();
  for (const [playerId, playerRows] of byPlayer) {
    for (const game of pivotUpcomingRows(playerRows, null)) {
      forecasts.set(forecastKey(playerId, game.nba_game_id), {
        prob_active: game.prob_active,
        stats: game.stats,
      });
    }
  }
  return forecasts;
}

async function recordPicks(runId: number, picks: PropPick[]): Promise<number> {
  if (picks.length === 0) return 0;
  const col = <T>(fn: (pick: PropPick) => T): T[] => picks.map(fn);
  const result = await query(INSERT_SQL, [
    runId,
    col((p) => p.nba_player_id),
    col((p) => p.player_name),
    col((p) => p.nba_game_id),
    col((p) => p.game_date),
    col((p) => p.market),
    col((p) => p.line),
    col((p) => p.side),
    col((p) => p.bookmaker),
    col((p) => p.price),
    col((p) => round(p.implied_prob, 4)),
    col((p) => round(p.implied_prob_novig, 4)),
    col((p) => round(p.model_prob, 4)),
    col((p) => round(p.model_prob_plays, 4)),
    col((p) => round(p.prob_active, 4)),
    col((p) => p.void_rule),
    col((p) => round(p.ev, 4)),
    col((p) => round(p.kelly_fraction, 4)),
  ]);
  return result.rowCount ?? 0;
}

export async function refreshPropPicks(
  thresholds: PickThresholds = DEFAULT_THRESHOLDS
): Promise<RefreshResult> {
  const run = await getLatestSlateRun();
  if (!run) return { run: null, snapshots: 0, candidates: 0, recorded: 0, picks: [] };
  const meta: PropRunMeta = {
    id: run.id,
    model_version: run.model_version,
    predicted_at: run.predicted_at,
    information_as_of: run.information_as_of,
  };
  if (!run.covers_from || !run.covers_to) {
    return { run: meta, snapshots: 0, candidates: 0, recorded: 0, picks: [] };
  }

  const snapshotRows = await rowsOrEmpty<SnapshotRow>(() =>
    query(SNAPSHOT_SQL, [run.covers_from, run.covers_to])
  );
  const snapshots = dropSupersededLines(parseSnapshotRows(snapshotRows));
  if (snapshots.length === 0) {
    return { run: meta, snapshots: 0, candidates: 0, recorded: 0, picks: [] };
  }

  const playerIds = [...new Set(snapshots.map((s) => s.nba_player_id))];
  const forecastRows = await rowsOrEmpty<ForecastRow>(() =>
    query(FORECAST_SQL, [run.id, playerIds, FORECAST_STATS])
  );
  const candidates = candidatePicks(snapshots, forecastsFromRows(forecastRows));
  const picks = selectPicks(candidates, thresholds);
  const recorded = await recordPicks(run.id, picks);
  return { run: meta, snapshots: snapshots.length, candidates: candidates.length, recorded, picks };
}

const SETTLE_SELECT_SQL = `
  SELECT p.id,
         p.market,
         p.line::float AS line,
         p.side,
         p.void_rule,
         (l.nba_player_id IS NOT NULL) AS has_log,
         l.minutes::float AS minutes,
         l.pts, l.reb, l.ast, l.fg3m, l.stl, l.blk, l.tov,
         closing.line::float AS closing_line,
         CASE WHEN p.side = 'over' THEN closing.over_price ELSE closing.under_price END AS closing_price
  FROM model_prop_picks p
  LEFT JOIN player_game_logs l
    ON l.nba_player_id = p.nba_player_id
   AND l.nba_game_id = p.nba_game_id
  LEFT JOIN nba_schedule s ON s.nba_game_id = p.nba_game_id
  LEFT JOIN LATERAL (
    SELECT o.line, o.over_price, o.under_price
    FROM prop_odds_snapshots o
    WHERE o.nba_player_id = p.nba_player_id
      AND o.nba_game_id = p.nba_game_id
      AND o.market = p.market
      AND o.bookmaker = p.bookmaker
      AND o.captured_at < s.scheduled_at
    ORDER BY o.captured_at DESC, (o.line = p.line) DESC
    LIMIT 1
  ) closing ON TRUE
  WHERE p.result IS NULL
    AND EXISTS (
      SELECT 1
      FROM player_game_logs g
      WHERE g.nba_game_id = p.nba_game_id
    )
`;

const SETTLE_UPDATE_SQL = `
  UPDATE model_prop_picks p
  SET result = t.result,
      actual = t.actual,
      closing_price = t.closing_price,
      closing_line = t.closing_line,
      settled_at = NOW()
  FROM unnest($1::bigint[], $2::text[], $3::numeric[], $4::int[], $5::numeric[])
       AS t(id, result, actual, closing_price, closing_line)
  WHERE p.id = t.id
    AND p.result IS NULL
`;

export interface SettleRow {
  id: unknown;
  market: unknown;
  line: unknown;
  side: unknown;
  void_rule: unknown;
  has_log: unknown;
  minutes: unknown;
  pts: unknown;
  reb: unknown;
  ast: unknown;
  fg3m: unknown;
  stl: unknown;
  blk: unknown;
  tov: unknown;
  closing_line: unknown;
  closing_price: unknown;
}

export interface SettleResult {
  examined: number;
  settled: number;
  by_result: Record<PickResult, number>;
}

export function outcomeFromRow(row: SettleRow): GameOutcome {
  const minutes = num(row.minutes);
  return {
    played: row.has_log === true && minutes !== null && minutes > 0,
    stats: {
      pts: num(row.pts),
      reb: num(row.reb),
      ast: num(row.ast),
      fg3m: num(row.fg3m),
      stl: num(row.stl),
      blk: num(row.blk),
      tov: num(row.tov),
    },
  };
}

export async function settlePropPicks(): Promise<SettleResult> {
  const rows = await rowsOrEmpty<SettleRow>(() => query(SETTLE_SELECT_SQL));
  const byResult: Record<PickResult, number> = { win: 0, loss: 0, push: 0, void: 0 };
  const updates: Array<{ id: number; graded: GradedPick; closingPrice: number | null; closingLine: number | null }> = [];

  for (const row of rows) {
    const id = num(row.id);
    const line = num(row.line);
    const side = row.side === 'over' || row.side === 'under' ? row.side : null;
    const voidRule = row.void_rule === 'dnp_loss' ? 'dnp_loss' : 'dnp_void';
    if (id === null || line === null || side === null || !isPropMarket(row.market)) continue;
    const graded = gradePick({ market: row.market, line, side, void_rule: voidRule }, outcomeFromRow(row));
    if (!graded) continue;
    byResult[graded.result] += 1;
    updates.push({ id, graded, closingPrice: price(row.closing_price), closingLine: num(row.closing_line) });
  }

  if (updates.length > 0) {
    await query(SETTLE_UPDATE_SQL, [
      updates.map((u) => u.id),
      updates.map((u) => u.graded.result),
      updates.map((u) => u.graded.actual),
      updates.map((u) => u.closingPrice),
      updates.map((u) => u.closingLine),
    ]);
  }
  return { examined: rows.length, settled: updates.length, by_result: byResult };
}

export interface SurfacedPick extends PropPick {
  id: number;
  surfaced_at: string | null;
}

export interface SurfacedPicksResponse {
  run: PropRunMeta | null;
  dates: string[];
  picks: SurfacedPick[];
}

const SURFACED_SQL = `
  WITH latest AS (
    SELECT MAX(prediction_run_id) AS run_id
    FROM model_prop_picks
    WHERE game_date = ANY($1::date[])
  )
  SELECT p.id,
         p.prediction_run_id,
         p.nba_player_id,
         p.player_name,
         p.nba_game_id,
         p.game_date,
         p.market,
         p.line::float AS line,
         p.side,
         p.bookmaker,
         p.price,
         p.implied_prob::float AS implied_prob,
         p.implied_prob_novig::float AS implied_prob_novig,
         p.model_prob::float AS model_prob,
         p.model_prob_plays::float AS model_prob_plays,
         p.prob_active::float AS prob_active,
         p.void_rule,
         p.ev::float AS ev,
         p.kelly_fraction::float AS kelly_fraction,
         p.surfaced_at,
         r.model_version,
         r.predicted_at,
         COALESCE(r.information_as_of, r.forecast_cutoff_at) AS information_as_of
  FROM model_prop_picks p
  JOIN latest ON p.prediction_run_id = latest.run_id
  JOIN prediction_runs r ON r.id = p.prediction_run_id
  WHERE p.game_date = ANY($1::date[])
  ORDER BY p.game_date ASC, p.ev DESC, p.id ASC
`;

interface SurfacedRow {
  id: unknown;
  prediction_run_id: unknown;
  nba_player_id: unknown;
  player_name: unknown;
  nba_game_id: unknown;
  game_date: unknown;
  market: unknown;
  line: unknown;
  side: unknown;
  bookmaker: unknown;
  price: unknown;
  implied_prob: unknown;
  implied_prob_novig: unknown;
  model_prob: unknown;
  model_prob_plays: unknown;
  prob_active: unknown;
  void_rule: unknown;
  ev: unknown;
  kelly_fraction: unknown;
  surfaced_at: unknown;
  model_version: unknown;
  predicted_at: unknown;
  information_as_of: unknown;
}

function surfacedPick(row: SurfacedRow): SurfacedPick | null {
  const id = num(row.id);
  const playerId = text(row.nba_player_id);
  const gameId = text(row.nba_game_id);
  const gameDate = toIsoDay(row.game_date);
  const bookmaker = text(row.bookmaker);
  const line = num(row.line);
  const odds = price(row.price);
  const implied = num(row.implied_prob);
  const modelProb = num(row.model_prob);
  const modelPlays = num(row.model_prob_plays);
  const probActive = num(row.prob_active);
  const ev = num(row.ev);
  const kelly = num(row.kelly_fraction);
  const side = row.side === 'over' || row.side === 'under' ? row.side : null;
  if (
    id === null || !playerId || !gameId || !gameDate || !bookmaker || line === null ||
    odds === null || implied === null || modelProb === null || modelPlays === null ||
    probActive === null || ev === null || kelly === null || side === null ||
    !isPropMarket(row.market)
  ) {
    return null;
  }
  const novig = num(row.implied_prob_novig);
  return {
    id,
    nba_player_id: playerId,
    player_name: text(row.player_name),
    nba_game_id: gameId,
    game_date: gameDate,
    market: row.market,
    line,
    side,
    bookmaker,
    price: odds,
    implied_prob: implied,
    implied_prob_novig: novig,
    model_prob: modelProb,
    model_prob_plays: modelPlays,
    prob_active: probActive,
    void_rule: row.void_rule === 'dnp_loss' ? 'dnp_loss' : 'dnp_void',
    ev,
    kelly_fraction: kelly,
    edge: round(modelProb - (novig ?? implied), 4) ?? 0,
    surfaced_at: instant(row.surfaced_at),
  };
}

export async function getSurfacedPropPicks(dates: string[]): Promise<SurfacedPicksResponse> {
  const rows = await rowsOrEmpty<SurfacedRow>(() => query(SURFACED_SQL, [dates]));
  const first = rows[0];
  const runId = first ? num(first.prediction_run_id) : null;
  const run: PropRunMeta | null =
    first && runId !== null
      ? {
          id: runId,
          model_version: String(first.model_version ?? ''),
          predicted_at: instant(first.predicted_at),
          information_as_of: instant(first.information_as_of),
        }
      : null;
  const picks = rows.map(surfacedPick).filter((p): p is SurfacedPick => p !== null);
  return { run, dates, picks };
}

const SUMMARY_SQL = `
  SELECT market,
         result,
         price,
         line::float AS line,
         ev::float AS ev,
         model_prob::float AS model_prob,
         closing_price,
         closing_line::float AS closing_line
  FROM model_prop_picks
`;

export interface SummarySqlRow {
  market: unknown;
  result: unknown;
  price: unknown;
  line: unknown;
  ev: unknown;
  model_prob: unknown;
  closing_price: unknown;
  closing_line: unknown;
}

const RESULTS: readonly PickResult[] = ['win', 'loss', 'push', 'void'];

export function parseSummaryRows(rows: SummarySqlRow[]): SummaryRow[] {
  const parsed: SummaryRow[] = [];
  for (const row of rows) {
    const odds = price(row.price);
    const line = num(row.line);
    const ev = num(row.ev);
    const modelProb = num(row.model_prob);
    if (!isPropMarket(row.market) || odds === null || line === null || ev === null || modelProb === null) continue;
    const result = RESULTS.find((r) => r === row.result) ?? null;
    parsed.push({
      market: row.market,
      result,
      price: odds,
      line,
      ev,
      model_prob: modelProb,
      closing_price: price(row.closing_price),
      closing_line: num(row.closing_line),
    });
  }
  return parsed;
}

export async function getPropPickSummary(): Promise<MarketSummary[]> {
  const rows = await rowsOrEmpty<SummarySqlRow>(() => query(SUMMARY_SQL));
  return summarizePicks(parseSummaryRows(rows));
}
