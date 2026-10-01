import { query } from '../db.js';


export interface FantasyStatLine {
  points_per_game: number;
  rebounds_per_game: number;
  assists_per_game: number;
  steals_per_game: number;
  blocks_per_game: number;
  three_pointers_made: number;
  turnovers_per_game: number;
}

export interface FantasyStatLineExtras {
  field_goals_made?: number;
  field_goals_attempted?: number;
  free_throws_made?: number;
  free_throws_attempted?: number;
  double_doubles_per_game?: number;
  triple_doubles_per_game?: number;
}

export interface ScoringFormat {
  name: string;
  pts: number;
  reb: number;
  ast: number;
  stl: number;
  blk: number;
  tov: number;
  threePM: number;
  fgm: number;
  fga: number;
  ftm: number;
  fta: number;
  doubleDoubleBonus: number;
  tripleDoubleBonus: number;
}

const ZERO_COEFFS = {
  pts: 0, reb: 0, ast: 0, stl: 0, blk: 0, tov: 0,
  threePM: 0, fgm: 0, fga: 0, ftm: 0, fta: 0,
  doubleDoubleBonus: 0, tripleDoubleBonus: 0,
} as const;

export const NBA_STANDARD: ScoringFormat = {
  ...ZERO_COEFFS,
  name: 'NBA Standard',
  pts: 1.0, reb: 1.2, ast: 1.5, stl: 3.0, blk: 3.0, tov: -1.0,
};

export const FANDUEL: ScoringFormat = {
  ...ZERO_COEFFS,
  name: 'FanDuel',
  pts: 1.0, reb: 1.2, ast: 1.5, stl: 3.0, blk: 3.0, tov: -1.0,
};

export const DRAFTKINGS: ScoringFormat = {
  ...ZERO_COEFFS,
  name: 'DraftKings',
  pts: 1.0, threePM: 0.5, reb: 1.25, ast: 1.5, stl: 2.0, blk: 2.0, tov: -0.5,
  doubleDoubleBonus: 1.5, tripleDoubleBonus: 3.0,
};

export const ESPN_DEFAULT: ScoringFormat = {
  ...ZERO_COEFFS,
  name: 'ESPN H2H Points',
  pts: 1.0, threePM: 1.0, reb: 1.0, ast: 2.0, stl: 4.0, blk: 4.0, tov: -2.0,
  fgm: 2.0, fga: -1.0, ftm: 1.0, fta: -1.0,
};

export const YAHOO_HIGH_SCORE: ScoringFormat = {
  ...ZERO_COEFFS,
  name: 'Yahoo High Score',
  pts: 1.0, reb: 1.0, ast: 2.0, stl: 3.0, blk: 3.0, tov: 0,
};

export const APP_LEGACY: ScoringFormat = {
  ...ZERO_COEFFS,
  name: 'App Legacy (NBA + 3PM bonus)',
  pts: 1.0, reb: 1.2, ast: 1.5, stl: 3.0, blk: 3.0, tov: -1.0, threePM: 1.0,
};

export const SCORING_FORMATS: Readonly<Record<string, ScoringFormat>> = Object.freeze({
  nba_standard: NBA_STANDARD,
  fanduel: FANDUEL,
  draftkings: DRAFTKINGS,
  espn: ESPN_DEFAULT,
  yahoo: YAHOO_HIGH_SCORE,
  app_legacy: APP_LEGACY,
});

export function fantasyPoints(
  p: FantasyStatLine,
  format: ScoringFormat = NBA_STANDARD,
  extras: FantasyStatLineExtras = {}
): number {
  return (
    p.points_per_game        * format.pts +
    p.rebounds_per_game      * format.reb +
    p.assists_per_game       * format.ast +
    p.steals_per_game        * format.stl +
    p.blocks_per_game        * format.blk +
    p.turnovers_per_game     * format.tov +
    p.three_pointers_made    * format.threePM +
    (extras.field_goals_made       ?? 0) * format.fgm +
    (extras.field_goals_attempted  ?? 0) * format.fga +
    (extras.free_throws_made       ?? 0) * format.ftm +
    (extras.free_throws_attempted  ?? 0) * format.fta +
    (extras.double_doubles_per_game ?? 0) * format.doubleDoubleBonus +
    (extras.triple_doubles_per_game ?? 0) * format.tripleDoubleBonus
  );
}

export const MIN_GAMES_FOR_RANK = 15;
export const MIN_MIN_FOR_RANK = 12;

export function scorePlayer(
  stats: FantasyStatLine & { games_played: number; minutes_per_game: number },
  format: ScoringFormat = NBA_STANDARD,
  extras: FantasyStatLineExtras = {}
): number | null {
  if (stats.games_played < MIN_GAMES_FOR_RANK) return null;
  if (stats.minutes_per_game < MIN_MIN_FOR_RANK) return null;
  return Math.round(fantasyPoints(stats, format, extras) * 10) / 10;
}

export interface CategoryStatLine extends FantasyStatLine {
  field_goal_percentage: number;
  free_throw_percentage: number;
}

const CATEGORY_STAT_KEYS = [
  'points_per_game',
  'rebounds_per_game',
  'assists_per_game',
  'steals_per_game',
  'blocks_per_game',
  'three_pointers_made',
  'field_goal_percentage',
  'free_throw_percentage',
  'turnovers_per_game',
] as const satisfies ReadonlyArray<keyof CategoryStatLine>;

const REVERSED_CATEGORY_STAT_KEYS: ReadonlySet<keyof CategoryStatLine> = new Set<keyof CategoryStatLine>([
  'turnovers_per_game',
]);

// shape (mean, population sd) comes from `reference`, so a candidate can be scored against a pool it is not part of; a null value is neutral and stays out of the shape
export function categoryZScores<K extends string>(
  rows: ReadonlyArray<Partial<Record<K, number | null>>>,
  categories: readonly K[],
  reversed: ReadonlySet<K> = new Set<K>(),
  reference: ReadonlyArray<Partial<Record<K, number | null>>> = rows
): Array<Record<K, number>> {
  const mean = (xs: number[]): number => xs.reduce((s, x) => s + x, 0) / xs.length;
  const stddev = (xs: number[]): number => {
    const m = mean(xs);
    return Math.sqrt(mean(xs.map((x) => (x - m) ** 2)));
  };
  const present = (value: number | null | undefined): value is number =>
    value !== null && value !== undefined && Number.isFinite(value);

  const shape = new Map<K, { m: number; sd: number }>();
  for (const cat of categories) {
    const values = reference.map((r) => r[cat]).filter(present);
    if (values.length > 0) shape.set(cat, { m: mean(values), sd: stddev(values) });
  }

  return rows.map((row) => {
    const out = {} as Record<K, number>;
    for (const cat of categories) {
      const value = row[cat];
      const s = shape.get(cat);
      const z = !present(value) || !s || s.sd === 0 ? 0 : (value - s.m) / s.sd;
      out[cat] = reversed.has(cat) ? -z : z;
    }
    return out;
  });
}

export function zScoreRank<T extends CategoryStatLine>(
  players: T[]
): Array<T & { z_score: number }> {
  if (players.length === 0) return [];

  const zs = categoryZScores<keyof CategoryStatLine>(
    players,
    CATEGORY_STAT_KEYS,
    REVERSED_CATEGORY_STAT_KEYS
  );

  return players.map((p, i) => {
    let total = 0;
    for (const cat of CATEGORY_STAT_KEYS) total += zs[i][cat];
    return { ...p, z_score: total / CATEGORY_STAT_KEYS.length };
  });
}

export interface PlayerWithScore {
  id: number;
  nba_id: string | null;
  name: string;
  team: string | null;
  position: string | null;
  points_per_game: number;
  rebounds_per_game: number;
  assists_per_game: number;
  steals_per_game: number;
  blocks_per_game: number;
  field_goal_percentage: number;
  free_throw_percentage: number;
  three_point_percentage: number;
  three_pointers_made: number;
  turnovers_per_game: number;
  minutes_per_game: number;
  games_played: number;
  injury_status: string | null;
  injury_detail: string | null;
  headshot_url: string | null;
  fantasy_score: number | null;
  fantasy_rank: number | null;
}

interface CacheEntry {
  ranked: PlayerWithScore[];
  byId: Map<number, PlayerWithScore>;
  fetchedAt: number;
}

let cache: CacheEntry | null = null;
const TTL_MS = 60 * 60 * 1000;

async function compute(): Promise<{ ranked: PlayerWithScore[]; byId: Map<number, PlayerWithScore> }> {
  const all = await query(`
    SELECT id, nba_id, name, team, position,
           points_per_game::float    AS points_per_game,
           rebounds_per_game::float  AS rebounds_per_game,
           assists_per_game::float   AS assists_per_game,
           steals_per_game::float    AS steals_per_game,
           blocks_per_game::float    AS blocks_per_game,
           field_goal_percentage::float    AS field_goal_percentage,
           free_throw_percentage::float    AS free_throw_percentage,
           three_point_percentage::float   AS three_point_percentage,
           three_pointers_made::float      AS three_pointers_made,
           turnovers_per_game::float       AS turnovers_per_game,
           minutes_per_game::float         AS minutes_per_game,
           games_played::int               AS games_played,
           injury_status, injury_detail, headshot_url
    FROM players
  `);

  const rows = all.rows as Array<Record<string, unknown>>;

  const scored: PlayerWithScore[] = rows.map((p) => {
    const gp = Number(p.games_played) || 0;
    const mpg = Number(p.minutes_per_game) || 0;

    const stats = {
      points_per_game: Number(p.points_per_game) || 0,
      rebounds_per_game: Number(p.rebounds_per_game) || 0,
      assists_per_game: Number(p.assists_per_game) || 0,
      steals_per_game: Number(p.steals_per_game) || 0,
      blocks_per_game: Number(p.blocks_per_game) || 0,
      three_pointers_made: Number(p.three_pointers_made) || 0,
      turnovers_per_game: Number(p.turnovers_per_game) || 0,
    };

    const score = scorePlayer({ ...stats, games_played: gp, minutes_per_game: mpg });

    return {
      id: Number(p.id),
      nba_id: (p.nba_id as string) ?? null,
      name: String(p.name ?? ''),
      team: (p.team as string) ?? null,
      position: (p.position as string) ?? null,
      ...stats,
      field_goal_percentage: Number(p.field_goal_percentage) || 0,
      free_throw_percentage: Number(p.free_throw_percentage) || 0,
      three_point_percentage: Number(p.three_point_percentage) || 0,
      minutes_per_game: mpg,
      games_played: gp,
      injury_status: (p.injury_status as string) ?? null,
      injury_detail: (p.injury_detail as string) ?? null,
      headshot_url: (p.headshot_url as string) ?? null,
      fantasy_score: score,
      fantasy_rank: null,
    };
  });

  const rankable = scored.filter((p) => p.fantasy_score !== null);
  rankable.sort((a, b) => (b.fantasy_score ?? 0) - (a.fantasy_score ?? 0));
  rankable.forEach((p, i) => { p.fantasy_rank = i + 1; });

  const byId = new Map<number, PlayerWithScore>();
  scored.forEach((p) => byId.set(p.id, p));

  const unranked = scored.filter((p) => p.fantasy_score === null);
  return { ranked: [...rankable, ...unranked], byId };
}

async function load(): Promise<CacheEntry> {
  if (cache && Date.now() - cache.fetchedAt < TTL_MS) return cache;
  const { ranked, byId } = await compute();
  cache = { ranked, byId, fetchedAt: Date.now() };
  return cache;
}

export async function getRankedPlayers(): Promise<PlayerWithScore[]> {
  return (await load()).ranked;
}

export async function getScoresById(): Promise<Map<number, PlayerWithScore>> {
  return (await load()).byId;
}
