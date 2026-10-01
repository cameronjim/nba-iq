import { query } from '../db.js';
import { num, rowsOrEmpty, toIsoDay } from './slate.js';


export const BASELINE_STATS = [
  'minutes',
  'pts',
  'reb',
  'ast',
  'stl',
  'blk',
  'fg3m',
  'fga',
] as const;

export type BaselineStat = (typeof BASELINE_STATS)[number];

export const BASELINE_WINDOW_GAMES = 15;

export const MIN_BASELINE_GAMES = 5;

export const BASELINE_RECENT_GAMES = 5;

export const BASELINE_LOOKBACK_DAYS = 400;

export const NOTABLE_MINUTES_DELTA = 4;

export const BASELINE_LABEL = 'his own recent form';
export const BASELINE_DEFINITION =
  `per-game averages over his last ${BASELINE_WINDOW_GAMES} games played before this date, ` +
  `requiring at least ${MIN_BASELINE_GAMES}`;

export interface BaselineDescriptor {
  window_games: number;
  min_games: number;
  notable_min_delta: number;
  label: string;
  definition: string;
}

export function baselineDescriptor(): BaselineDescriptor {
  return {
    window_games: BASELINE_WINDOW_GAMES,
    min_games: MIN_BASELINE_GAMES,
    notable_min_delta: NOTABLE_MINUTES_DELTA,
    label: BASELINE_LABEL,
    definition: BASELINE_DEFINITION,
  };
}

export interface PlayerBaseline {
  nba_player_id: string;
  games: number;
  avg: Record<BaselineStat, number | null>;
  pts_recent: number | null;
  pts_sd: number | null;
  last_played_date: string | null;
}

export function hasUsableBaseline(baseline: PlayerBaseline | undefined): boolean {
  return baseline !== undefined && baseline.games >= MIN_BASELINE_GAMES;
}

export function deltaOf(projected: number | null, usual: number | null): number | null {
  if (projected === null || usual === null) return null;
  return projected - usual;
}

export function daysSince(date: string, lastPlayed: string | null): number | null {
  if (!lastPlayed) return null;
  const a = Date.parse(`${date}T00:00:00Z`);
  const b = Date.parse(`${lastPlayed}T00:00:00Z`);
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.round((a - b) / 86_400_000);
}

const LOG_COLUMNS_SQL = BASELINE_STATS.map((stat) => `g.${stat}::float AS ${stat}`).join(',\n              ');
const LOG_AVERAGES_SQL = BASELINE_STATS.map((stat) => `AVG(${stat}) AS ${stat}`).join(',\n              ');

interface BaselineRow {
  nba_player_id: unknown;
  games: unknown;
  pts_recent: unknown;
  pts_sd: unknown;
  last_played_date: unknown;
}

export async function fetchBaselines(
  date: string,
  nbaPlayerIds: string[] | null = null
): Promise<Map<string, PlayerBaseline>> {
  const playerFilter =
    nbaPlayerIds === null ? '' : '\n             AND g.nba_player_id = ANY($5)';
  const rows = await rowsOrEmpty<BaselineRow & Record<BaselineStat, unknown>>(() =>
    query(
      `WITH played AS (
           SELECT g.nba_player_id,
                  g.game_date,
                  g.nba_game_id,
                  ${LOG_COLUMNS_SQL},
                  ROW_NUMBER() OVER (
                    PARTITION BY g.nba_player_id
                    ORDER BY g.game_date DESC, g.nba_game_id DESC
                  ) AS rn
           FROM player_game_logs g
           WHERE g.season_type = 'Regular Season'
             AND g.game_date < $1
             AND g.game_date >= $1::date - $2::int
             AND g.minutes IS NOT NULL
             AND g.minutes > 0${playerFilter}
         )
         SELECT nba_player_id,
                COUNT(*)::int AS games,
                ${LOG_AVERAGES_SQL},
                AVG(pts) FILTER (WHERE rn <= $4) AS pts_recent,
                STDDEV_POP(pts) AS pts_sd,
                MAX(game_date) FILTER (WHERE rn = 1) AS last_played_date
         FROM played
         WHERE rn <= $3
         GROUP BY nba_player_id`,
      [
        date,
        BASELINE_LOOKBACK_DAYS,
        BASELINE_WINDOW_GAMES,
        BASELINE_RECENT_GAMES,
        ...(nbaPlayerIds === null ? [] : [nbaPlayerIds]),
      ]
    )
  );

  const map = new Map<string, PlayerBaseline>();
  for (const row of rows) {
    const id = String(row.nba_player_id);
    const avg = {} as Record<BaselineStat, number | null>;
    for (const stat of BASELINE_STATS) avg[stat] = num(row[stat]);
    map.set(id, {
      nba_player_id: id,
      games: num(row.games) ?? 0,
      avg,
      pts_recent: num(row.pts_recent),
      pts_sd: num(row.pts_sd),
      last_played_date: toIsoDay(row.last_played_date),
    });
  }
  return map;
}
