import { describe, it, expect } from 'vitest';
import {
  SIM_STATS,
  createRng,
  drawGameLine,
  missRisk,
  prepareGame,
  quantileAt,
  resolveAnchors,
  simulateWeek,
  typicalOpponent,
  type OpponentLine,
  type SimGame,
  type SimPlayer,
  type SimStat,
  type StatInput,
} from '../../src/services/weeklySimulation.js';
import type { ImpactInput } from '../../src/services/slate.js';

function spread(expected: number, p10: number, p50: number, p90: number): StatInput {
  return { expected, p10, p50, p90 };
}

function exact(value: number): StatInput {
  return { expected: value, p10: value, p50: value, p90: value };
}

function fullGame(over: Partial<SimGame> = {}): SimGame {
  return {
    nba_game_id: 'g1',
    game_date: '2026-01-15',
    prob_active: 0.9,
    stats: {
      pts: spread(20, 12, 19, 29),
      reb: spread(6, 3, 6, 10),
      ast: spread(4, 1, 4, 7),
      stl: spread(1, 0, 1, 2),
      blk: spread(0.6, 0, 0.5, 2),
      tov: spread(2, 0.5, 2, 4),
      fg3m: spread(2, 0, 2, 4),
      fgm: spread(7, 4, 7, 11),
      fga: spread(15, 10, 15, 21),
      ftm: spread(4, 1, 4, 8),
      fta: spread(5, 1.5, 5, 9),
    },
    ...over,
  };
}

function exactGame(id: string, prob: number, stats: Partial<Record<SimStat, number>>): SimGame {
  const out: Partial<Record<SimStat, StatInput>> = {};
  for (const stat of SIM_STATS) {
    const value = stats[stat];
    if (value !== undefined) out[stat] = exact(value);
  }
  return { nba_game_id: id, game_date: '2026-01-15', prob_active: prob, stats: out };
}

function opponentLine(over: Partial<OpponentLine>): OpponentLine {
  return {
    pts: 0,
    reb: 0,
    ast: 0,
    stl: 0,
    blk: 0,
    fg3m: 0,
    tov: 0,
    fg_pct: 0,
    ft_pct: 0,
    ...over,
  };
}

describe('quantileAt', () => {
  it('passes through each of its three anchors', () => {
    // arrange
    const anchors = { p10: 4, p50: 9, p90: 17 };

    // act
    const values = [0.1, 0.5, 0.9].map((u) => quantileAt(u, anchors));

    // assert
    expect(values[0]).toBeCloseTo(4, 10);
    expect(values[1]).toBeCloseTo(9, 10);
    expect(values[2]).toBeCloseTo(17, 10);
  });

  it('extends the outer slopes into the tails and clips the lower tail at zero', () => {
    // arrange
    const anchors = { p10: 1, p50: 5, p90: 9 };

    // act
    const top = quantileAt(1, anchors);
    const bottom = quantileAt(0, anchors);

    // assert
    expect(top).toBeCloseTo(10, 10);
    expect(bottom).toBe(0);
  });
});

describe('resolveAnchors', () => {
  it('flags a stat with no stored quantiles and spreads it around the expectation', () => {
    // arrange
    const input: StatInput = { expected: 10, p10: null, p50: null, p90: null };

    // act
    const { anchors, fallback } = resolveAnchors('pts', input);

    // assert
    expect(fallback).toBe(true);
    expect(anchors.p50).toBe(10);
    expect(anchors.p10).toBeLessThan(10);
    expect(anchors.p90).toBeGreaterThan(10);
  });
});

describe('drawGameLine', () => {
  it('keeps makes within attempts and threes within makes on every draw', () => {
    // arrange
    const game = prepareGame(fullGame(), new Set());
    const rng = createRng(7);
    const violations: string[] = [];

    // act
    for (let i = 0; i < 5000; i += 1) {
      const line = drawGameLine(game, rng);
      if (line.fgm > line.fga + 1e-9) violations.push(`fgm ${line.fgm} > fga ${line.fga}`);
      if (line.ftm > line.fta + 1e-9) violations.push(`ftm ${line.ftm} > fta ${line.fta}`);
      if (line.fg3m > line.fgm + 1e-9) violations.push(`fg3m ${line.fg3m} > fgm ${line.fgm}`);
      for (const stat of SIM_STATS) if (line[stat] < 0) violations.push(`${stat} negative`);
    }

    // assert
    expect(violations).toEqual([]);
  });
});

describe('miss risk', () => {
  const probs = [0.9, 0.8, 0.95];
  const players: SimPlayer[] = [
    {
      nba_player_id: 'p1',
      games: probs.map((p, i) => exactGame(`g${i}`, p, { pts: 10 })),
    },
  ];
  const independent = 1 - probs.reduce((acc, p) => acc * p, 1);
  const dependent = Math.max(...probs.map((p) => 1 - p));

  it('equals the largest single-game miss chance when rho is 1', () => {
    // act
    const result = simulateWeek(players, null, { rho: 1, n: 4000 });

    // assert
    expect(missRisk(probs, 1)).toBeCloseTo(dependent, 10);
    expect(result.players[0].miss_risk).toBeCloseTo(dependent, 10);
    expect(result.players[0].simulated_miss_risk).toBeCloseTo(dependent, 1);
  });

  it('equals one minus the product of play chances when rho is 0', () => {
    // act
    const result = simulateWeek(players, null, { rho: 0, n: 4000 });

    // assert
    expect(missRisk(probs, 0)).toBeCloseTo(independent, 10);
    expect(result.players[0].simulated_miss_risk).toBeCloseTo(independent, 1);
  });

  it('sits strictly between the two bounds at the default rho of 0.5', () => {
    // act
    const result = simulateWeek(players, null, { n: 4000 });
    const risk = result.players[0].miss_risk;
    const simulated = result.players[0].simulated_miss_risk;

    // assert
    expect(risk).toBeGreaterThan(dependent);
    expect(risk).toBeLessThan(independent);
    expect(simulated).toBeGreaterThan(dependent);
    expect(simulated).toBeLessThan(independent);
  });

  it('is zero for a player with no games in the window', () => {
    // act
    const result = simulateWeek([{ nba_player_id: 'p2', games: [] }], null);

    // assert
    expect(result.players[0]).toMatchObject({ games_scheduled: 0, miss_risk: 0 });
  });
});

describe('simulateWeek', () => {
  const roster: SimPlayer[] = [
    { nba_player_id: 'a', games: [fullGame(), fullGame({ nba_game_id: 'g2', prob_active: 0.6 })] },
    { nba_player_id: 'b', games: [fullGame({ nba_game_id: 'g3' })] },
  ];

  it('is deterministic for a given seed', () => {
    // act
    const first = simulateWeek(roster, null, { seed: 42 });
    const second = simulateWeek(roster, null, { seed: 42 });
    const other = simulateWeek(roster, null, { seed: 43 });

    // assert
    expect(second).toEqual(first);
    expect(other.categories).not.toEqual(first.categories);
  });

  it('reports ordered percentiles for every category', () => {
    // act
    const result = simulateWeek(roster, null);

    // assert
    expect(result.categories.map((c) => c.category)).toEqual([
      'pts', 'reb', 'ast', 'stl', 'blk', 'fg3m', 'fg_pct', 'ft_pct', 'tov',
    ]);
    for (const category of result.categories) {
      expect(category.p10!).toBeLessThanOrEqual(category.p50!);
      expect(category.p50!).toBeLessThanOrEqual(category.p90!);
    }
  });

  it('computes shooting percentages as makes over attempts summed, not a mean of game percentages', () => {
    // arrange
    const players: SimPlayer[] = [
      { nba_player_id: 'a', games: [exactGame('g1', 1, { fgm: 2, fga: 2, ftm: 1, fta: 1 })] },
      { nba_player_id: 'b', games: [exactGame('g2', 1, { fgm: 9, fga: 22, ftm: 3, fta: 9 })] },
    ];

    // act
    const result = simulateWeek(players, null, { n: 50 });
    const fg = result.categories.find((c) => c.category === 'fg_pct')!;
    const ft = result.categories.find((c) => c.category === 'ft_pct')!;

    // assert
    expect(fg.p50).toBeCloseTo(11 / 24, 10);
    expect(fg.mean).toBeCloseTo(11 / 24, 10);
    expect(ft.p50).toBeCloseTo(4 / 10, 10);
  });

  it('records which stats fell back to the default spread', () => {
    // arrange
    const players: SimPlayer[] = [
      {
        nba_player_id: 'a',
        games: [
          {
            nba_game_id: 'g1',
            game_date: '2026-01-15',
            prob_active: 1,
            stats: { pts: exact(10), reb: { expected: 5, p10: null, p50: null, p90: null } },
          },
        ],
      },
    ];

    // act
    const result = simulateWeek(players, null, { n: 10 });

    // assert
    expect(result.players[0].fallback_stats).toEqual(['reb']);
  });

  it('wins every category with certainty when the roster dominates the opponent', () => {
    // arrange
    const players: SimPlayer[] = [
      {
        nba_player_id: 'a',
        games: [
          exactGame('g1', 1, {
            pts: 200, reb: 80, ast: 50, stl: 20, blk: 20, tov: 1, fg3m: 30,
            fgm: 60, fga: 100, ftm: 40, fta: 45,
          }),
        ],
      },
    ];
    const opponent = opponentLine({
      pts: 100, reb: 40, ast: 25, stl: 7, blk: 5, fg3m: 12, tov: 15, fg_pct: 0.45, ft_pct: 0.75,
    });

    // act
    const result = simulateWeek(players, opponent, { n: 200 });

    // assert
    for (const category of result.categories) expect(category.win_probability).toBe(1);
  });

  it('loses every category with certainty when the roster is dominated', () => {
    // arrange
    const players: SimPlayer[] = [
      {
        nba_player_id: 'a',
        games: [
          exactGame('g1', 1, {
            pts: 10, reb: 4, ast: 2, stl: 1, blk: 0, tov: 30, fg3m: 1,
            fgm: 4, fga: 20, ftm: 2, fta: 10,
          }),
        ],
      },
    ];
    const opponent = opponentLine({
      pts: 100, reb: 40, ast: 25, stl: 7, blk: 5, fg3m: 12, tov: 15, fg_pct: 0.45, ft_pct: 0.75,
    });

    // act
    const result = simulateWeek(players, opponent, { n: 200 });

    // assert
    for (const category of result.categories) expect(category.win_probability).toBe(0);
  });
});

describe('typicalOpponent', () => {
  function entry(scale: number): ImpactInput {
    return {
      pts: 20 * scale, reb: 8 * scale, ast: 5 * scale, stl: scale, blk: scale, tov: 2 * scale,
      fg3m: 2 * scale, fgm: 8 * scale, fga: 16 * scale, ftm: 4 * scale, fta: 5 * scale,
    };
  }

  it('averages the top league-size pool and scales it to the roster size', () => {
    // arrange
    const pool = [entry(3), entry(2), entry(1), entry(0.5)];

    // act
    const opponent = typicalOpponent(pool, 2, 1);

    // assert
    expect(opponent?.players_used).toBe(2);
    expect(opponent?.totals.pts).toBeCloseTo(((60 + 40) / 2) * 2, 10);
    expect(opponent?.totals.fg_pct).toBeCloseTo(0.5, 10);
    expect(opponent?.totals.ft_pct).toBeCloseTo(0.8, 10);
  });

  it('returns null for an empty pool', () => {
    // act + assert
    expect(typicalOpponent([], 10)).toBeNull();
  });
});
