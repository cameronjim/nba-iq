import { describe, it, expect } from 'vitest';
import { cdfAt, quantileAt } from '../../src/services/quantileDistribution.js';
import {
  KELLY_CAP,
  evPerDollar,
  kellyFraction,
  lineProbabilities,
  marketAnchors,
  priceProp,
  sumAnchors,
  type StatDistribution,
} from '../../src/services/propProbability.js';

function dist(expected: number | null, p10: number, p50: number, p90: number): StatDistribution {
  return { expected, p10, p50, p90 };
}

const SYMMETRIC = { p10: 10, p50: 20, p90: 30 };

describe('cdfAt', () => {
  it('returns the anchor quantiles at the anchor values', () => {
    // act
    const values = [10, 20, 30].map((x) => cdfAt(x, SYMMETRIC));

    // assert
    expect(values[0]).toBeCloseTo(0.1, 10);
    expect(values[1]).toBeCloseTo(0.5, 10);
    expect(values[2]).toBeCloseTo(0.9, 10);
  });

  it('inverts the same quantile function the weekly simulation draws from', () => {
    // arrange
    const anchors = { p10: 4, p50: 9, p90: 17 };
    const us = [0.05, 0.2, 0.5, 0.7, 0.95];

    // act
    const roundTrip = us.map((u) => cdfAt(quantileAt(u, anchors), anchors));

    // assert
    roundTrip.forEach((value, i) => expect(value).toBeCloseTo(us[i], 10));
  });

  it('puts the floored lower tail as a point mass at zero', () => {
    // arrange
    const anchors = { p10: 0, p50: 1, p90: 4 };

    // act
    const atZero = cdfAt(0, anchors);
    const belowZero = cdfAt(-0.5, anchors);

    // assert
    expect(atZero).toBeCloseTo(0.1, 10);
    expect(belowZero).toBe(0);
  });
});

describe('lineProbabilities', () => {
  it('splits a .5 line into over and under with no push', () => {
    // act
    const probs = lineProbabilities(19.5, SYMMETRIC);

    // assert
    expect(probs.over).toBeCloseTo(0.52, 10);
    expect(probs.under).toBeCloseTo(0.48, 10);
    expect(probs.push).toBe(0);
  });

  it('gives an integer line the density between L - 0.5 and L + 0.5 as push mass', () => {
    // act
    const probs = lineProbabilities(20, SYMMETRIC);

    // assert
    expect(probs.over).toBeCloseTo(0.48, 10);
    expect(probs.under).toBeCloseTo(0.48, 10);
    expect(probs.push).toBeCloseTo(0.04, 10);
  });
});

describe('sumAnchors (PRA)', () => {
  it('centres on the summed expectations and adds half-widths in quadrature', () => {
    // arrange
    const parts = [dist(21, 15, 20, 25), dist(8, 5, 8, 11), dist(6, 4, 6, 8)];

    // act
    const anchors = sumAnchors(parts);

    // assert
    expect(anchors).not.toBeNull();
    expect(anchors!.p50).toBeCloseTo(35, 10);
    expect(anchors!.p50 - anchors!.p10).toBeCloseTo(Math.sqrt(38), 10);
    expect(anchors!.p90 - anchors!.p50).toBeCloseTo(Math.sqrt(38), 10);
  });

  it('cannot price PRA when one component has no quantiles', () => {
    // arrange
    const stats = { pts: dist(20, 15, 20, 25), reb: dist(8, 5, 8, 11) };

    // act
    const anchors = marketAnchors('pra', stats);

    // assert
    expect(anchors).toBeNull();
  });
});

describe('priceProp', () => {
  const stats = { pts: dist(20, 10, 20, 30) };

  it('reports P(over | plays) as model_prob under dnp_void', () => {
    // act
    const pricing = priceProp({ market: 'pts', line: 19.5, side: 'over', price: -110, stats, probActive: 0.8 });

    // assert
    expect(pricing?.void_rule).toBe('dnp_void');
    expect(pricing?.model_prob_plays).toBeCloseTo(0.52, 10);
    expect(pricing?.model_prob).toBeCloseTo(0.52, 10);
    expect(pricing?.prob_active).toBe(0.8);
  });

  it('multiplies by prob_active under dnp_loss', () => {
    // act
    const pricing = priceProp({
      market: 'pts',
      line: 19.5,
      side: 'over',
      price: -110,
      stats,
      probActive: 0.8,
      voidRule: 'dnp_loss',
    });

    // assert
    expect(pricing?.model_prob_plays).toBeCloseTo(0.52, 10);
    expect(pricing?.model_prob).toBeCloseTo(0.416, 10);
    expect(pricing!.ev).toBeLessThan(0);
  });

  it('returns null when the stat has no stored quantiles', () => {
    // act
    const pricing = priceProp({ market: 'reb', line: 7.5, side: 'over', price: -110, stats, probActive: 1 });

    // assert
    expect(pricing).toBeNull();
  });
});

describe('evPerDollar', () => {
  it('is positive at +100 with a 55% win probability', () => {
    // act + assert
    expect(evPerDollar(0.55, 0, 100)).toBeCloseTo(0.1, 10);
  });

  it('is negative at -110 with a coin flip and positive at 60%', () => {
    // act
    const flip = evPerDollar(0.5, 0, -110);
    const edge = evPerDollar(0.6, 0, -110);

    // assert
    expect(flip).toBeCloseTo(0.5 * (100 / 110) - 0.5, 10);
    expect(flip).toBeLessThan(0);
    expect(edge).toBeGreaterThan(0);
  });

  it('returns the stake on a push rather than losing it', () => {
    // act
    const withPush = evPerDollar(0.5, 0.1, 100);

    // assert
    expect(withPush).toBeCloseTo(0.1, 10);
  });
});

describe('kellyFraction', () => {
  it('is a quarter of full kelly below the cap', () => {
    // act + assert
    expect(kellyFraction(0.52, 0, 100)).toBeCloseTo(0.01, 10);
  });

  it('caps at 2% of bankroll', () => {
    // act + assert
    expect(kellyFraction(0.7, 0, 100)).toBe(KELLY_CAP);
  });

  it('is zero for a negative-EV bet', () => {
    // act + assert
    expect(kellyFraction(0.5, 0, -110)).toBe(0);
  });
});
