import { describe, it, expect } from 'vitest';
import {
  compareTrade,
  parseTradeRequest,
  tradeVerdict,
  type TradeCategoryDelta,
} from '../../src/services/tradeCheck.js';
import type { CategoryOutlook, OutlookCategory } from '../../src/services/weeklySimulation.js';

function outlook(category: OutlookCategory, win: number | null): CategoryOutlook {
  return {
    category,
    lower_is_better: category === 'tov',
    mean: 1, p10: 1, p50: 1, p90: 1, opponent: 1,
    win_probability: win,
  };
}

function delta(label: string, value: number | null): TradeCategoryDelta {
  return { category: 'pts', label, lower_is_better: false, before: 0.5, after: 0.5, delta: value };
}

describe('compareTrade', () => {
  it('subtracts win probabilities per category and sums them into expected category wins', () => {
    // arrange
    const before = [outlook('pts', 0.6), outlook('reb', 0.4), outlook('fg3m', 0.7), outlook('tov', 0.5)];
    const after = [outlook('pts', 0.62), outlook('reb', 0.75), outlook('fg3m', 0.55), outlook('tov', 0.45)];

    // act
    const result = compareTrade(before, after);

    // assert
    expect(result.categories.map((c) => [c.label, c.delta])).toEqual([
      ['PTS', 0.02],
      ['REB', 0.35],
      ['3PM', -0.15],
      ['TO', -0.05],
    ]);
    expect(result.before_expected_wins).toBe(2.2);
    expect(result.after_expected_wins).toBe(2.37);
    expect(result.delta_expected_wins).toBe(0.17);
  });

  it('leaves a category delta empty when either side has no win probability', () => {
    // act
    const result = compareTrade([outlook('ft_pct', null)], [outlook('ft_pct', 0.5)]);

    // assert
    expect(result.categories[0]).toMatchObject({ label: 'FT%', before: null, after: 0.5, delta: null });
  });
});

describe('tradeVerdict', () => {
  it('names the two biggest gains and any loss when the trade helps', () => {
    // arrange
    const rows = [delta('PTS', 0.02), delta('REB', 0.3), delta('BLK', 0.25), delta('AST', 0.1), delta('3PM', -0.12)];

    // act + assert
    expect(tradeVerdict(rows, 0.6)).toBe(
      'This trade helps: +0.6 expected category wins, mainly REB and BLK; you lose some 3PM.'
    );
  });

  it('leads with the losses when the trade hurts', () => {
    // arrange
    const rows = [delta('STL', -0.2), delta('FT%', -0.4), delta('TO', 0.08)];

    // act + assert
    expect(tradeVerdict(rows, -0.52)).toBe(
      'This trade hurts: -0.5 expected category wins, mainly FT% and STL; you gain some TO.'
    );
  });

  it('calls a small net change about even and lists both sides', () => {
    // arrange
    const rows = [delta('REB', 0.1), delta('3PM', -0.08), delta('PTS', 0.01)];

    // act + assert
    expect(tradeVerdict(rows, 0.04)).toBe(
      'This trade is about even: +0.04 expected category wins; you gain some REB and lose some 3PM.'
    );
  });

  it('keeps the sentence short when no category moves much', () => {
    // act + assert
    expect(tradeVerdict([delta('PTS', 0.01)], 0)).toBe('This trade is about even: +0.00 expected category wins.');
  });
});

describe('parseTradeRequest', () => {
  it('accepts positive integer ids, including numeric strings', () => {
    // act
    const parsed = parseTradeRequest({ give: [3, '4'], get: [10] });

    // assert
    expect(parsed).toEqual({ ok: true, request: { give: [3, 4], get: [10] } });
  });

  it.each([
    [{ get: [1] }, /give must be a non-empty list/],
    [{ give: [], get: [1] }, /give must be a non-empty list/],
    [{ give: [1] }, /get must be a non-empty list/],
    [{ give: [1.5], get: [2] }, /positive whole-number/],
    [{ give: [-1], get: [2] }, /positive whole-number/],
    [{ give: ['1; DROP TABLE'], get: [2] }, /positive whole-number/],
    [{ give: [1, 1], get: [2] }, /same player twice/],
    [{ give: [1], get: [1] }, /both sides/],
    [{ give: [1, 2, 3, 4, 5, 6], get: [7] }, /at most 5/],
  ])('rejects %j', (body, message) => {
    // act
    const parsed = parseTradeRequest(body);

    // assert
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) expect(parsed.error).toMatch(message);
  });
});
