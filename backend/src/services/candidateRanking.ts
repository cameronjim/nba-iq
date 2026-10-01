import { categoryZScores, type CategoryStatLine } from './fantasyScore.js';
import {
  IMPACT_CATEGORIES,
  PROJECTED_STATS,
  REVERSED_IMPACT_CATEGORIES,
  categoryValues,
  poolRates,
  round,
  type ImpactCategory,
  type ImpactInput,
  type ProjectedStat,
} from './slate.js';

export const DEFAULT_WAIVER_LIMIT = 25;
export const DEFAULT_TRADE_LIMIT = 20;
export const DRIVERS_SHOWN = 3;

export const CATEGORY_LABELS: Record<ImpactCategory, string> = {
  pts: 'PTS',
  reb: 'REB',
  ast: 'AST',
  stl: 'STL',
  blk: 'BLK',
  fg3m: '3PM',
  fg: 'FG%',
  ft: 'FT%',
  tov: 'TO',
};

export interface RankingPlayer extends CategoryStatLine {
  id: number;
  nba_id: string | null;
  name: string;
  team: string | null;
  position: string | null;
}

export interface PlayerWindowProjection {
  games: number;
  totals: Record<ProjectedStat, number | null>;
  mean_prob_active: number | null;
}

export interface RankingOptions {
  scheduledGames: ReadonlyMap<string, number>;
  population?: RankingPlayer[];
  rosteredPool?: RankingPlayer[];
  limit?: number;
}

export type CandidateBasis = 'projection' | 'season_average';

export interface CategoryDriver {
  category: ImpactCategory;
  value: number;
}

export interface RankedCandidate {
  id: number;
  nba_id: string | null;
  name: string;
  team: string | null;
  position: string | null;
  score: number;
  drivers: CategoryDriver[];
  projected_games: number | null;
  mean_prob_play: number | null;
  basis: CandidateBasis;
}

interface WindowLine {
  input: ImpactInput;
  games: number | null;
  prob: number | null;
  basis: CandidateBasis;
}

function seasonInput(player: RankingPlayer, games: number): ImpactInput {
  return {
    pts: player.points_per_game * games,
    reb: player.rebounds_per_game * games,
    ast: player.assists_per_game * games,
    stl: player.steals_per_game * games,
    blk: player.blocks_per_game * games,
    tov: player.turnovers_per_game * games,
    fg3m: player.three_pointers_made * games,
    // season rows carry percentages but no attempts, so fg% and ft% stay neutral for this basis
    fgm: null,
    fga: null,
    ftm: null,
    fta: null,
  };
}

function windowLine(
  player: RankingPlayer,
  projections: ReadonlyMap<string, PlayerWindowProjection>,
  scheduledGames: ReadonlyMap<string, number>
): WindowLine {
  const projected = player.nba_id ? projections.get(player.nba_id) : undefined;
  if (projected && projected.games > 0) {
    const input = {} as ImpactInput;
    for (const stat of PROJECTED_STATS) input[stat] = projected.totals[stat];
    return { input, games: projected.games, prob: projected.mean_prob_active, basis: 'projection' };
  }

  // an empty schedule means the window is unknown (offseason, schedule not loaded), so rank per game rather than zero everyone out
  if (scheduledGames.size === 0) {
    return { input: seasonInput(player, 1), games: null, prob: null, basis: 'season_average' };
  }
  const games = (player.team ? scheduledGames.get(player.team) : undefined) ?? 0;
  return { input: seasonInput(player, games), games, prob: null, basis: 'season_average' };
}

// abramowitz and stegun 7.1.26, max error 1.5e-7
function normalCdf(x: number): number {
  const t = 1 / (1 + 0.3275911 * (Math.abs(x) / Math.SQRT2));
  const poly =
    t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))));
  const erf = 1 - poly * Math.exp(-(x * x) / 2);
  return x >= 0 ? (1 + erf) / 2 : (1 - erf) / 2;
}

function uniqueById(players: RankingPlayer[]): RankingPlayer[] {
  const seen = new Map<number, RankingPlayer>();
  for (const p of players) if (!seen.has(p.id)) seen.set(p.id, p);
  return [...seen.values()];
}

function rankCandidates(
  roster: RankingPlayer[],
  pool: RankingPlayer[],
  projections: ReadonlyMap<string, PlayerWindowProjection>,
  options: RankingOptions,
  defaultLimit: number
): RankedCandidate[] {
  const rosterIds = new Set(roster.map((p) => p.id));
  const candidates = uniqueById(pool).filter((p) => !rosterIds.has(p.id));
  if (candidates.length === 0) return [];

  const population = uniqueById(options.population ?? [...roster, ...candidates]);
  const rostered = options.rosteredPool ? uniqueById(options.rosteredPool) : [];
  const everyone = uniqueById([...population, ...roster, ...candidates, ...rostered]);

  const lines = new Map<number, WindowLine>();
  for (const p of everyone) lines.set(p.id, windowLine(p, projections, options.scheduledGames));
  const lineOf = (p: RankingPlayer): WindowLine => lines.get(p.id) as WindowLine;

  const rates = poolRates(population.map((p) => lineOf(p).input));
  const valuesOf = (players: RankingPlayer[]): Array<Record<ImpactCategory, number | null>> =>
    players.map((p) => categoryValues(lineOf(p).input, rates));
  const reference = valuesOf(population);
  const zOf = (players: RankingPlayer[]): Array<Record<ImpactCategory, number>> =>
    categoryZScores(valuesOf(players), IMPACT_CATEGORIES, REVERSED_IMPACT_CATEGORIES, reference);

  const rosterZ = zOf(roster);
  const rosteredZ = zOf(rostered);
  const candidateZ = zOf(candidates);

  // margin is the roster's category total against a typical opponent built from the rostered tier; spread is the noise of a sum of z-scores on each side
  const size = roster.length;
  const spread = Math.sqrt(2 * Math.max(size, 1));
  const margin = {} as Record<ImpactCategory, number>;
  for (const cat of IMPACT_CATEGORIES) {
    const own = rosterZ.reduce((s, z) => s + z[cat], 0);
    const typical =
      rosteredZ.length === 0 ? 0 : (rosteredZ.reduce((s, z) => s + z[cat], 0) / rosteredZ.length) * size;
    margin[cat] = own - typical;
  }

  const ranked = candidates.map((player, i): RankedCandidate => {
    const gains = IMPACT_CATEGORIES.map((cat) => ({
      category: cat,
      value:
        normalCdf((margin[cat] + candidateZ[i][cat]) / spread) - normalCdf(margin[cat] / spread),
    }));
    const total = gains.reduce((s, g) => s + g.value, 0);
    const drivers = [...gains]
      .sort((a, b) => b.value - a.value)
      .slice(0, DRIVERS_SHOWN)
      .map((g) => ({ category: g.category, value: round(g.value, 3) as number }));
    const line = lineOf(player);

    return {
      id: player.id,
      nba_id: player.nba_id,
      name: player.name,
      team: player.team,
      position: player.position,
      score: round(total, 3) as number,
      drivers,
      projected_games: line.games,
      mean_prob_play: line.prob,
      basis: line.basis,
    };
  });

  ranked.sort((a, b) => b.score - a.score || a.id - b.id);
  return ranked.slice(0, options.limit ?? defaultLimit);
}

export function rankWaiverCandidates(
  roster: RankingPlayer[],
  pool: RankingPlayer[],
  projections: ReadonlyMap<string, PlayerWindowProjection>,
  options: RankingOptions
): RankedCandidate[] {
  return rankCandidates(roster, pool, projections, options, DEFAULT_WAIVER_LIMIT);
}

export function rankTradeTargets(
  roster: RankingPlayer[],
  pool: RankingPlayer[],
  projections: ReadonlyMap<string, PlayerWindowProjection>,
  options: RankingOptions
): RankedCandidate[] {
  return rankCandidates(roster, pool, projections, options, DEFAULT_TRADE_LIMIT);
}
