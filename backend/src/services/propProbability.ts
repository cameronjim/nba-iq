import { americanToDecimal } from './oddsMath.js';
import { cdfAt, type QuantileAnchors } from './quantileDistribution.js';

export const PROP_MARKETS = ['pts', 'reb', 'ast', 'fg3m', 'pra', 'stl', 'blk', 'tov'] as const;

export type PropMarket = (typeof PROP_MARKETS)[number];

export type SingleStatMarket = Exclude<PropMarket, 'pra'>;

export const PRA_COMPONENTS = ['pts', 'reb', 'ast'] as const satisfies readonly SingleStatMarket[];

export const PROP_SIDES = ['over', 'under'] as const;

export type PropSide = (typeof PROP_SIDES)[number];

// dnp_void: the bet is refunded when he does not play; dnp_loss: it is graded a loss.
export type VoidRule = 'dnp_void' | 'dnp_loss';

export const DEFAULT_VOID_RULE: VoidRule = 'dnp_void';

export const KELLY_MULTIPLIER = 0.25;

export const KELLY_CAP = 0.02;

export function isPropMarket(value: unknown): value is PropMarket {
  return typeof value === 'string' && (PROP_MARKETS as readonly string[]).includes(value);
}

export interface StatDistribution {
  expected: number | null;
  p10: number | null;
  p50: number | null;
  p90: number | null;
}

export type PlayerGameStats = Partial<Record<string, StatDistribution>>;

export interface LineProbabilities {
  over: number;
  under: number;
  push: number;
}

export function anchorsFor(dist: StatDistribution | undefined): QuantileAnchors | null {
  if (!dist) return null;
  const { p10, p50, p90 } = dist;
  if (p10 === null || p50 === null || p90 === null) return null;
  if (![p10, p50, p90].every(Number.isFinite)) return null;
  const [low, mid, high] = [p10, p50, p90].sort((a, b) => a - b);
  return { p10: low, p50: mid, p90: high };
}

// approximation: the sum is centred on the summed expectations and each half-width is the quadrature sum of the components', i.e. independent components.
export function sumAnchors(components: StatDistribution[]): QuantileAnchors | null {
  let center = 0;
  let lowSq = 0;
  let highSq = 0;
  for (const dist of components) {
    const anchors = anchorsFor(dist);
    if (!anchors) return null;
    const expected = dist.expected !== null && Number.isFinite(dist.expected) ? dist.expected : anchors.p50;
    center += Math.max(0, expected);
    lowSq += (anchors.p50 - anchors.p10) ** 2;
    highSq += (anchors.p90 - anchors.p50) ** 2;
  }
  return { p10: center - Math.sqrt(lowSq), p50: center, p90: center + Math.sqrt(highSq) };
}

export function marketAnchors(market: PropMarket, stats: PlayerGameStats): QuantileAnchors | null {
  if (market === 'pra') {
    const parts = PRA_COMPONENTS.map((stat) => stats[stat]);
    if (parts.some((part) => part === undefined)) return null;
    return sumAnchors(parts.filter((part): part is StatDistribution => part !== undefined));
  }
  return anchorsFor(stats[market]);
}

// the stat is a count: P(X = k) is the continuous mass on [k - 0.5, k + 0.5].
export function lineProbabilities(line: number, anchors: QuantileAnchors): LineProbabilities {
  const over = 1 - cdfAt(Math.floor(line) + 0.5, anchors);
  const under = cdfAt(Math.ceil(line) - 0.5, anchors);
  const push = Number.isInteger(line) ? Math.max(0, 1 - over - under) : 0;
  return { over, under, push };
}

export function evPerDollar(winProb: number, pushProb: number, americanPrice: number): number {
  const profit = americanToDecimal(americanPrice) - 1;
  const lossProb = Math.max(0, 1 - winProb - pushProb);
  return winProb * profit - lossProb;
}

// full kelly with a push outcome is (b p - q) / (b (p + q)); a push returns the stake and drops out.
export function kellyFraction(winProb: number, pushProb: number, americanPrice: number): number {
  const profit = americanToDecimal(americanPrice) - 1;
  const lossProb = Math.max(0, 1 - winProb - pushProb);
  const decided = winProb + lossProb;
  if (profit <= 0 || decided <= 0) return 0;
  const full = (profit * winProb - lossProb) / (profit * decided);
  return Math.min(KELLY_CAP, Math.max(0, full * KELLY_MULTIPLIER));
}

export interface PropPriceInput {
  market: PropMarket;
  line: number;
  side: PropSide;
  price: number;
  stats: PlayerGameStats;
  probActive: number;
  voidRule?: VoidRule;
}

export interface PropPricing {
  void_rule: VoidRule;
  // P(side wins | he plays)
  model_prob_plays: number;
  push_prob_plays: number;
  // P(side wins) under the void rule: equal to model_prob_plays for dnp_void, times prob_active for dnp_loss
  model_prob: number;
  prob_active: number;
  // per $1 staked; under dnp_void it is conditional on the bet having action
  ev: number;
  kelly_fraction: number;
}

export function priceProp(input: PropPriceInput): PropPricing | null {
  const anchors = marketAnchors(input.market, input.stats);
  if (!anchors || !Number.isFinite(input.line) || !Number.isFinite(input.price)) return null;
  const voidRule = input.voidRule ?? DEFAULT_VOID_RULE;
  const probActive = Math.min(1, Math.max(0, input.probActive));
  const probs = lineProbabilities(input.line, anchors);
  const winPlays = input.side === 'over' ? probs.over : probs.under;
  const scale = voidRule === 'dnp_loss' ? probActive : 1;
  const win = winPlays * scale;
  const push = probs.push * scale;
  return {
    void_rule: voidRule,
    model_prob_plays: winPlays,
    push_prob_plays: probs.push,
    model_prob: win,
    prob_active: probActive,
    ev: evPerDollar(win, push, input.price),
    kelly_fraction: kellyFraction(win, push, input.price),
  };
}
