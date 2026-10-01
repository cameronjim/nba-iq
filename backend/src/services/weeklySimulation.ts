import { impactScores, poolRates, type ImpactInput, type ProjectedStat } from './slate.js';
import { P10, P50, P90, quantileAt, type QuantileAnchors } from './quantileDistribution.js';

export { quantileAt, type QuantileAnchors };

export const SIM_STATS = [
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
] as const satisfies readonly ProjectedStat[];

export type SimStat = (typeof SIM_STATS)[number];

export const COUNTING_CATEGORIES = ['pts', 'reb', 'ast', 'stl', 'blk', 'fg3m', 'tov'] as const;

export type CountingCategory = (typeof COUNTING_CATEGORIES)[number];

export const RATIO_CATEGORIES = {
  fg_pct: ['fgm', 'fga'],
  ft_pct: ['ftm', 'fta'],
} as const satisfies Record<string, readonly [SimStat, SimStat]>;

export type RatioCategory = keyof typeof RATIO_CATEGORIES;

export type OutlookCategory = CountingCategory | RatioCategory;

export const OUTLOOK_CATEGORIES: readonly OutlookCategory[] = [
  'pts',
  'reb',
  'ast',
  'stl',
  'blk',
  'fg3m',
  'fg_pct',
  'ft_pct',
  'tov',
];

export const LOWER_IS_BETTER: ReadonlySet<OutlookCategory> = new Set<OutlookCategory>(['tov']);

// hand-set, not fitted: the share of player-weeks whose games share one availability draw.
export const DEPENDENCE_RHO = 0.5;

export const SIMULATION_COUNT = 2000;

export const DEFAULT_SEED = 20260930;

// hand-set p10/p90 half-width as a share of the expectation, used only when the store has no quantiles.
export const FALLBACK_RELATIVE_SPREAD: Record<SimStat, number> = {
  pts: 0.45,
  reb: 0.5,
  ast: 0.6,
  stl: 0.9,
  blk: 1.0,
  tov: 0.7,
  fg3m: 0.9,
  fgm: 0.45,
  fga: 0.4,
  ftm: 0.7,
  fta: 0.65,
};

// an average team in a league this size rosters the top teams * roster-size players.
export const TYPICAL_LEAGUE_TEAMS = 12;

export const OPPONENT_DEFINITION =
  `the top ${TYPICAL_LEAGUE_TEAMS} x roster-size players in the window by summed slate impact, ` +
  'their mean expected weekly totals scaled to your roster size, percentages as ratio of sums (slate poolRates)';

export interface StatInput {
  expected: number;
  p10: number | null;
  p50: number | null;
  p90: number | null;
}

export interface SimGame {
  nba_game_id: string;
  game_date: string;
  prob_active: number | null;
  stats: Partial<Record<SimStat, StatInput>>;
}

export interface SimPlayer {
  nba_player_id: string;
  games: SimGame[];
}

export type Rng = () => number;

export type GameLine = Record<SimStat, number>;

export type OpponentLine = Record<OutlookCategory, number | null>;

export interface CategoryOutlook {
  category: OutlookCategory;
  lower_is_better: boolean;
  mean: number | null;
  p10: number | null;
  p50: number | null;
  p90: number | null;
  opponent: number | null;
  win_probability: number | null;
}

export interface PlayerSimulation {
  nba_player_id: string;
  games_scheduled: number;
  expected_games: number;
  miss_risk: number;
  simulated_miss_risk: number;
  fallback_stats: SimStat[];
}

export interface SimulationOptions {
  n?: number;
  seed?: number;
  rho?: number;
}

export interface SimulationResult {
  n: number;
  seed: number;
  rho: number;
  categories: CategoryOutlook[];
  players: PlayerSimulation[];
}

// mulberry32: tiny, seedable, and good enough for a 2000-draw monte carlo.
export function createRng(seed: number): Rng {
  let state = seed >>> 0;
  return (): number => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function clampProbability(value: number | null): number {
  // a missing prob_active means availability was not modelled, not that he is ruled out.
  if (value === null || !Number.isFinite(value)) return 1;
  return Math.min(1, Math.max(0, value));
}

export function resolveAnchors(
  stat: SimStat,
  input: StatInput
): { anchors: QuantileAnchors; fallback: boolean } {
  const { expected, p10, p50, p90 } = input;
  if (p10 !== null && p50 !== null && p90 !== null) {
    const sorted = [p10, p50, p90].sort((a, b) => a - b);
    return { anchors: { p10: sorted[0], p50: sorted[1], p90: sorted[2] }, fallback: false };
  }
  const width = FALLBACK_RELATIVE_SPREAD[stat];
  const center = Math.max(0, expected);
  return {
    anchors: { p10: Math.max(0, center * (1 - width)), p50: center, p90: center * (1 + width) },
    fallback: true,
  };
}

function relativeHalfWidth(anchors: QuantileAnchors): number {
  return anchors.p50 > 0 ? (anchors.p90 - anchors.p10) / (2 * anchors.p50) : 0;
}

// approximation: the rate's spread is what is left of the makes' spread after the attempts' spread, as if log makes = log attempts + log rate with the two independent.
function rateAnchors(made: QuantileAnchors, attempted: QuantileAnchors, rate: number): QuantileAnchors {
  const madeWidth = relativeHalfWidth(made);
  const attemptWidth = relativeHalfWidth(attempted);
  const width = Math.sqrt(Math.max(0, madeWidth ** 2 - attemptWidth ** 2));
  return { p10: rate * (1 - width), p50: rate, p90: rate * (1 + width) };
}

export interface PreparedStat {
  anchors: QuantileAnchors;
  expected: number;
}

export type PreparedGame = {
  prob_active: number;
  stats: Partial<Record<SimStat, PreparedStat>>;
};

export function prepareGame(game: SimGame, fallbacks: Set<SimStat>): PreparedGame {
  const stats: Partial<Record<SimStat, PreparedStat>> = {};
  for (const stat of SIM_STATS) {
    const input = game.stats[stat];
    if (!input || !Number.isFinite(input.expected)) continue;
    const { anchors, fallback } = resolveAnchors(stat, input);
    if (fallback) fallbacks.add(stat);
    stats[stat] = { anchors, expected: Math.max(0, input.expected) };
  }
  return { prob_active: clampProbability(game.prob_active), stats };
}

function drawRate(
  rng: Rng,
  made: PreparedStat,
  attempted: PreparedStat
): number {
  if (attempted.expected <= 0) return 0;
  const rate = Math.min(1, made.expected / attempted.expected);
  const jittered = quantileAt(rng(), rateAnchors(made.anchors, attempted.anchors, rate));
  return Math.min(1, Math.max(0, jittered));
}

// attempts are drawn first and makes are attempts times a jittered rate, so fgm <= fga, ftm <= fta and fg3m <= fgm hold in every draw.
export function drawGameLine(game: PreparedGame, rng: Rng): GameLine {
  const line = {} as GameLine;
  for (const stat of SIM_STATS) line[stat] = 0;
  const { stats } = game;

  for (const stat of ['pts', 'reb', 'ast', 'stl', 'blk', 'tov'] as const) {
    const prepared = stats[stat];
    if (prepared) line[stat] = quantileAt(rng(), prepared.anchors);
  }

  const fga = stats.fga;
  const fgm = stats.fgm;
  if (fga && fgm) {
    line.fga = quantileAt(rng(), fga.anchors);
    line.fgm = line.fga * drawRate(rng, fgm, fga);
    const fg3m = stats.fg3m;
    if (fg3m) line.fg3m = line.fgm * drawRate(rng, fg3m, fgm);
  } else if (stats.fg3m) {
    line.fg3m = quantileAt(rng(), stats.fg3m.anchors);
  }

  const fta = stats.fta;
  const ftm = stats.ftm;
  if (fta && ftm) {
    line.fta = quantileAt(rng(), fta.anchors);
    line.ftm = line.fta * drawRate(rng, ftm, fta);
  }

  return line;
}

// with probability rho one draw decides every game (comonotone), otherwise each game is independent.
export function missRisk(probs: number[], rho: number): number {
  if (probs.length === 0) return 0;
  const clamped = probs.map((p) => clampProbability(p));
  const dependent = Math.max(...clamped.map((p) => 1 - p));
  const independent = 1 - clamped.reduce((acc, p) => acc * p, 1);
  return rho * dependent + (1 - rho) * independent;
}

function drawAvailability(probs: number[], rho: number, rng: Rng): boolean[] {
  if (rng() < rho) {
    const shared = rng();
    return probs.map((p) => shared < p);
  }
  return probs.map((p) => rng() < p);
}

function percentile(sorted: number[], q: number): number | null {
  if (sorted.length === 0) return null;
  const position = (sorted.length - 1) * q;
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  const weight = position - lower;
  return sorted[lower] + (sorted[upper] - sorted[lower]) * weight;
}

function mean(values: number[]): number | null {
  if (values.length === 0) return null;
  let total = 0;
  for (const value of values) total += value;
  return total / values.length;
}

function winProbability(
  draws: number[],
  opponent: number | null,
  lowerIsBetter: boolean
): number | null {
  if (opponent === null || draws.length === 0) return null;
  let wins = 0;
  for (const value of draws) {
    if (value === opponent) wins += 0.5;
    else if (lowerIsBetter ? value < opponent : value > opponent) wins += 1;
  }
  return wins / draws.length;
}

export function simulateWeek(
  players: SimPlayer[],
  opponent: OpponentLine | null,
  options: SimulationOptions = {}
): SimulationResult {
  const n = options.n ?? SIMULATION_COUNT;
  const seed = options.seed ?? DEFAULT_SEED;
  const rho = options.rho ?? DEPENDENCE_RHO;
  const rng = createRng(seed);

  const prepared = players.map((player) => {
    const fallbacks = new Set<SimStat>();
    const games = player.games.map((game) => prepareGame(game, fallbacks));
    return { player, games, probs: games.map((g) => g.prob_active), fallbacks, misses: 0 };
  });

  const totals: Record<SimStat, number[]> = {} as Record<SimStat, number[]>;
  for (const stat of SIM_STATS) totals[stat] = new Array<number>(n).fill(0);

  for (let sim = 0; sim < n; sim += 1) {
    for (const entry of prepared) {
      if (entry.games.length === 0) continue;
      const plays = drawAvailability(entry.probs, rho, rng);
      if (plays.some((played) => !played)) entry.misses += 1;
      entry.games.forEach((game, i) => {
        if (!plays[i]) return;
        const line = drawGameLine(game, rng);
        for (const stat of SIM_STATS) totals[stat][sim] += line[stat];
      });
    }
  }

  const anyGames = prepared.some((entry) => entry.games.length > 0);

  const categories: CategoryOutlook[] = OUTLOOK_CATEGORIES.map((category) => {
    let draws: number[];
    if (category === 'fg_pct' || category === 'ft_pct') {
      const [made, attempted] = RATIO_CATEGORIES[category];
      draws = [];
      for (let sim = 0; sim < n; sim += 1) {
        const att = totals[attempted][sim];
        if (att > 0) draws.push(totals[made][sim] / att);
      }
    } else {
      draws = anyGames ? totals[category] : [];
    }
    const sorted = [...draws].sort((a, b) => a - b);
    const lowerIsBetter = LOWER_IS_BETTER.has(category);
    const opp = opponent ? opponent[category] : null;
    return {
      category,
      lower_is_better: lowerIsBetter,
      mean: mean(draws),
      p10: percentile(sorted, P10),
      p50: percentile(sorted, P50),
      p90: percentile(sorted, P90),
      opponent: opp,
      win_probability: winProbability(draws, opp, lowerIsBetter),
    };
  });

  const playerResults: PlayerSimulation[] = prepared.map((entry) => ({
    nba_player_id: entry.player.nba_player_id,
    games_scheduled: entry.games.length,
    expected_games: entry.probs.reduce((acc, p) => acc + p, 0),
    miss_risk: missRisk(entry.probs, rho),
    simulated_miss_risk: n === 0 ? 0 : entry.misses / n,
    fallback_stats: SIM_STATS.filter((stat) => entry.fallbacks.has(stat)),
  }));

  return { n, seed, rho, categories, players: playerResults };
}

export interface TypicalOpponent {
  pool_size: number;
  players_used: number;
  totals: OpponentLine;
}

// ranks the window's pool on summed weekly expectations, so a four-game week outranks a two-game one.
export function typicalOpponent(
  pool: ImpactInput[],
  rosterSize: number,
  leagueTeams: number = TYPICAL_LEAGUE_TEAMS
): TypicalOpponent | null {
  if (pool.length === 0 || rosterSize <= 0) return null;

  const scores = impactScores(pool);
  const ranked = pool
    .map((entry, i) => ({ entry, score: scores[i] }))
    .filter((row): row is { entry: ImpactInput; score: number } => row.score !== null)
    .sort((a, b) => b.score - a.score);
  if (ranked.length === 0) return null;

  const top = ranked.slice(0, leagueTeams * rosterSize).map((row) => row.entry);
  const rates = poolRates(top);
  const scaled = (stat: CountingCategory): number => {
    let total = 0;
    for (const entry of top) total += entry[stat] ?? 0;
    return (total / top.length) * rosterSize;
  };

  const totals: OpponentLine = {
    pts: scaled('pts'),
    reb: scaled('reb'),
    ast: scaled('ast'),
    stl: scaled('stl'),
    blk: scaled('blk'),
    fg3m: scaled('fg3m'),
    tov: scaled('tov'),
    fg_pct: rates.fg > 0 ? rates.fg : null,
    ft_pct: rates.ft > 0 ? rates.ft : null,
  };
  return { pool_size: pool.length, players_used: top.length, totals };
}
