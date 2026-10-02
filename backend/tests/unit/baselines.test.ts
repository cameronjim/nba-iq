import { describe, it, expect, vi, beforeEach } from 'vitest';
import { pgResult } from '../helpers/mockDb.js';
import { query } from '../../src/db.js';
import {
  BASELINE_DEFINITION,
  BASELINE_RECENT_GAMES,
  BASELINE_STATS,
  BASELINE_WINDOW_GAMES,
  MIN_BASELINE_GAMES,
  NOTABLE_MINUTES_DELTA,
  baselineDescriptor,
  baselineIncludesPostseason,
  baselineSeasonTypes,
  daysSince,
  deltaOf,
  fetchBaselines,
  hasUsableBaseline,
  type BaselineStat,
  type PlayerBaseline,
} from '../../src/services/baselines.js';

const queryMock = vi.mocked(query);

function baseline(overrides: Partial<PlayerBaseline> = {}): PlayerBaseline {
  const avg = {} as Record<BaselineStat, number | null>;
  for (const stat of BASELINE_STATS) avg[stat] = 1;
  return {
    nba_player_id: '1001',
    games: BASELINE_WINDOW_GAMES,
    avg,
    pts_recent: 12,
    pts_sd: 4,
    last_played_date: '2026-02-03',
    ...overrides,
  };
}

describe('hasUsableBaseline', () => {
  it('accepts a baseline exactly at the minimum', () => {
    expect(hasUsableBaseline(baseline({ games: MIN_BASELINE_GAMES }))).toBe(true);
  });

  it('rejects one game short — that is the rookie with no usual to deviate from', () => {
    expect(hasUsableBaseline(baseline({ games: MIN_BASELINE_GAMES - 1 }))).toBe(false);
  });

  it('rejects a player with no baseline row at all', () => {
    expect(hasUsableBaseline(undefined)).toBe(false);
  });
});

describe('deltaOf', () => {
  it('subtracts usual from projected', () => {
    expect(deltaOf(31, 22)).toBe(9);
    expect(deltaOf(18, 26)).toBe(-8);
  });

  it('is null rather than zero when either half is missing', () => {
    expect(deltaOf(31, null)).toBeNull();
    expect(deltaOf(null, 22)).toBeNull();
  });

  it('reports a genuine zero as zero', () => {
    expect(deltaOf(24, 24)).toBe(0);
  });
});

describe('daysSince', () => {
  it('counts whole days from the last appearance to the date', () => {
    expect(daysSince('2026-02-10', '2026-02-01')).toBe(9);
  });

  it('spans a month boundary', () => {
    expect(daysSince('2026-03-02', '2026-02-25')).toBe(5);
  });

  it('is null when there is no appearance on record', () => {
    expect(daysSince('2026-02-10', null)).toBeNull();
  });

  it('is null for an unparseable day rather than NaN days', () => {
    expect(daysSince('2026-02-10', 'not-a-day')).toBeNull();
  });
});

describe('baselineDescriptor', () => {
  it('publishes the window, the minimum and the threshold the pages read', () => {
    const descriptor = baselineDescriptor();

    expect(descriptor.window_games).toBe(BASELINE_WINDOW_GAMES);
    expect(descriptor.min_games).toBe(MIN_BASELINE_GAMES);
    expect(descriptor.notable_min_delta).toBe(NOTABLE_MINUTES_DELTA);
    expect(descriptor.definition).toContain(String(BASELINE_WINDOW_GAMES));
    expect(descriptor.definition).toContain(String(MIN_BASELINE_GAMES));
    expect(descriptor.label).toBeTruthy();
  });
});

describe('the baseline window', () => {
  it('keeps the hot-streak window inside the baseline window', () => {
    expect(BASELINE_RECENT_GAMES).toBeLessThan(BASELINE_WINDOW_GAMES);
  });

  it('covers every stat the projections can be compared against', () => {
    expect([...BASELINE_STATS]).toEqual(
      expect.arrayContaining(['minutes', 'pts', 'reb', 'ast', 'stl', 'blk', 'fg3m', 'fga'])
    );
  });
});

describe('baselineIncludesPostseason', () => {
  it('is off when the flag is unset', () => {
    expect(baselineIncludesPostseason({})).toBe(false);
  });

  it('is on for the usual truthy spellings', () => {
    for (const value of ['true', 'TRUE', '1', 'on', ' yes ']) {
      expect(baselineIncludesPostseason({ BASELINE_INCLUDES_POSTSEASON: value })).toBe(true);
    }
  });

  it('stays off for anything else', () => {
    for (const value of ['false', '0', 'off', '']) {
      expect(baselineIncludesPostseason({ BASELINE_INCLUDES_POSTSEASON: value })).toBe(false);
    }
  });
});

describe('the baseline season types', () => {
  it('is the regular season alone with the flag off', () => {
    // act
    const descriptor = baselineDescriptor(false);

    // assert
    expect(baselineSeasonTypes(false)).toEqual(['Regular Season']);
    expect(descriptor.season_types).toEqual(['Regular Season']);
    expect(descriptor.includes_postseason).toBe(false);
    expect(descriptor.definition).toContain('regular-season games');
    expect(descriptor.definition).toBe(BASELINE_DEFINITION);
  });

  it('adds the play-in and the playoffs with the flag on, and says so', () => {
    // act
    const descriptor = baselineDescriptor(true);

    // assert
    expect(descriptor.season_types).toEqual(['Regular Season', 'PlayIn', 'Playoffs']);
    expect(descriptor.includes_postseason).toBe(true);
    expect(descriptor.definition).toContain('regular-season, play-in and playoff games');
  });

  it('reads the flag from the environment by default', () => {
    // arrange
    const previous = process.env.BASELINE_INCLUDES_POSTSEASON;
    delete process.env.BASELINE_INCLUDES_POSTSEASON;

    // act
    const descriptor = baselineDescriptor();

    // assert
    expect(descriptor.includes_postseason).toBe(false);
    if (previous !== undefined) process.env.BASELINE_INCLUDES_POSTSEASON = previous;
  });
});

describe('fetchBaselines', () => {
  beforeEach(() => {
    queryMock.mockReset();
    queryMock.mockResolvedValue(pgResult([]));
  });

  it('filters the logs to the regular season with the flag off', async () => {
    // act
    await fetchBaselines('2026-11-10', null, false);

    // assert
    const [sql, params] = queryMock.mock.calls[0];
    expect(sql).toContain('g.season_type = ANY($5)');
    expect(params?.[4]).toEqual(['Regular Season']);
  });

  it('reads play-in and playoff logs with the flag on', async () => {
    // act
    await fetchBaselines('2026-11-10', ['1628369'], true);

    // assert
    const [sql, params] = queryMock.mock.calls[0];
    expect(params?.[4]).toEqual(['Regular Season', 'PlayIn', 'Playoffs']);
    expect(sql).toContain('g.nba_player_id = ANY($6)');
    expect(params?.[5]).toEqual(['1628369']);
  });

  it('never reads preseason logs, whatever the postseason flag says', async () => {
    // act
    await fetchBaselines('2026-10-10', null, false);
    await fetchBaselines('2026-10-10', null, true);

    // assert
    for (const [, params] of queryMock.mock.calls) {
      expect(params?.[4]).not.toContain('Pre Season');
    }
    expect(baselineDescriptor(true).season_types).not.toContain('Pre Season');
    expect(baselineDescriptor(false).season_types).not.toContain('Pre Season');
  });
});
