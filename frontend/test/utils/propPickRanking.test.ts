import { describe, it, expect } from 'vitest';
import { bestPickPerMarket, rankPicks } from '../../src/utils/propPickRanking';
import type { PropPick } from '../../src/types';

const pick = (over: Partial<PropPick>): PropPick => ({
  player_name: 'Stephen Curry', team: 'GSW', opponent: 'LAL', game_date: '2026-10-21',
  market: 'pts', line: 24.5, side: 'over', bookmaker: 'DraftKings', price: -115,
  model_prob: 0.58, implied_prob_novig: 0.53, ev: 0.08, prob_active: 0.82, void_rule: 'dnp',
  ...over,
});

describe('bestPickPerMarket', () => {
  it('keeps the highest ev row per player, game and market', () => {
    // arrange
    const picks = [pick({ line: 24.5, ev: 0.05 }), pick({ line: 25.5, ev: 0.09 }), pick({ market: 'reb', ev: 0.02 })];

    // act
    const result = bestPickPerMarket(picks);

    // assert
    expect(result).toHaveLength(2);
    expect(result.find((p) => p.market === 'pts')?.line).toBe(25.5);
  });

  it('breaks ev ties by model probability, then shorter price', () => {
    // arrange
    const byProb = [pick({ line: 1, model_prob: 0.55 }), pick({ line: 2, model_prob: 0.6 })];
    const byPrice = [pick({ line: 3, price: -130 }), pick({ line: 4, price: -105 })];

    // act + assert
    expect(bestPickPerMarket(byProb)[0].line).toBe(2);
    expect(bestPickPerMarket(byPrice)[0].line).toBe(4);
  });

  it('keeps separate players and games apart', () => {
    // arrange
    const picks = [pick({}), pick({ player_name: 'Jimmy Butler' }), pick({ game_date: '2026-10-22' })];

    // act + assert
    expect(bestPickPerMarket(picks)).toHaveLength(3);
  });
});

describe('rankPicks', () => {
  it('sorts by ev descending without mutating the input', () => {
    // arrange
    const picks = [pick({ ev: 0.01 }), pick({ ev: 0.09 }), pick({ ev: 0.05 })];

    // act
    const result = rankPicks(picks);

    // assert
    expect(result.map((p) => p.ev)).toEqual([0.09, 0.05, 0.01]);
    expect(picks[0].ev).toBe(0.01);
  });
});
