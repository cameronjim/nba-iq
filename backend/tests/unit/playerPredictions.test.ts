import { describe, it, expect, vi, beforeEach } from 'vitest';
import { query } from '../../src/db.js';
import { pgResult } from '../helpers/mockDb.js';
import { COMPLETE_RUN_STATUS, PRODUCTION_CHANNEL } from '../../src/services/slate.js';
import {
  getLatestRunMeta,
  DEFAULT_UPCOMING_LIMIT,
  MAX_UPCOMING_LIMIT,
  collectStatKeys,
  horizonFromNotes,
  orderStatKeys,
  parseFromDate,
  parseLimit,
  pivotUpcomingRows,
  type UpcomingPredictionRow,
} from '../../src/services/playerPredictions.js';


function row(over: Partial<UpcomingPredictionRow> = {}): UpcomingPredictionRow {
  return {
    nba_game_id: '0022500586',
    game_date: new Date(2026, 0, 15),
    home_team_abbr: 'LAL',
    away_team_abbr: 'CHA',
    game_status: 'Final',
    stat: 'pts',
    quantile: null,
    value: 30,
    conditional: true,
    ...over,
  };
}

describe('parseFromDate', () => {
  it('treats an absent parameter as no filter rather than as today', () => {
    expect(parseFromDate(undefined)).toBeNull();
    expect(parseFromDate('')).toBeNull();
    expect(parseFromDate(null)).toBeNull();
  });

  it('accepts a calendar day', () => {
    expect(parseFromDate('2026-01-17')).toBe('2026-01-17');
  });

  it('rejects a malformed or impossible day', () => {
    expect(parseFromDate('2026-02-31')).toBe(false);
    expect(parseFromDate('17/01/2026')).toBe(false);
    expect(parseFromDate(20260117)).toBe(false);
  });
});

describe('parseLimit', () => {
  it('defaults when absent', () => {
    expect(parseLimit(undefined)).toBe(DEFAULT_UPCOMING_LIMIT);
    expect(parseLimit('')).toBe(DEFAULT_UPCOMING_LIMIT);
  });

  it('accepts a whole number inside the served range', () => {
    expect(parseLimit('3')).toBe(3);
    expect(parseLimit(String(MAX_UPCOMING_LIMIT))).toBe(MAX_UPCOMING_LIMIT);
  });

  it('rejects rather than clamps', () => {
    expect(parseLimit('0')).toBe(false);
    expect(parseLimit('2.5')).toBe(false);
    expect(parseLimit(String(MAX_UPCOMING_LIMIT + 1))).toBe(false);
    expect(parseLimit('lots')).toBe(false);
  });
});

describe('horizonFromNotes', () => {
  it('reads the horizon clause out of a run note', () => {
    expect(
      horizonFromNotes('horizon=gameday (T-6h); backtest smoke run: cutoff 2026-01-15')
    ).toBe('gameday (T-6h)');
  });

  it('is null for a note that does not carry one', () => {
    expect(horizonFromNotes('backtest smoke run')).toBeNull();
    expect(horizonFromNotes(null)).toBeNull();
    expect(horizonFromNotes(undefined)).toBeNull();
  });
});

describe('orderStatKeys', () => {
  it('puts known stats in box-score order and appends unknown ones alphabetically', () => {
    expect(orderStatKeys(['zebra', 'pts', 'fga', 'minutes', 'alpha'])).toEqual([
      'minutes',
      'pts',
      'fga',
      'alpha',
      'zebra',
    ]);
  });
});

describe('pivotUpcomingRows', () => {
  it('groups the long format into one entry per game, date-ordered', () => {
    const rows = [
      row({ nba_game_id: '0022500601', game_date: new Date(2026, 0, 17), stat: 'pts', value: 27 }),
      row({ stat: 'pts', value: 30 }),
    ];

    const games = pivotUpcomingRows(rows, 'LAL');

    expect(games.map((g) => g.game_date)).toEqual(['2026-01-15', '2026-01-17']);
  });

  it('splits conditional, unconditional and quantile rows into one stat line', () => {
    const rows = [
      row({ stat: 'minutes', quantile: null, value: 36.333 }),
      row({ stat: 'minutes', quantile: 0.1, value: 28.475 }),
      row({ stat: 'minutes', quantile: 0.5, value: 36.17 }),
      row({ stat: 'minutes', quantile: 0.9, value: 43.5 }),
      row({ stat: 'minutes_uncond', quantile: null, value: 33.232, conditional: false }),
    ];

    const [game] = pivotUpcomingRows(rows, 'LAL');

    expect(Object.keys(game.stats)).toEqual(['minutes']);
    expect(game.stats.minutes).toEqual({
      expected: 36.33,
      p10: 28.48,
      p50: 36.17,
      p90: 43.5,
      unconditional: 33.23,
    });
  });

  it('serves a partial band instead of dropping it', () => {
    const rows = [row({ stat: 'pts', quantile: 0.5, value: 24.5 })];

    const [game] = pivotUpcomingRows(rows, 'LAL');

    expect(game.stats.pts).toEqual({
      expected: null,
      p10: null,
      p50: 24.5,
      p90: null,
      unconditional: null,
    });
  });

  it('lifts the two availability stats out of the stat map and clamps them', () => {
    const rows = [
      row({ stat: 'prob_active', value: 1.4, conditional: false }),
      row({ stat: 'prob_active_model', value: -0.2, conditional: false }),
      row({ stat: 'pts', value: 30 }),
    ];

    const [game] = pivotUpcomingRows(rows, 'LAL');

    expect(game.prob_active).toBe(1);
    expect(game.prob_active_model).toBe(0);
    expect(Object.keys(game.stats)).toEqual(['pts']);
  });

  it('never nulls a stat line out for a player the model expects to sit', () => {
    const rows = [
      row({ stat: 'prob_active', value: 0.04, conditional: false }),
      row({ stat: 'pts', quantile: 0.5, value: 22.1 }),
      row({ stat: 'pts_uncond', quantile: null, value: 0.9, conditional: false }),
    ];

    const [game] = pivotUpcomingRows(rows, 'LAL');

    expect(game.prob_active).toBe(0.04);
    expect(game.stats.pts.p50).toBe(22.1);
    expect(game.stats.pts.unconditional).toBe(0.9);
  });

  it('reads home/away off the player team and picks the other side as the opponent', () => {
    const [home] = pivotUpcomingRows([row()], 'LAL');
    const [away] = pivotUpcomingRows([row()], 'CHA');
    const [neither] = pivotUpcomingRows([row()], 'BOS');
    const [unknown] = pivotUpcomingRows([row()], null);

    expect([home.is_home, home.opponent_abbr]).toEqual([true, 'CHA']);
    expect([away.is_home, away.opponent_abbr]).toEqual([false, 'LAL']);
    expect([neither.is_home, neither.opponent_abbr]).toEqual([null, null]);
    expect([unknown.is_home, unknown.opponent_abbr]).toEqual([null, null]);
  });

  it('skips rows with no usable game id or date', () => {
    const games = pivotUpcomingRows([row({ nba_game_id: null }), row({ game_date: null })], 'LAL');

    expect(games).toEqual([]);
  });
});

describe('collectStatKeys', () => {
  it('unions the stat keys across games in display order', () => {
    const rows = [
      row({ stat: 'pts', value: 30 }),
      row({ nba_game_id: '0022500601', game_date: new Date(2026, 0, 17), stat: 'reb', value: 8 }),
      row({ nba_game_id: '0022500601', game_date: new Date(2026, 0, 17), stat: 'minutes', value: 33 }),
    ];

    const keys = collectStatKeys(pivotUpcomingRows(rows, 'LAL'));

    expect(keys).toEqual(['minutes', 'pts', 'reb']);
  });
});

describe('getLatestRunMeta', () => {
  const queryMock = vi.mocked(query);

  beforeEach(() => {
    queryMock.mockReset();
  });

  it('reads run metadata only from the production channel', async () => {
    // arrange
    queryMock.mockResolvedValueOnce(pgResult([]));

    // act
    const meta = await getLatestRunMeta();

    // assert
    const [sql, params] = queryMock.mock.calls[0];
    expect(params).toEqual([COMPLETE_RUN_STATUS, PRODUCTION_CHANNEL]);
    expect(sql).toMatch(/channel = \$2/);
    expect(meta).toBeNull();
  });
});
