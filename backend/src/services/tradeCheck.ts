import { query } from '../db.js';
import { OUTLOOK_LABELS, formatSignedWins, joinWords } from './decisionText.js';
import {
  getLatestCompleteRun,
  resolvePlayerName,
  round,
  rowsOrEmpty,
  type RunSummary,
} from './slate.js';
import { windowRange, type WatchlistWindow } from './watchlist.js';
import {
  DEFAULT_OUTLOOK_DAYS,
  fetchPoolWeeklyTotals,
  fetchRoster,
  fetchRosterPredictions,
  toImpactInput,
  toSimPlayer,
  type RosterPredictionRow,
} from './weeklyOutlook.js';
import {
  DEFAULT_SEED,
  OPPONENT_DEFINITION,
  simulateWeek,
  typicalOpponent,
  type CategoryOutlook,
  type OutlookCategory,
  type SimPlayer,
} from './weeklySimulation.js';

export const MAX_TRADE_SIDE = 5;
export const EVEN_TRADE_WINS = 0.1;
export const NOTABLE_CATEGORY_DELTA = 0.05;
export const CATEGORIES_NAMED = 2;

export const TRADE_OPPONENT_NOTE = 'the same typical opponent, scaled to your current roster size, is used before and after';

export type TradeCheckStatus = 'ok' | 'empty_roster' | 'no_run' | 'no_games';

export interface TradeRequest {
  give: number[];
  get: number[];
}

export type TradeRequestParse = { ok: true; request: TradeRequest } | { ok: false; error: string };

export interface TradePlayer {
  id: number;
  nba_id: string | null;
  name: string;
  games: number;
}

export interface TradeCategoryDelta {
  category: OutlookCategory;
  label: string;
  lower_is_better: boolean;
  before: number | null;
  after: number | null;
  delta: number | null;
}

export interface TradeComparison {
  categories: TradeCategoryDelta[];
  before_expected_wins: number;
  after_expected_wins: number;
  delta_expected_wins: number;
}

export interface TradeCheckResponse {
  status: TradeCheckStatus;
  window: WatchlistWindow;
  run: RunSummary | null;
  seed: number;
  opponent_definition: string | null;
  give: TradePlayer[];
  get: TradePlayer[];
  before_expected_wins: number | null;
  after_expected_wins: number | null;
  delta_expected_wins: number | null;
  categories: TradeCategoryDelta[];
  verdict: string | null;
}

export type TradeCheckOutcome =
  | { kind: 'invalid'; error: string }
  | { kind: 'ok'; response: TradeCheckResponse };

function parseSide(raw: unknown, side: keyof TradeRequest): number[] | string {
  if (!Array.isArray(raw) || raw.length === 0) return `${side} must be a non-empty list of player ids`;
  if (raw.length > MAX_TRADE_SIDE) return `${side} can list at most ${MAX_TRADE_SIDE} players`;
  const ids: number[] = [];
  for (const value of raw) {
    const id = typeof value === 'string' && /^\d+$/.test(value) ? Number(value) : value;
    if (typeof id !== 'number' || !Number.isSafeInteger(id) || id < 1) {
      return `${side} must only contain positive whole-number player ids`;
    }
    if (ids.includes(id)) return `${side} lists the same player twice`;
    ids.push(id);
  }
  return ids;
}

export function parseTradeRequest(body: unknown): TradeRequestParse {
  const record = typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : {};
  const give = parseSide(record.give, 'give');
  if (typeof give === 'string') return { ok: false, error: give };
  const get = parseSide(record.get, 'get');
  if (typeof get === 'string') return { ok: false, error: get };
  if (give.some((id) => get.includes(id))) return { ok: false, error: 'a player cannot be on both sides of a trade' };
  return { ok: true, request: { give, get } };
}

function expectedWins(categories: readonly CategoryOutlook[]): number {
  return categories.reduce((sum, c) => sum + (c.win_probability ?? 0), 0);
}

export function compareTrade(
  before: readonly CategoryOutlook[],
  after: readonly CategoryOutlook[]
): TradeComparison {
  const afterByCategory = new Map(after.map((c) => [c.category, c]));
  const categories = before.map((row): TradeCategoryDelta => {
    const b = round(row.win_probability, 3);
    const a = round(afterByCategory.get(row.category)?.win_probability ?? null, 3);
    return {
      category: row.category,
      label: OUTLOOK_LABELS[row.category],
      lower_is_better: row.lower_is_better,
      before: b,
      after: a,
      delta: a === null || b === null ? null : round(a - b, 3),
    };
  });
  const beforeWins = round(expectedWins(before), 2) as number;
  const afterWins = round(expectedWins(after), 2) as number;
  return {
    categories,
    before_expected_wins: beforeWins,
    after_expected_wins: afterWins,
    delta_expected_wins: round(afterWins - beforeWins, 2) as number,
  };
}

function namedCategories(rows: readonly TradeCategoryDelta[], direction: 1 | -1): string[] {
  return rows
    .filter((r): r is TradeCategoryDelta & { delta: number } => r.delta !== null && r.delta * direction >= NOTABLE_CATEGORY_DELTA)
    .sort((a, b) => (b.delta - a.delta) * direction)
    .slice(0, CATEGORIES_NAMED)
    .map((r) => r.label);
}

export function tradeVerdict(rows: readonly TradeCategoryDelta[], delta: number): string {
  const gains = joinWords(namedCategories(rows, 1));
  const losses = joinWords(namedCategories(rows, -1));
  const wins = `${formatSignedWins(delta)} expected category wins`;

  if (delta >= EVEN_TRADE_WINS) {
    return `This trade helps: ${wins}${gains ? `, mainly ${gains}` : ''}${losses ? `; you lose some ${losses}` : ''}.`;
  }
  if (delta <= -EVEN_TRADE_WINS) {
    return `This trade hurts: ${wins}${losses ? `, mainly ${losses}` : ''}${gains ? `; you gain some ${gains}` : ''}.`;
  }
  const sides = [gains ? `gain some ${gains}` : null, losses ? `lose some ${losses}` : null].filter(
    (part): part is string => part !== null
  );
  return `This trade is about even: ${wins}${sides.length > 0 ? `; you ${sides.join(' and ')}` : ''}.`;
}

interface PlayerLookupRow {
  id: unknown;
  nba_id: unknown;
  name: unknown;
}

async function fetchPlayersById(ids: number[]): Promise<PlayerLookupRow[]> {
  return rowsOrEmpty<PlayerLookupRow>(() =>
    query(
      `SELECT id, nba_id, name
       FROM players
       WHERE id = ANY($1)`,
      [ids]
    )
  );
}

interface TradeSidePlayer {
  id: number;
  nba_id: string | null;
  name: string;
}

function toSidePlayer(row: { id: unknown; nba_id: unknown; name: unknown }): TradeSidePlayer {
  const nbaId = row.nba_id === null || row.nba_id === undefined || row.nba_id === '' ? null : String(row.nba_id);
  return { id: Number(row.id), nba_id: nbaId, name: resolvePlayerName(row.name, nbaId ?? String(row.id)).name };
}

function simPlayersOf(players: readonly TradeSidePlayer[], byPlayer: ReadonlyMap<string, RosterPredictionRow[]>): SimPlayer[] {
  return players.map((p) =>
    toSimPlayer(p.nba_id ?? `player-${p.id}`, p.nba_id ? byPlayer.get(p.nba_id) ?? [] : [])
  );
}

export async function checkTrade(
  userId: number,
  request: TradeRequest,
  start: string,
  days: number = DEFAULT_OUTLOOK_DAYS
): Promise<TradeCheckOutcome> {
  const window = windowRange(start, days);
  const roster = (await fetchRoster(userId)).map((row) =>
    toSidePlayer({ id: row.player_id, nba_id: row.nba_id, name: row.name })
  );
  const rosterIds = new Set(roster.map((p) => p.id));

  const response = (
    status: TradeCheckStatus,
    run: RunSummary | null,
    give: TradePlayer[],
    get: TradePlayer[]
  ): TradeCheckResponse => ({
    status,
    window,
    run,
    seed: DEFAULT_SEED,
    opponent_definition: null,
    give,
    get,
    before_expected_wins: null,
    after_expected_wins: null,
    delta_expected_wins: null,
    categories: [],
    verdict: null,
  });

  if (roster.length === 0) return { kind: 'ok', response: response('empty_roster', null, [], []) };
  if (request.give.some((id) => !rosterIds.has(id))) {
    return { kind: 'invalid', error: 'give must only list players on your roster' };
  }
  if (request.get.some((id) => rosterIds.has(id))) {
    return { kind: 'invalid', error: 'get must only list players who are not on your roster' };
  }

  const found = new Map((await fetchPlayersById(request.get)).map((row) => [Number(row.id), toSidePlayer(row)]));
  if (request.get.some((id) => !found.has(id))) {
    return { kind: 'invalid', error: 'get lists a player who does not exist' };
  }

  const giving = request.give.map((id) => roster.find((p) => p.id === id) as TradeSidePlayer);
  const getting = request.get.map((id) => found.get(id) as TradeSidePlayer);
  const idle = (players: TradeSidePlayer[]): TradePlayer[] => players.map((p) => ({ ...p, games: 0 }));

  const run = await getLatestCompleteRun();
  if (!run) return { kind: 'ok', response: response('no_run', null, idle(giving), idle(getting)) };
  const runSummary: RunSummary = { model_version: run.model_version, predicted_at: run.predicted_at };

  const before = roster;
  const after = [...roster.filter((p) => !request.give.includes(p.id)), ...getting];
  const nbaIds = [...new Set([...before, ...getting].map((p) => p.nba_id).filter((id): id is string => id !== null))];
  const rows = nbaIds.length > 0 ? await fetchRosterPredictions(run.id, nbaIds, window) : [];
  if (rows.length === 0) {
    return { kind: 'ok', response: response('no_games', runSummary, idle(giving), idle(getting)) };
  }

  const byPlayer = new Map<string, RosterPredictionRow[]>();
  for (const row of rows) {
    const id = String(row.nba_player_id);
    const list = byPlayer.get(id) ?? [];
    list.push(row);
    byPlayer.set(id, list);
  }

  const pool = (await fetchPoolWeeklyTotals(run.id, window)).map(toImpactInput);
  const opponent = typicalOpponent(pool, roster.length);
  const beforeSim = simulateWeek(simPlayersOf(before, byPlayer), opponent?.totals ?? null, { seed: DEFAULT_SEED });
  const afterSim = simulateWeek(simPlayersOf(after, byPlayer), opponent?.totals ?? null, { seed: DEFAULT_SEED });
  const comparison = compareTrade(beforeSim.categories, afterSim.categories);

  const gamesOf = (player: TradeSidePlayer): number =>
    player.nba_id ? new Set((byPlayer.get(player.nba_id) ?? []).map((r) => String(r.nba_game_id))).size : 0;
  const withGames = (players: TradeSidePlayer[]): TradePlayer[] => players.map((p) => ({ ...p, games: gamesOf(p) }));

  return {
    kind: 'ok',
    response: {
      ...response('ok', runSummary, withGames(giving), withGames(getting)),
      opponent_definition: opponent ? `${OPPONENT_DEFINITION}; ${TRADE_OPPONENT_NOTE}` : null,
      ...comparison,
      verdict: opponent ? tradeVerdict(comparison.categories, comparison.delta_expected_wins) : null,
    },
  };
}
