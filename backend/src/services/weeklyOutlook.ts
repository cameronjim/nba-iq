import { query } from '../db.js';
import {
  PRODUCTION_CHANNEL,
  PROB_ACTIVE_STAT,
  getLatestCompleteRun,
  num,
  resolvePlayerName,
  round,
  rowsOrEmpty,
  uncondStat,
  type ImpactInput,
  type RunSummary,
} from './slate.js';
import { pivotUpcomingRows, type UpcomingPredictionRow } from './playerPredictions.js';
import { MAX_WINDOW_DAYS, windowRange, type WatchlistWindow } from './watchlist.js';
import {
  DEPENDENCE_RHO,
  OPPONENT_DEFINITION,
  OUTLOOK_CATEGORIES,
  SIM_STATS,
  TYPICAL_LEAGUE_TEAMS,
  simulateWeek,
  typicalOpponent,
  type CategoryOutlook,
  type OpponentLine,
  type SimGame,
  type SimPlayer,
  type SimStat,
  type StatInput,
} from './weeklySimulation.js';

export const DEFAULT_OUTLOOK_DAYS = 7;

export type WeeklyOutlookStatus = 'ok' | 'empty_roster' | 'no_run' | 'no_games';

export interface OutlookPlayer {
  player_id: number;
  nba_player_id: string | null;
  name: string;
  games_scheduled: number;
  expected_games: number;
  miss_risk: number;
  simulated_miss_risk: number;
  fallback_stats: SimStat[];
}

export interface OutlookOpponent {
  definition: string;
  league_teams: number;
  pool_size: number;
  players_used: number;
  totals: OpponentLine;
}

export interface FallbackSpreadPlayer {
  nba_player_id: string;
  name: string;
  stats: SimStat[];
}

export interface WeeklyOutlookResponse {
  status: WeeklyOutlookStatus;
  window: WatchlistWindow;
  roster_size: number;
  run: RunSummary | null;
  simulation: { n: number; seed: number; dependence_rho: number } | null;
  opponent: OutlookOpponent | null;
  categories: CategoryOutlook[];
  players: OutlookPlayer[];
  provenance: {
    model_version: string | null;
    predicted_at: string | null;
    fallback_spread_players: FallbackSpreadPlayer[];
  };
}

export function parseOutlookDays(raw: unknown): number | null {
  if (raw === undefined || raw === null || raw === '') return DEFAULT_OUTLOOK_DAYS;
  if (typeof raw !== 'string' && typeof raw !== 'number') return null;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1 || value > MAX_WINDOW_DAYS) return null;
  return value;
}

export interface RosterRow {
  player_id: unknown;
  nba_id: unknown;
  name: unknown;
}

export async function fetchRoster(userId: number): Promise<RosterRow[]> {
  return rowsOrEmpty<RosterRow>(() =>
    query(
      `SELECT mr.player_id, p.nba_id, p.name
       FROM my_roster mr
       JOIN players p ON p.id = mr.player_id
       WHERE mr.user_id = $1
       ORDER BY p.name`,
      [userId]
    )
  );
}

export type RosterPredictionRow = UpcomingPredictionRow & { nba_player_id: unknown };

const ROSTER_STATS = [PROB_ACTIVE_STAT, ...SIM_STATS];

export async function fetchRosterPredictions(
  runId: number,
  nbaIds: string[],
  window: WatchlistWindow
): Promise<RosterPredictionRow[]> {
  return rowsOrEmpty<RosterPredictionRow>(() =>
    query(
      `SELECT pgp.nba_player_id,
              pgp.nba_game_id,
              pgp.game_date,
              NULL AS home_team_abbr,
              NULL AS away_team_abbr,
              NULL AS game_status,
              pgp.stat,
              pgp.quantile::float AS quantile,
              pgp.value::float AS value,
              pgp.conditional
       FROM player_game_predictions pgp
       JOIN prediction_runs pr ON pr.id = pgp.prediction_run_id
       WHERE pgp.prediction_run_id = $1
         AND pr.channel = $2
         AND pgp.nba_player_id = ANY($3)
         AND pgp.game_date >= $4
         AND pgp.game_date <= $5
         AND pgp.stat = ANY($6)
       ORDER BY pgp.nba_player_id, pgp.game_date, pgp.nba_game_id`,
      [runId, PRODUCTION_CHANNEL, nbaIds, window.from, window.to, ROSTER_STATS]
    )
  );
}

export type PoolRow = { nba_player_id: unknown } & { [K in SimStat]: unknown };

const POOL_PARAM_OFFSET = 5;

const POOL_PIVOT_SQL = SIM_STATS.map(
  (stat, i) =>
    `SUM(CASE WHEN pgp.stat = $${POOL_PARAM_OFFSET + i} THEN pgp.value END)::float AS ${stat}`
).join(',\n              ');

// unconditional expectations already price availability in, so they sum straight into a week.
export async function fetchPoolWeeklyTotals(runId: number, window: WatchlistWindow): Promise<PoolRow[]> {
  return rowsOrEmpty<PoolRow>(() =>
    query(
      `SELECT pgp.nba_player_id,
              ${POOL_PIVOT_SQL}
       FROM player_game_predictions pgp
       JOIN prediction_runs pr ON pr.id = pgp.prediction_run_id
       WHERE pgp.prediction_run_id = $1
         AND pr.channel = $2
         AND pgp.game_date >= $3
         AND pgp.game_date <= $4
         AND pgp.quantile IS NULL
       GROUP BY pgp.nba_player_id`,
      [runId, PRODUCTION_CHANNEL, window.from, window.to, ...SIM_STATS.map(uncondStat)]
    )
  );
}

export function toSimPlayer(nbaPlayerId: string, rows: UpcomingPredictionRow[]): SimPlayer {
  const games: SimGame[] = pivotUpcomingRows(rows, null).map((game) => {
    const stats: Partial<Record<SimStat, StatInput>> = {};
    for (const stat of SIM_STATS) {
      const line = game.stats[stat];
      if (!line || line.expected === null) continue;
      stats[stat] = { expected: line.expected, p10: line.p10, p50: line.p50, p90: line.p90 };
    }
    return {
      nba_game_id: game.nba_game_id,
      game_date: game.game_date,
      prob_active: game.prob_active,
      stats,
    };
  });
  return { nba_player_id: nbaPlayerId, games };
}

export function toImpactInput(row: PoolRow): ImpactInput {
  return {
    pts: num(row.pts),
    reb: num(row.reb),
    ast: num(row.ast),
    stl: num(row.stl),
    blk: num(row.blk),
    tov: num(row.tov),
    fg3m: num(row.fg3m),
    fgm: num(row.fgm),
    fga: num(row.fga),
    ftm: num(row.ftm),
    fta: num(row.fta),
  };
}

function roundCategory(outlook: CategoryOutlook): CategoryOutlook {
  const digits = outlook.category === 'fg_pct' || outlook.category === 'ft_pct' ? 4 : 2;
  return {
    ...outlook,
    mean: round(outlook.mean, digits),
    p10: round(outlook.p10, digits),
    p50: round(outlook.p50, digits),
    p90: round(outlook.p90, digits),
    opponent: round(outlook.opponent, digits),
    win_probability: round(outlook.win_probability, 3),
  };
}

function roundOpponent(totals: OpponentLine): OpponentLine {
  const out = {} as OpponentLine;
  for (const category of OUTLOOK_CATEGORIES) {
    const digits = category === 'fg_pct' || category === 'ft_pct' ? 4 : 2;
    out[category] = round(totals[category], digits);
  }
  return out;
}

export async function getWeeklyOutlook(
  userId: number,
  start: string,
  days: number = DEFAULT_OUTLOOK_DAYS
): Promise<WeeklyOutlookResponse> {
  const window = windowRange(start, days);
  const roster = await fetchRoster(userId);

  const base = (
    status: WeeklyOutlookStatus,
    run: RunSummary | null,
    players: OutlookPlayer[]
  ): WeeklyOutlookResponse => ({
    status,
    window,
    roster_size: roster.length,
    run,
    simulation: null,
    opponent: null,
    categories: [],
    players,
    provenance: {
      model_version: run?.model_version ?? null,
      predicted_at: run?.predicted_at ?? null,
      fallback_spread_players: [],
    },
  });

  const rosterPlayers = roster.map((row) => {
    const nbaId = row.nba_id === null || row.nba_id === undefined ? null : String(row.nba_id);
    return {
      player_id: Number(row.player_id),
      nba_player_id: nbaId,
      name: resolvePlayerName(row.name, nbaId ?? String(row.player_id)).name,
    };
  });
  const idle = (): OutlookPlayer[] =>
    rosterPlayers.map((player) => ({
      ...player,
      games_scheduled: 0,
      expected_games: 0,
      miss_risk: 0,
      simulated_miss_risk: 0,
      fallback_stats: [],
    }));

  if (roster.length === 0) return base('empty_roster', null, []);

  const run = await getLatestCompleteRun();
  if (!run) return base('no_run', null, idle());
  const runSummary: RunSummary = { model_version: run.model_version, predicted_at: run.predicted_at };

  const nbaIds = rosterPlayers
    .map((player) => player.nba_player_id)
    .filter((id): id is string => id !== null);
  const rows = nbaIds.length > 0 ? await fetchRosterPredictions(run.id, nbaIds, window) : [];
  if (rows.length === 0) return base('no_games', runSummary, idle());

  const byPlayer = new Map<string, RosterPredictionRow[]>();
  for (const row of rows) {
    const id = String(row.nba_player_id);
    const list = byPlayer.get(id) ?? [];
    list.push(row);
    byPlayer.set(id, list);
  }
  const simPlayers: SimPlayer[] = rosterPlayers.map((player) =>
    toSimPlayer(
      player.nba_player_id ?? `roster-${player.player_id}`,
      player.nba_player_id ? byPlayer.get(player.nba_player_id) ?? [] : []
    )
  );

  const pool = (await fetchPoolWeeklyTotals(run.id, window)).map(toImpactInput);
  const opponent = typicalOpponent(pool, roster.length);
  const result = simulateWeek(simPlayers, opponent?.totals ?? null);

  const players: OutlookPlayer[] = rosterPlayers.map((player, i) => {
    const sim = result.players[i];
    return {
      ...player,
      games_scheduled: sim.games_scheduled,
      expected_games: round(sim.expected_games, 2) ?? 0,
      miss_risk: round(sim.miss_risk, 3) ?? 0,
      simulated_miss_risk: round(sim.simulated_miss_risk, 3) ?? 0,
      fallback_stats: sim.fallback_stats,
    };
  });

  return {
    status: 'ok',
    window,
    roster_size: roster.length,
    run: runSummary,
    simulation: { n: result.n, seed: result.seed, dependence_rho: DEPENDENCE_RHO },
    opponent: opponent
      ? {
          definition: OPPONENT_DEFINITION,
          league_teams: TYPICAL_LEAGUE_TEAMS,
          pool_size: opponent.pool_size,
          players_used: opponent.players_used,
          totals: roundOpponent(opponent.totals),
        }
      : null,
    categories: result.categories.map(roundCategory),
    players,
    provenance: {
      model_version: runSummary.model_version,
      predicted_at: runSummary.predicted_at,
      fallback_spread_players: players.flatMap((player) =>
        player.nba_player_id !== null && player.fallback_stats.length > 0
          ? [{ nba_player_id: player.nba_player_id, name: player.name, stats: player.fallback_stats }]
          : []
      ),
    },
  };
}
