import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { query } from '../../src/db.js';
import { pgResult } from '../helpers/mockDb.js';
import {
  COMPLETE_RUN_STATUS,
  PRODUCTION_CHANNEL,
  getLatestCompleteRun,
  IMPACT_CATEGORIES,
  PLACEHOLDER_NAME_SUFFIX,
  POINTS_UNCOND_STAT,
  PROJECTED_STATS,
  SPOTLIGHT_PER_GAME,
  TOP_PLAYERS_PER_GAME,
  categoryValues,
  impactScores,
  injuryOverlayFields,
  isMissingRelation,
  num,
  parsePredictionDate,
  poolRates,
  rankSlatePlayers,
  resolvePlayerName,
  round,
  toIsoDay,
  topImpactIds,
  uncondStat,
  type ImpactInput,
  type SlatePlayer,
} from '../../src/services/slate.js';

function player(overrides: Partial<SlatePlayer> = {}): SlatePlayer {
  return {
    nba_player_id: '1',
    name: 'Test Player',
    name_is_placeholder: false,
    team_abbr: 'LAL',
    prob_active: 0.9,
    proj_pts: 10,
    proj_min_p50: 25,
    projected: { reb: 5, ast: 4, stl: 1, blk: 0.5, tov: 2, fg3m: 1.5 },
    usual_min: 24,
    usual_pts: 9,
    min_vs_usual: 1,
    pts_vs_usual: 1,
    baseline_games: 15,
    impact: null,
    spotlight: false,
    slate_spotlight: false,
    injury_status: null,
    injury_status_raw: null,
    injury_detail: null,
    injury_as_of: null,
    injury_changed_after_run: false,
    ...overrides,
  };
}

function input(overrides: Partial<ImpactInput> = {}): ImpactInput {
  const entry = {} as ImpactInput;
  for (const stat of PROJECTED_STATS) entry[stat] = 1;
  return { ...entry, ...overrides };
}

afterEach(() => {
  vi.useRealTimers();
});

describe('parsePredictionDate', () => {
  it('defaults to the ET calendar day, not the UTC one', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-02-05T03:30:00Z'));

    expect(parsePredictionDate(undefined)).toBe('2026-02-04');
    expect(parsePredictionDate('')).toBe('2026-02-04');
  });

  it('accepts a well-formed calendar day', () => {
    expect(parsePredictionDate('2026-02-04')).toBe('2026-02-04');
    expect(parsePredictionDate('2024-02-29')).toBe('2024-02-29');
  });

  it('rejects a date that is not a real calendar day', () => {
    expect(parsePredictionDate('2026-02-31')).toBeNull();
    expect(parsePredictionDate('2025-02-29')).toBeNull();
    expect(parsePredictionDate('2026-13-01')).toBeNull();
  });

  it('rejects anything that is not a YYYY-MM-DD string', () => {
    expect(parsePredictionDate('02/04/2026')).toBeNull();
    expect(parsePredictionDate('2026-2-4')).toBeNull();
    expect(parsePredictionDate('tomorrow')).toBeNull();
    expect(parsePredictionDate(20260204)).toBeNull();
    expect(parsePredictionDate(['2026-02-04'])).toBeNull();
  });
});

describe('num', () => {
  it('parses the strings pg returns for NUMERIC columns', () => {
    expect(num('12.5')).toBe(12.5);
    expect(num(3)).toBe(3);
  });

  it('distinguishes missing from zero', () => {
    expect(num(null)).toBeNull();
    expect(num(undefined)).toBeNull();
    expect(num('')).toBeNull();
    expect(num('abc')).toBeNull();
    expect(num(0)).toBe(0);
  });
});

describe('round', () => {
  it('rounds to the requested precision', () => {
    expect(round(12.3456, 1)).toBe(12.3);
    expect(round(0.98765, 3)).toBe(0.988);
  });

  it('keeps null as null rather than collapsing it to zero', () => {
    expect(round(null, 2)).toBeNull();
  });
});

describe('toIsoDay', () => {
  it('reads the local calendar fields of a pg DATE', () => {
    const date = new Date(2026, 1, 4);

    expect(toIsoDay(date)).toBe('2026-02-04');
  });

  it('takes the day off a timestamp string', () => {
    expect(toIsoDay('2026-02-04T00:00:00.000Z')).toBe('2026-02-04');
  });

  it('is null for anything else', () => {
    expect(toIsoDay(null)).toBeNull();
    expect(toIsoDay(42)).toBeNull();
  });
});

describe('isMissingRelation', () => {
  it('recognizes an unapplied migration', () => {
    expect(isMissingRelation({ code: '42P01' })).toBe(true);
    expect(isMissingRelation({ code: '42703' })).toBe(true);
  });

  it('does not swallow a real database failure', () => {
    expect(isMissingRelation({ code: '08006' })).toBe(false);
    expect(isMissingRelation(new Error('db down'))).toBe(false);
    expect(isMissingRelation(null)).toBe(false);
  });
});

describe('rankSlatePlayers', () => {
  it('orders by total projected impact, best first', () => {
    const ranked = rankSlatePlayers([
      player({ nba_player_id: '1', name: 'Low', proj_pts: 8, impact: -1.2 }),
      player({ nba_player_id: '2', name: 'Volume', proj_pts: 26, impact: 1.4 }),
      player({ nba_player_id: '3', name: 'All-round', proj_pts: 17, impact: 4.8 }),
    ]);

    expect(ranked.map((p) => p.name)).toEqual(['All-round', 'Volume', 'Low']);
  });

  it('falls back to projected points when no impact was computed', () => {
    const ranked = rankSlatePlayers([
      player({ nba_player_id: '1', name: 'Low', proj_pts: 8 }),
      player({ nba_player_id: '2', name: 'High', proj_pts: 26 }),
      player({ nba_player_id: '3', name: 'Mid', proj_pts: 17 }),
    ]);

    expect(ranked.map((p) => p.name)).toEqual(['High', 'Mid', 'Low']);
  });

  it('sorts players without an impact score below those that have one', () => {
    const ranked = rankSlatePlayers([
      player({ nba_player_id: '1', name: 'Unscored', proj_pts: 30, impact: null }),
      player({ nba_player_id: '2', name: 'Scored', proj_pts: 4, impact: -3 }),
    ]);

    expect(ranked.map((p) => p.name)).toEqual(['Scored', 'Unscored']);
  });

  it('keeps a placeholder name below a real one at equal impact', () => {
    const ranked = rankSlatePlayers([
      player({
        nba_player_id: '1642850',
        name: 'NBA #1642850 (new roster)',
        name_is_placeholder: true,
        impact: 2,
        proj_pts: 12,
      }),
      player({ nba_player_id: '2544', name: 'Zeb Named', impact: 2, proj_pts: 12 }),
    ]);

    expect(ranked.map((p) => p.nba_player_id)).toEqual(['2544', '1642850']);
  });

  it('sorts unprojected players to the bottom — a null is unknown, not zero', () => {
    const ranked = rankSlatePlayers([
      player({ nba_player_id: '1', name: 'Unknown', proj_pts: null }),
      player({ nba_player_id: '2', name: 'Known', proj_pts: 2 }),
    ]);

    expect(ranked.map((p) => p.name)).toEqual(['Known', 'Unknown']);
  });

  it('breaks ties by name so the order is stable', () => {
    const ranked = rankSlatePlayers([
      player({ nba_player_id: '1', name: 'Zeb', proj_pts: 12 }),
      player({ nba_player_id: '2', name: 'Abe', proj_pts: 12 }),
    ]);

    expect(ranked.map((p) => p.name)).toEqual(['Abe', 'Zeb']);
  });

  it('caps each game at the published depth', () => {
    const many = Array.from({ length: 14 }, (_, i) =>
      player({ nba_player_id: String(i), name: `P${i}`, proj_pts: i })
    );

    expect(rankSlatePlayers(many)).toHaveLength(TOP_PLAYERS_PER_GAME);
  });

  it('does not mutate the input array', () => {
    const unsorted = [
      player({ nba_player_id: '1', name: 'Low', proj_pts: 4 }),
      player({ nba_player_id: '2', name: 'High', proj_pts: 30 }),
    ];

    rankSlatePlayers(unsorted);

    expect(unsorted.map((p) => p.name)).toEqual(['Low', 'High']);
  });
});

describe('uncondStat', () => {
  it('names the schedule-level twin of a stat', () => {
    expect(uncondStat('pts')).toBe('pts_uncond');
    expect(POINTS_UNCOND_STAT).toBe('pts_uncond');
  });
});

describe('resolvePlayerName', () => {
  it('passes a real name through', () => {
    expect(resolvePlayerName('LeBron James', '2544')).toEqual({
      name: 'LeBron James',
      placeholder: false,
    });
  });

  it('labels a missing name with the id rather than rendering blank', () => {
    const resolved = resolvePlayerName(null, '1642850');

    expect(resolved.placeholder).toBe(true);
    expect(resolved.name).toBe(`NBA #1642850 ${PLACEHOLDER_NAME_SUFFIX}`);
  });

  it('treats a blank or whitespace name as missing', () => {
    expect(resolvePlayerName('', '1').name).not.toBe('');
    expect(resolvePlayerName('   ', '1').placeholder).toBe(true);
    expect(resolvePlayerName(undefined, '1').placeholder).toBe(true);
  });

  it('trims a padded name instead of calling it a placeholder', () => {
    expect(resolvePlayerName('  Luka Doncic  ', '1629029')).toEqual({
      name: 'Luka Doncic',
      placeholder: false,
    });
  });
});

describe('poolRates', () => {
  it('is the pool-wide make rate, not the average of per-player rates', () => {
    const pool = [input({ fgm: 2, fga: 2 }), input({ fgm: 8, fga: 18 })];

    const rates = poolRates(pool);

    expect(rates.fg).toBeCloseTo(10 / 20, 6);
  });

  it('has no rate to divide by when the pool never shoots', () => {
    expect(poolRates([input({ fga: 0, fta: 0 })])).toEqual({ fg: 0, ft: 0 });
  });
});

describe('categoryValues', () => {
  it('scores percentages as attempt-weighted excess makes', () => {
    const values = categoryValues(input({ fgm: 10, fga: 20 }), { fg: 0.45, ft: 0.8 });

    expect(values.fg).toBeCloseTo(10 - 20 * 0.45, 6);
  });

  it('has no percentage value when half of a pair is missing', () => {
    const values = categoryValues(input({ ftm: 5, fta: null }), { fg: 0.45, ft: 0.8 });

    expect(values.ft).toBeNull();
  });

  it('covers the nine categories fantasyScore ranks on', () => {
    expect(Object.keys(categoryValues(input(), { fg: 0.45, ft: 0.8 })).sort()).toEqual(
      [...IMPACT_CATEGORIES].sort()
    );
  });
});

describe('impactScores', () => {
  it('is a z-score sum, so an average night on the slate is zero', () => {
    const pool = [input({ pts: 10 }), input({ pts: 20 }), input({ pts: 30 })];

    const scores = impactScores(pool);

    expect(scores[1]).toBe(0);
    expect(scores[0]).toBeCloseTo(-(scores[2] as number), 6);
  });

  it('rewards production spread across categories over points alone', () => {
    const filler = [input({ pts: 12 }), input({ pts: 16 }), input({ pts: 20 })];
    const scorer = input({ pts: 34, reb: 1, ast: 1, stl: 1, blk: 1, fg3m: 1 });
    const allRound = input({ pts: 22, reb: 9, ast: 7, stl: 2, blk: 2, fg3m: 3 });

    const [scorerImpact, allRoundImpact] = impactScores([scorer, allRound, ...filler]);

    expect(allRoundImpact).toBeGreaterThan(scorerImpact as number);
  });

  it('treats turnovers as a negative', () => {
    const [careful, careless] = impactScores([input({ tov: 1 }), input({ tov: 5 })]);

    expect(careful).toBeGreaterThan(careless as number);
  });

  it('scores nothing for a player the run only partly projected', () => {
    const [complete, partial] = impactScores([input(), input({ ast: null })]);

    expect(complete).not.toBeNull();
    expect(partial).toBeNull();
  });

  it('ranks on the categories a run actually emitted', () => {
    const sparse = (pts: number, ast: number): ImpactInput => {
      const entry = {} as ImpactInput;
      for (const stat of PROJECTED_STATS) entry[stat] = null;
      return { ...entry, pts, ast };
    };

    const scores = impactScores([sparse(10, 2), sparse(30, 9)]);

    expect(scores.every((s) => s !== null)).toBe(true);
    expect(scores[1]).toBeGreaterThan(scores[0] as number);
  });

  it('separates nobody on a category the whole pool agrees about', () => {
    expect(impactScores([input(), input()])).toEqual([0, 0]);
  });

  it('has nothing to score for an empty pool', () => {
    expect(impactScores([])).toEqual([]);
  });
});

describe('topImpactIds', () => {
  it('picks the best few by impact', () => {
    const top = topImpactIds(
      [
        player({ nba_player_id: 'a', impact: 1 }),
        player({ nba_player_id: 'b', impact: 5 }),
        player({ nba_player_id: 'c', impact: 3 }),
        player({ nba_player_id: 'd', impact: -2 }),
      ],
      SPOTLIGHT_PER_GAME
    );

    expect([...top].sort()).toEqual(['a', 'b', 'c']);
  });

  it('never spotlights an unscored player, however short the list', () => {
    const top = topImpactIds([player({ nba_player_id: 'a', impact: null })], 3);

    expect(top.size).toBe(0);
  });
});

describe('injuryOverlayFields', () => {
  const RUN_AT = '2026-02-04T16:15:00.000Z';

  function overlayRow(overrides: Record<string, unknown> = {}): Parameters<
    typeof injuryOverlayFields
  >[0] {
    return {
      nba_id: '2544',
      status_raw: 'Out',
      detail: 'Ankle',
      current_normalized: 'out',
      current_captured_at: new Date('2026-02-04T20:00:00.000Z'),
      run_normalized: 'out',
      ...overrides,
    };
  }

  it('is empty for a player with no overlay row at all', () => {
    expect(injuryOverlayFields(undefined, RUN_AT)).toEqual({
      injury_status: null,
      injury_status_raw: null,
      injury_detail: null,
      injury_as_of: null,
      injury_changed_after_run: false,
    });
  });

  it('is empty for a player neither listed now nor listed at the run', () => {
    const row = overlayRow({
      status_raw: null,
      detail: null,
      current_normalized: null,
      current_captured_at: null,
      run_normalized: null,
    });
    expect(injuryOverlayFields(row, RUN_AT).injury_status).toBeNull();
    expect(injuryOverlayFields(row, RUN_AT).injury_changed_after_run).toBe(false);
  });

  it('carries the designation without a change flag when the run already knew it', () => {
    const fields = injuryOverlayFields(overlayRow(), RUN_AT);
    expect(fields).toEqual({
      injury_status: 'out',
      injury_status_raw: 'Out',
      injury_detail: 'Ankle',
      injury_as_of: '2026-02-04T20:00:00.000Z',
      injury_changed_after_run: false,
    });
  });

  it('flags a designation the run did not know about', () => {
    const fields = injuryOverlayFields(overlayRow({ run_normalized: null }), RUN_AT);
    expect(fields.injury_status).toBe('out');
    expect(fields.injury_changed_after_run).toBe(true);
  });

  it('flags a bucket that moved after the run', () => {
    const fields = injuryOverlayFields(
      overlayRow({ current_normalized: 'out', run_normalized: 'questionable' }),
      RUN_AT
    );
    expect(fields.injury_changed_after_run).toBe(true);
  });

  it('flags a clearance: listed at the run, off the report now', () => {
    const fields = injuryOverlayFields(
      overlayRow({
        status_raw: null,
        detail: null,
        current_normalized: null,
        current_captured_at: null,
        run_normalized: 'out',
      }),
      RUN_AT
    );
    expect(fields.injury_status).toBeNull();
    expect(fields.injury_status_raw).toBeNull();
    expect(fields.injury_as_of).toBeNull();
    expect(fields.injury_changed_after_run).toBe(true);
  });

  it('never flags a change without a run boundary to compare against', () => {
    const fields = injuryOverlayFields(overlayRow({ run_normalized: null }), null);
    expect(fields.injury_status).toBe('out');
    expect(fields.injury_changed_after_run).toBe(false);
  });

  it('falls back to the unknown bucket when the snapshot has no normalized row', () => {
    const fields = injuryOverlayFields(
      overlayRow({ current_normalized: null, run_normalized: null }),
      RUN_AT
    );
    expect(fields.injury_status).toBe('unknown');
  });
});

describe('getLatestCompleteRun', () => {
  const queryMock = vi.mocked(query);

  beforeEach(() => {
    queryMock.mockReset();
  });

  it('selects only complete runs on the production channel', async () => {
    // arrange
    queryMock.mockResolvedValueOnce(
      pgResult([{ id: 7, model_version: 'v3', predicted_at: new Date('2026-09-30T12:00:00.000Z') }])
    );

    // act
    const run = await getLatestCompleteRun();

    // assert
    const [sql, params] = queryMock.mock.calls[0];
    expect(params).toEqual([COMPLETE_RUN_STATUS, PRODUCTION_CHANNEL]);
    expect(sql).toMatch(/channel = \$2/);
    expect(run).toEqual({ id: 7, model_version: 'v3', predicted_at: '2026-09-30T12:00:00.000Z' });
  });

  it('returns null when no production run exists', async () => {
    // arrange
    queryMock.mockResolvedValueOnce(pgResult([]));

    // act
    const run = await getLatestCompleteRun();

    // assert
    expect(run).toBeNull();
  });
});
