import { describe, it, expect, vi, beforeEach } from 'vitest';
import { query } from '../../src/db.js';
import { pgResult } from '../helpers/mockDb.js';
import { PRODUCTION_CHANNEL } from '../../src/services/slate.js';
import {
  getWatchlist,
  DEVIATION_STATS,
  DEVIATION_WEIGHTS,
  DEFAULT_WINDOW_DAYS,
  HOT_STREAK_STDDEV_MULTIPLE,
  IMPACT_PERCENTILE_FLOOR,
  MAX_WINDOW_DAYS,
  POSITION_FILTERS,
  REASON_CODES,
  RETURN_GAP_DAYS,
  RETURN_GAP_MAX_DAYS,
  RETURN_MIN_PROB_ACTIVE,
  ROLE_INCREASE_MIN_DELTA,
  SHOT_VOLUME_SURGE_FGA_DELTA,
  TEAMMATE_ABSENCE_MAX_PROB_ACTIVE,
  TEAMMATE_ABSENCE_MIN_MINUTES,
  UPSIDE_DRIVERS_SHOWN,
  WATCHLIST_LIMIT,
  deviationScales,
  evidenceFor,
  findAbsentTeammate,
  groupByDate,
  hasRoleIncrease,
  hasShotVolumeSurge,
  isHotStreak,
  isReturningFromAbsence,
  matchesPosition,
  opponentOf,
  parsePositionFilter,
  parsePositions,
  parseWindowDays,
  rankCandidates,
  reasonsFor,
  relevanceFor,
  scoreCandidates,
  shiftIsoDate,
  upsideOf,
  upsideScores,
  watchlistPool,
  watchlistScore,
  windowRange,
  type DeviationStat,
  type WatchlistCandidate,
} from '../../src/services/watchlist.js';
import { PROJECTED_STATS, poolDescriptor } from '../../src/services/slate.js';

function uncondLine(overrides: Partial<Record<string, number>> = {}) {
  const line = {} as Record<string, number | null>;
  for (const stat of PROJECTED_STATS) line[stat] = overrides[stat] ?? null;
  return line as WatchlistCandidate['uncond'];
}

function candidate(overrides: Partial<WatchlistCandidate> = {}): WatchlistCandidate {
  return {
    nba_player_id: '1001',
    name: 'Test Candidate',
    name_is_placeholder: false,
    team_abbr: 'LAL',
    position: parsePositions('SG,SF'),
    opponent_team_abbr: 'GSW',
    nba_game_id: '0022500555',
    game_date: '2026-02-04',
    prob_active: 0.9,
    impact: 1,
    proj_pts_uncond: 14,
    uncond: uncondLine({ pts: 14 }),
    baseline_games: 15,
    deltas: {},
    minutes: { usual: null, projected: null, delta: null },
    points: { usual: null, projected: null, delta: null },
    shots: { usual: null, projected: null, delta: null },
    days_since_played: null,
    last_played_date: null,
    pts_recent: null,
    pts_sd: null,
    teammate_out: null,
    ...overrides,
  };
}

function jumper(
  id: string,
  usualMin: number,
  projMin: number,
  impact: number,
  overrides: Partial<WatchlistCandidate> = {}
): WatchlistCandidate {
  return candidate({
    nba_player_id: id,
    name: `Player ${id}`,
    impact,
    deltas: { minutes: projMin - usualMin },
    minutes: { usual: usualMin, projected: projMin, delta: projMin - usualMin },
    ...overrides,
  });
}

describe('deviationScales', () => {
  it('measures each stat in the spread of that stat across the pool', () => {
    const pool = [
      { minutes: -2, pts: -4 },
      { minutes: 2, pts: 4 },
    ];

    const scales = deviationScales(pool);

    expect(scales.get('minutes')).toBe(2);
    expect(scales.get('pts')).toBe(4);
  });

  it('omits a stat nobody in the pool has a deviation for', () => {
    const scales = deviationScales([{ minutes: 3 }, { minutes: -1 }]);

    expect(scales.has('minutes')).toBe(true);
    expect(scales.has('blk')).toBe(false);
  });
});

describe('upsideOf', () => {
  it('is positive only when the projection is above the baseline', () => {
    const scales = new Map<DeviationStat, number>([['minutes', 4]]);

    expect(upsideOf({ minutes: 8 }, scales).upside).toBe(2);
    expect(upsideOf({ minutes: -8 }, scales).upside).toBe(-2);
    expect(upsideOf({ minutes: 0 }, scales).upside).toBe(0);
  });

  it('weights minutes above points, because minutes are the mechanism', () => {
    const scales = new Map<DeviationStat, number>([
      ['minutes', 1],
      ['pts', 1],
    ]);

    const fromMinutes = upsideOf({ minutes: 1, pts: 0 }, scales).upside as number;
    const fromPoints = upsideOf({ minutes: 0, pts: 1 }, scales).upside as number;

    expect(fromMinutes).toBeGreaterThan(fromPoints);
    expect(DEVIATION_WEIGHTS.minutes).toBeGreaterThan(DEVIATION_WEIGHTS.pts);
  });

  it('averages rather than sums, so a wider run does not score higher', () => {
    const two = new Map<DeviationStat, number>([
      ['minutes', 1],
      ['pts', 1],
    ]);
    const all = new Map<DeviationStat, number>(DEVIATION_STATS.map((stat) => [stat, 1]));
    const deltas = Object.fromEntries(DEVIATION_STATS.map((stat) => [stat, 1]));

    expect(upsideOf(deltas, two).upside).toBe(1);
    expect(upsideOf(deltas, all).upside).toBe(1);
  });

  it('reports the deviations that point up, biggest contribution first', () => {
    const scales = new Map<DeviationStat, number>([
      ['minutes', 1],
      ['pts', 1],
      ['reb', 1],
    ]);

    const { drivers } = upsideOf({ minutes: 1, pts: 3, reb: -2 }, scales);

    expect(drivers.map((d) => d.stat)).toEqual(['pts', 'minutes']);
    expect(drivers[0]).toEqual({ stat: 'pts', delta: 3, scaled: 3 });
  });

  it('lets a row explain a positive score whose minutes and points are flat', () => {
    const scales = new Map<DeviationStat, number>([
      ['minutes', 1],
      ['pts', 1],
      ['stl', 1],
      ['blk', 1],
    ]);

    const { upside, drivers } = upsideOf({ minutes: -0.2, pts: -0.1, stl: 1, blk: 1 }, scales);

    expect(upside as number).toBeGreaterThan(0);
    expect(drivers.map((d) => d.stat)).toEqual(['stl', 'blk']);
  });

  it('contributes zero for a stat the whole pool deviates on identically', () => {
    const scales = new Map<DeviationStat, number>([['minutes', 0]]);

    expect(upsideOf({ minutes: 5 }, scales).upside).toBe(0);
  });

  it('is null when there is no deviation at all to score', () => {
    expect(upsideOf({}, new Map([['minutes', 3]])).upside).toBeNull();
    expect(upsideOf({ minutes: 3 }, new Map()).upside).toBeNull();
  });
});

describe('upsideScores', () => {
  it('scores a pool against its own spread, in input order', () => {
    const scores = upsideScores([{ minutes: -2 }, { minutes: 2 }]);

    expect(scores).toEqual([-1, 1]);
  });

  it('returns nothing for an empty pool', () => {
    expect(upsideScores([])).toEqual([]);
  });
});

describe('relevanceFor', () => {
  const pool = Array.from({ length: 100 }, (_, i) => i);

  it('is exactly zero at and below the floor', () => {
    expect(relevanceFor(0, pool)).toBe(0);
    expect(relevanceFor(IMPACT_PERCENTILE_FLOOR - 10, pool)).toBe(0);
  });

  it('ramps to one at the top of the pool', () => {
    const best = relevanceFor(99, pool) as number;

    expect(best).toBeGreaterThan(0.9);
    expect(best).toBeLessThanOrEqual(1);
  });

  it('rises with the impact percentile', () => {
    const good = relevanceFor(90, pool) as number;
    const better = relevanceFor(95, pool) as number;

    expect(better).toBeGreaterThan(good);
  });

  it('is null when the run has no impact score for him', () => {
    expect(relevanceFor(null, pool)).toBeNull();
  });
});

describe('watchlistScore', () => {
  it('multiplies the two terms', () => {
    expect(watchlistScore(2, 0.5)).toBe(1);
  });

  it('clamps a projection below the baseline to zero rather than going negative', () => {
    expect(watchlistScore(-3, 0.9)).toBe(0);
  });

  it('zeroes out a huge jump that clears no absolute floor', () => {
    expect(watchlistScore(5, 0)).toBe(0);
  });

  it('is null when either factor is unknown', () => {
    expect(watchlistScore(null, 0.5)).toBeNull();
    expect(watchlistScore(2, null)).toBeNull();
  });
});

describe('hasRoleIncrease', () => {
  it('fires exactly at the threshold', () => {
    expect(hasRoleIncrease(ROLE_INCREASE_MIN_DELTA)).toBe(true);
  });

  it('stays quiet just below it, and on a minutes drop', () => {
    expect(hasRoleIncrease(ROLE_INCREASE_MIN_DELTA - 0.1)).toBe(false);
    expect(hasRoleIncrease(-8)).toBe(false);
  });

  it('needs a measurable deviation', () => {
    expect(hasRoleIncrease(null)).toBe(false);
  });
});

describe('hasShotVolumeSurge', () => {
  it('fires exactly at the threshold', () => {
    expect(hasShotVolumeSurge(SHOT_VOLUME_SURGE_FGA_DELTA)).toBe(true);
    expect(hasShotVolumeSurge(SHOT_VOLUME_SURGE_FGA_DELTA - 0.1)).toBe(false);
  });

  it('needs a measurable deviation', () => {
    expect(hasShotVolumeSurge(null)).toBe(false);
  });
});

describe('isReturningFromAbsence', () => {
  it('fires on a gap at the threshold when he is expected to play', () => {
    expect(isReturningFromAbsence(RETURN_GAP_DAYS, 0.95)).toBe(true);
  });

  it('ignores a shorter gap, which is just a rest day', () => {
    expect(isReturningFromAbsence(RETURN_GAP_DAYS - 1, 0.95)).toBe(false);
  });

  it('ignores an offseason — every player would fire on opening night', () => {
    expect(isReturningFromAbsence(RETURN_GAP_MAX_DAYS + 1, 0.95)).toBe(false);
    expect(isReturningFromAbsence(190, 0.95)).toBe(false);
  });

  it('does not call a player who is still out a returning player', () => {
    expect(isReturningFromAbsence(21, RETURN_MIN_PROB_ACTIVE - 0.01)).toBe(false);
    expect(isReturningFromAbsence(21, null)).toBe(false);
  });

  it('needs a measurable gap', () => {
    expect(isReturningFromAbsence(null, 0.95)).toBe(false);
  });
});

describe('isHotStreak', () => {
  it('fires when recent scoring beats the baseline by the stddev multiple', () => {
    expect(isHotStreak(20, 14, 4)).toBe(true);
    expect(isHotStreak(19.9, 14, 4)).toBe(false);
  });

  it('scales the bar by the player, not by a fixed point total', () => {
    expect(isHotStreak(19, 14, 2)).toBe(true);
    expect(isHotStreak(19, 14, 10)).toBe(false);
    expect(HOT_STREAK_STDDEV_MULTIPLE).toBe(1.5);
  });

  it('never fires with no volatility to scale by, or on a cold stretch', () => {
    expect(isHotStreak(20, 14, 0)).toBe(false);
    expect(isHotStreak(20, 14, null)).toBe(false);
    expect(isHotStreak(8, 14, 3)).toBe(false);
  });
});

describe('findAbsentTeammate', () => {
  const mate = (name: string, minutes: number | null, prob: number | null) => ({
    name,
    usual_minutes: minutes,
    prob_active: prob,
  });

  it('returns a rotation teammate the run does not expect to play', () => {
    const found = findAbsentTeammate([
      mate('Star Guard', TEAMMATE_ABSENCE_MIN_MINUTES, TEAMMATE_ABSENCE_MAX_PROB_ACTIVE),
    ]);

    expect(found).toEqual({
      name: 'Star Guard',
      usual_minutes: TEAMMATE_ABSENCE_MIN_MINUTES,
      prob_active: TEAMMATE_ABSENCE_MAX_PROB_ACTIVE,
    });
  });

  it('ignores a low-minutes teammate whose absence opens nothing', () => {
    expect(findAbsentTeammate([mate('Deep Bench', 12, 0.05)])).toBeNull();
  });

  it('ignores a teammate the run still expects on the floor', () => {
    expect(
      findAbsentTeammate([mate('Star Guard', 34, TEAMMATE_ABSENCE_MAX_PROB_ACTIVE + 0.01)])
    ).toBeNull();
    expect(findAbsentTeammate([mate('Star Guard', 34, null)])).toBeNull();
  });

  it('picks the highest-minutes absence, which is the usage actually freed', () => {
    const found = findAbsentTeammate([mate('Third Option', 29, 0.1), mate('Franchise', 36, 0.1)]);

    expect(found?.name).toBe('Franchise');
  });

  it('finds nobody on a slate with no injury news', () => {
    expect(findAbsentTeammate([mate('Star Guard', 34, 0.92), mate('Wing', 30, 0.88)])).toBeNull();
  });
});

describe('reasonsFor', () => {
  it('returns nothing for a player whose situation is unchanged', () => {
    expect(
      reasonsFor(
        candidate({
          minutes: { usual: 24, projected: 24, delta: 0 },
          shots: { usual: 9, projected: 9, delta: 0 },
        })
      )
    ).toEqual([]);
  });

  it('stacks every rule that fires, in code order', () => {
    const stacked = candidate({
      minutes: { usual: 22, projected: 31, delta: 9 },
      shots: { usual: 9, projected: 13, delta: 4 },
      points: { usual: 12, projected: 19, delta: 7 },
      days_since_played: 9,
      prob_active: 0.95,
      pts_recent: 20,
      pts_sd: 4,
      teammate_out: { name: 'Star Guard', usual_minutes: 34, prob_active: 0.1 },
    });

    expect(reasonsFor(stacked)).toEqual([...REASON_CODES]);
  });
});

describe('evidenceFor', () => {
  it('reports the numbers behind the reasons that fired, and nothing else', () => {
    const c = candidate({
      shots: { usual: 9.14, projected: 13.2, delta: 4.06 },
      points: { usual: 12, projected: 14, delta: 2 },
      pts_recent: 18,
      pts_sd: 3,
    });
    const reasons = reasonsFor(c);

    const evidence = evidenceFor(c, reasons);

    expect(reasons).toEqual(['SHOT_VOLUME_SURGE', 'HOT_STREAK']);
    expect(evidence).toEqual({
      fga_usual: 9.1,
      fga_projected: 13.2,
      fga_delta: 4.1,
      pts_recent: 18,
      pts_sd: 3,
      pts_recent_delta: 6,
    });
    expect(evidence.teammate_out).toBeUndefined();
    expect(evidence.days_since_played).toBeUndefined();
  });

  it('names the absent teammate and the minutes his absence frees', () => {
    const c = candidate({
      teammate_out: { name: 'Franchise Player', usual_minutes: 34.62, prob_active: 0.1234 },
    });

    const evidence = evidenceFor(c, reasonsFor(c));

    expect(evidence).toEqual({
      teammate_out: 'Franchise Player',
      teammate_out_minutes: 34.6,
      teammate_out_prob_active: 0.123,
    });
  });

  it('carries the return date alongside the gap', () => {
    const c = candidate({
      days_since_played: 12,
      last_played_date: '2026-01-23',
      prob_active: 0.9,
    });

    const evidence = evidenceFor(c, reasonsFor(c));

    expect(evidence).toEqual({ days_since_played: 12, last_played_date: '2026-01-23' });
  });
});

describe('opponentOf', () => {
  it('names the other side of the game', () => {
    expect(opponentOf('LAL', ['LAL', 'GSW'])).toBe('GSW');
    expect(opponentOf('GSW', ['LAL', 'GSW'])).toBe('LAL');
  });

  it('is null when the team is not in the game, or either side is unknown', () => {
    expect(opponentOf('BOS', ['LAL', 'GSW'])).toBeNull();
    expect(opponentOf(null, ['LAL', 'GSW'])).toBeNull();
    expect(opponentOf('LAL', undefined)).toBeNull();
  });
});

describe('scoreCandidates', () => {
  it('scores every candidate without filtering, in input order', () => {
    const pool = [jumper('1', 22, 31, 5), jumper('2', 22, 18, -5)];

    const scored = scoreCandidates(pool);

    expect(scored).toHaveLength(2);
    expect(scored.map((s) => s.candidate.nba_player_id)).toEqual(['1', '2']);
    expect(scored[1].score).toBe(0);
  });
});

describe('rankCandidates', () => {
  function filler(count: number, lo = -3, hi = 3): WatchlistCandidate[] {
    return Array.from({ length: count }, (_, i) =>
      jumper(`f${i}`, 10, 10, lo + ((hi - lo) * i) / Math.max(1, count - 1), {
        deltas: { minutes: 0 },
      })
    );
  }

  it('keeps a rotation player stepping into starter minutes above a bench jump', () => {
    const scrub = jumper('scrub', 5, 15, -2);
    const rotation = jumper('rotation', 24, 32, 4);

    const ranked = rankCandidates([scrub, rotation, ...filler(100)]);

    expect(ranked[0].nba_player_id).toBe('rotation');
    expect(ranked.map((p) => p.nba_player_id)).not.toContain('scrub');
  });

  it('drops a player projected below his own usual, however good he is', () => {
    const star = jumper('star', 36, 33, 9);

    const ranked = rankCandidates([star, ...filler(100)]);

    expect(ranked.map((p) => p.nba_player_id)).not.toContain('star');
  });

  it('drops a player the run has no impact score for', () => {
    const unscored = jumper('unscored', 22, 31, 0);

    const ranked = rankCandidates([{ ...unscored, impact: null }, ...filler(100)]);

    expect(ranked).toHaveLength(0);
  });

  it('drops a player with no deviation to score at all', () => {
    const noBaseline = candidate({ nba_player_id: 'nb', impact: 9, deltas: {} });

    const ranked = rankCandidates([noBaseline, ...filler(100)]);

    expect(ranked.map((p) => p.nba_player_id)).not.toContain('nb');
  });

  it('carries the deltas, the two factors and the reason codes on every row', () => {
    const stepping = jumper('rotation', 22, 31, 6, {
      points: { usual: 11.6, projected: 20, delta: 8.4 },
      deltas: { minutes: 9, pts: 8.4 },
    });

    const [row] = rankCandidates([stepping, ...filler(100)]);

    expect(row.minutes).toEqual({ usual: 22, projected: 31, delta: 9 });
    expect(row.points).toEqual({ usual: 11.6, projected: 20, delta: 8.4 });
    expect(row.reasons).toContain('ROLE_INCREASE');
    expect(row.score).toBeCloseTo(row.upside * row.relevance, 3);
    expect(row.impact_percentile).toBeGreaterThan(0);
    expect(row.drivers.length).toBeLessThanOrEqual(UPSIDE_DRIVERS_SHOWN);
    expect(row.opponent_team_abbr).toBe('GSW');
    expect(row.game_date).toBe('2026-02-04');
  });

  it('orders by score, and lets a named player win a tie against a placeholder', () => {
    const named = jumper('named', 22, 31, 5, { name: 'Aaa Named' });
    const placeholder = jumper('ph', 22, 31, 5, {
      name: 'NBA #99 (new roster)',
      name_is_placeholder: true,
    });

    const ranked = rankCandidates([placeholder, named, ...filler(100)]);

    expect(ranked[0].nba_player_id).toBe('named');
  });

  it('caps the list at the published limit, and respects an explicit one', () => {
    const many = Array.from({ length: 25 }, (_, i) => jumper(`p${i}`, 22, 31, 5 + i * 0.1));

    expect(rankCandidates([...many, ...filler(100)])).toHaveLength(WATCHLIST_LIMIT);
    expect(rankCandidates([...many, ...filler(100)], 3)).toHaveLength(3);
  });

  it('returns an empty list when nothing clears both terms', () => {
    expect(rankCandidates(filler(100))).toEqual([]);
  });

  it('reports one game and a per-game score equal to the total for one date', () => {
    const stepping = jumper('rotation', 22, 31, 6);

    const [row] = rankCandidates([stepping, ...filler(100)]);

    expect(row.games_count).toBe(1);
    expect(row.score_per_game).toBe(row.score);
    expect(row.score).toBeCloseTo(row.upside * row.relevance, 3);
    expect(row.games).toHaveLength(1);
    expect(row.games[0].game_date).toBe('2026-02-04');
  });

  it('carries the position through to the row, and null when there is none', () => {
    const guard = jumper('guard', 22, 31, 6, { position: parsePositions('PG,SG') });
    const unknown = jumper('unknown', 22, 31, 5, { position: parsePositions(null) });

    const ranked = rankCandidates([guard, unknown, ...filler(100)]);

    expect(ranked.find((p) => p.nba_player_id === 'guard')?.position).toBe('PG/SG');
    expect(ranked.find((p) => p.nba_player_id === 'unknown')?.position).toBeNull();
  });
});

describe('rankCandidates over a window', () => {
  function fillerOn(date: string, count: number, lo = -3, hi = 3): WatchlistCandidate[] {
    return Array.from({ length: count }, (_, i) =>
      jumper(`f${date}${i}`, 10, 10, lo + ((hi - lo) * i) / Math.max(1, count - 1), {
        deltas: { minutes: 0 },
        game_date: date,
        nba_game_id: `g${date}`,
      })
    );
  }

  function gameOn(
    id: string,
    date: string,
    usualMin: number,
    projMin: number,
    impact: number,
    overrides: Partial<WatchlistCandidate> = {}
  ): WatchlistCandidate {
    return jumper(id, usualMin, projMin, impact, {
      game_date: date,
      nba_game_id: `g${date}`,
      ...overrides,
    });
  }

  const WEEK = ['2026-02-04', '2026-02-05', '2026-02-06', '2026-02-07', '2026-02-08'];

  it('lets four decent games outrank two better ones — the streaming argument', () => {
    const busy = WEEK.slice(0, 4).map((date) => gameOn('busy', date, 24, 30, 4));
    const rested = WEEK.slice(0, 2).map((date) => gameOn('rested', date, 24, 33, 6));
    const pool = WEEK.flatMap((date) => fillerOn(date, 60));

    const ranked = rankCandidates([...busy, ...rested, ...pool]);
    const busyRow = ranked.find((p) => p.nba_player_id === 'busy');
    const restedRow = ranked.find((p) => p.nba_player_id === 'rested');

    expect(busyRow?.games_count).toBe(4);
    expect(restedRow?.games_count).toBe(2);
    expect(busyRow!.score).toBeGreaterThan(restedRow!.score);
    expect(busyRow!.score_per_game).toBeLessThan(restedRow!.score_per_game);
    expect(ranked.indexOf(busyRow!)).toBeLessThan(ranked.indexOf(restedRow!));
  });

  it('sums the per-game scores rather than averaging them', () => {
    const two = WEEK.slice(0, 2).map((date) => gameOn('twice', date, 22, 31, 6));
    const pool = WEEK.slice(0, 2).flatMap((date) => fillerOn(date, 60));

    const [row] = rankCandidates([...two, ...pool]);

    expect(row.games_count).toBe(2);
    expect(row.score).toBeCloseTo(2 * row.score_per_game, 3);
    expect(row.score).toBeCloseTo(2 * row.upside * row.relevance, 2);
  });

  it('never lets a flat night debit a big one', () => {
    const mixed = [gameOn('mixed', WEEK[0], 22, 31, 6), gameOn('mixed', WEEK[1], 22, 14, 6)];
    const onlyBig = [gameOn('onlyBig', WEEK[0], 22, 31, 6)];
    const pool = WEEK.slice(0, 2).flatMap((date) => fillerOn(date, 60));

    const ranked = rankCandidates([...mixed, ...onlyBig, ...pool]);
    const mixedRow = ranked.find((p) => p.nba_player_id === 'mixed');

    expect(mixedRow?.games_count).toBe(2);
    expect(mixedRow!.games.find((g) => g.game_date === WEEK[1])?.score).toBe(0);
    expect(mixedRow!.score).toBeGreaterThan(0);
  });

  it('scores each night against its own slate, not against the window', () => {
    const quiet = [
      gameOn('same', WEEK[0], 22, 31, 2),
      ...fillerOn(WEEK[0], 2, -1, 0),
    ];
    const loaded = [gameOn('same', WEEK[1], 22, 31, 2), ...fillerOn(WEEK[1], 60, 3, 9)];

    const [row] = rankCandidates([...quiet, ...loaded]);
    const quietGame = row.games.find((g) => g.game_date === WEEK[0]);
    const loadedGame = row.games.find((g) => g.game_date === WEEK[1]);

    expect(quietGame!.score).toBeGreaterThan(0);
    expect(loadedGame!.score).toBe(0);
  });

  it('explains a row with its best-scoring game, and lists the rest in order', () => {
    const games = [
      gameOn('wing', WEEK[2], 22, 24, 6),
      gameOn('wing', WEEK[0], 22, 34, 6, { shots: { usual: 8, projected: 13, delta: 5 } }),
      gameOn('wing', WEEK[1], 22, 23, 6),
    ];
    const pool = WEEK.slice(0, 3).flatMap((date) => fillerOn(date, 60));

    const [row] = rankCandidates([...games, ...pool]);

    expect(row.game_date).toBe(WEEK[0]);
    expect(row.reasons).toContain('SHOT_VOLUME_SURGE');
    expect(row.evidence.fga_delta).toBe(5);
    expect(row.games.map((g) => g.game_date)).toEqual([WEEK[0], WEEK[1], WEEK[2]]);
  });

  it('averages the intensities and sums the quantities', () => {
    const games = [gameOn('wing', WEEK[0], 22, 30, 6), gameOn('wing', WEEK[1], 22, 34, 6)];
    games[0].uncond = { ...games[0].uncond, pts: 18 };
    games[1].uncond = { ...games[1].uncond, pts: 22 };
    games[0].prob_active = 0.9;
    games[1].prob_active = 0.7;
    const pool = WEEK.slice(0, 2).flatMap((date) => fillerOn(date, 60));

    const [row] = rankCandidates([...games, ...pool]);

    expect(row.minutes).toEqual({ usual: 22, projected: 32, delta: 10 });
    expect(row.prob_active).toBe(0.8);
    expect(row.totals.pts).toBe(40);
    expect(row.impact).toBe(12);
    expect(row.games.map((g) => g.proj_pts)).toEqual([18, 22]);
  });

  it('counts a night the run cannot score as a game that contributes nothing', () => {
    const games = [
      gameOn('wing', WEEK[0], 22, 31, 6),
      gameOn('wing', WEEK[1], 22, 31, 6, { impact: null }),
    ];
    const pool = WEEK.slice(0, 2).flatMap((date) => fillerOn(date, 60));

    const [row] = rankCandidates([...games, ...pool]);

    expect(row.games_count).toBe(2);
    expect(row.games.find((g) => g.game_date === WEEK[1])?.score).toBe(0);
    expect(row.score).toBe(row.games[0].score);
  });

  it('drops a player whose every night in the window is flat', () => {
    expect(rankCandidates(WEEK.flatMap((date) => fillerOn(date, 60)))).toEqual([]);
  });

  it('keeps the limit a count of PLAYERS, not of player-games', () => {
    const many = Array.from({ length: 25 }, (_, i) =>
      WEEK.slice(0, 3).map((date) => gameOn(`p${i}`, date, 22, 31, 5 + i * 0.1))
    ).flat();
    const pool = WEEK.slice(0, 3).flatMap((date) => fillerOn(date, 60));

    const ranked = rankCandidates([...many, ...pool]);

    expect(ranked).toHaveLength(WATCHLIST_LIMIT);
    expect(new Set(ranked.map((p) => p.nba_player_id)).size).toBe(WATCHLIST_LIMIT);
  });

  it('filters to a position after scoring, so the limit fills with that position', () => {
    const forwards = Array.from({ length: 20 }, (_, i) =>
      gameOn(`fwd${i}`, WEEK[0], 22, 31, 6 + i * 0.1, { position: parsePositions('SF,PF') })
    );
    const guards = Array.from({ length: 3 }, (_, i) =>
      gameOn(`gd${i}`, WEEK[0], 22, 29, 4 + i * 0.1, { position: parsePositions('PG,SG') })
    );
    const pool = fillerOn(WEEK[0], 60);

    const all = rankCandidates([...forwards, ...guards, ...pool]);
    const onlyGuards = rankCandidates([...forwards, ...guards, ...pool], WATCHLIST_LIMIT, 'G');

    expect(all.map((p) => p.nba_player_id)).not.toContain('gd0');
    expect(onlyGuards).toHaveLength(3);
    expect(onlyGuards.every((p) => p.position === 'PG/SG')).toBe(true);
  });

  it('includes a player with no position only when no position is asked for', () => {
    const unknown = gameOn('unknown', WEEK[0], 22, 31, 6, { position: parsePositions(null) });
    const pool = fillerOn(WEEK[0], 60);

    expect(rankCandidates([unknown, ...pool]).map((p) => p.nba_player_id)).toContain('unknown');
    expect(rankCandidates([unknown, ...pool], WATCHLIST_LIMIT, 'G')).toEqual([]);
    expect(rankCandidates([unknown, ...pool], WATCHLIST_LIMIT, 'C')).toEqual([]);
  });
});

describe('groupByDate', () => {
  it('splits a window into one pool per date, earliest first', () => {
    const rows = [
      candidate({ nba_player_id: 'b', game_date: '2026-02-06' }),
      candidate({ nba_player_id: 'a', game_date: '2026-02-04' }),
      candidate({ nba_player_id: 'c', game_date: '2026-02-04' }),
    ];

    const grouped = groupByDate(rows);

    expect(grouped.map((g) => g.date)).toEqual(['2026-02-04', '2026-02-06']);
    expect(grouped[0].candidates.map((c) => c.nba_player_id)).toEqual(['a', 'c']);
  });

  it('returns nothing for nothing', () => {
    expect(groupByDate([])).toEqual([]);
  });
});

describe('parsePositions', () => {
  it('reads the comma-joined form the players table actually stores', () => {
    const parsed = parsePositions('PG,SG');

    expect(parsed.positions).toEqual(['PG', 'SG']);
    expect(parsed.buckets).toEqual(['G']);
    expect(parsed.label).toBe('PG/SG');
  });

  it('puts a combo player in both of his buckets', () => {
    const parsed = parsePositions('SG,SF');

    expect(parsed.buckets).toEqual(['G', 'F']);
    expect(parsed.label).toBe('SG/SF');
  });

  it('buckets a centre as a centre, in both vocabularies', () => {
    const parsed = parsePositions('C,PF');

    expect(parsed.positions).toEqual(['C', 'PF']);
    expect(parsed.buckets).toEqual(['F', 'C']);
  });

  it('accepts slashes and the bucket-only shorthand a different scrape may write', () => {
    const slashed = parsePositions('PG/SG');
    const shorthand = parsePositions('G-F');

    expect(slashed.positions).toEqual(['PG', 'SG']);
    expect(shorthand.positions).toEqual([]);
    expect(shorthand.buckets).toEqual(['G', 'F']);
    expect(shorthand.label).toBe('G/F');
  });

  it('normalises case and whitespace, and drops what it cannot read', () => {
    expect(parsePositions(' pg , sg ').positions).toEqual(['PG', 'SG']);
    expect(parsePositions('PG,WING').positions).toEqual(['PG']);
    expect(parsePositions('PG,PG').positions).toEqual(['PG']);
  });

  it('has nothing to say about a player with no roster row', () => {
    for (const raw of [null, undefined, '', '   ', 'utility']) {
      expect(parsePositions(raw)).toEqual({ positions: [], buckets: [], label: null });
    }
  });
});

describe('matchesPosition', () => {
  const combo = parsePositions('SG,SF');
  const center = parsePositions('C');
  const nothing = parsePositions(null);

  it('answers a bucket filter from the buckets', () => {
    expect(matchesPosition(combo, 'G')).toBe(true);
    expect(matchesPosition(combo, 'F')).toBe(true);
    expect(matchesPosition(combo, 'C')).toBe(false);
  });

  it('answers a specific filter from the specific positions', () => {
    expect(matchesPosition(combo, 'SG')).toBe(true);
    expect(matchesPosition(combo, 'PG')).toBe(false);
    expect(matchesPosition(center, 'C')).toBe(true);
  });

  it('matches everyone when no position is asked for, including the unknowns', () => {
    expect(matchesPosition(nothing, null)).toBe(true);
    expect(matchesPosition(combo, null)).toBe(true);
  });

  it('excludes an unknown position from every specific filter', () => {
    for (const filter of POSITION_FILTERS) {
      expect(matchesPosition(nothing, filter)).toBe(false);
    }
  });
});

describe('parsePositionFilter', () => {
  it('reads every published filter, in any case', () => {
    for (const filter of POSITION_FILTERS) {
      expect(parsePositionFilter(filter)).toBe(filter);
      expect(parsePositionFilter(filter.toLowerCase())).toBe(filter);
    }
  });

  it('treats absent, empty and "any" as no filter at all', () => {
    for (const raw of [undefined, null, '', '  ', 'any', 'ALL']) {
      expect(parsePositionFilter(raw)).toBeNull();
    }
  });

  it('rejects a value it cannot honour rather than ignoring it', () => {
    expect(parsePositionFilter('WING')).toBe(false);
    expect(parsePositionFilter('pg,sg')).toBe(false);
    expect(parsePositionFilter(3)).toBe(false);
  });
});

describe('parseWindowDays', () => {
  it('defaults to a single day, which is what an older client asked for', () => {
    expect(parseWindowDays(undefined)).toBe(DEFAULT_WINDOW_DAYS);
    expect(parseWindowDays('')).toBe(1);
  });

  it('accepts every whole number up to the cap', () => {
    expect(parseWindowDays('1')).toBe(1);
    expect(parseWindowDays('7')).toBe(7);
    expect(parseWindowDays(String(MAX_WINDOW_DAYS))).toBe(MAX_WINDOW_DAYS);
  });

  it('rejects rather than clamps, so a wrong answer never looks right', () => {
    expect(parseWindowDays('0')).toBeNull();
    expect(parseWindowDays(String(MAX_WINDOW_DAYS + 1))).toBeNull();
    expect(parseWindowDays('7.5')).toBeNull();
    expect(parseWindowDays('week')).toBeNull();
    expect(parseWindowDays({})).toBeNull();
  });
});

describe('shiftIsoDate and windowRange', () => {
  it('walks the calendar in UTC, across a month boundary', () => {
    expect(shiftIsoDate('2026-02-04', 6)).toBe('2026-02-10');
    expect(shiftIsoDate('2026-02-25', 7)).toBe('2026-03-04');
    expect(shiftIsoDate('2026-02-04', 0)).toBe('2026-02-04');
  });

  it('makes a one-day window start and end on the same date', () => {
    expect(windowRange('2026-02-04', 1)).toEqual({
      from: '2026-02-04',
      to: '2026-02-04',
      days: 1,
    });
    expect(windowRange('2026-02-04', 7)).toEqual({
      from: '2026-02-04',
      to: '2026-02-10',
      days: 7,
    });
  });
});

describe('watchlistPool', () => {
  it('echoes the slate descriptor verbatim for a single day', () => {
    expect(watchlistPool(244, 1)).toEqual(poolDescriptor(244));
  });

  it('says a longer window is scored one night at a time', () => {
    const pool = watchlistPool(1200, 7);

    expect(pool.key).toBe(poolDescriptor(0).key);
    expect(pool.sample_size).toBe(1200);
    expect(pool.label).toBe("Each night's slate");
    expect(pool.definition).toMatch(/each night in the window is scored against its own slate/);
  });
});

describe('getWatchlist run selection', () => {
  const queryMock = vi.mocked(query);

  beforeEach(() => {
    queryMock.mockReset();
  });

  it('looks up its run on the production channel and serves nothing without one', async () => {
    // arrange
    queryMock.mockResolvedValueOnce(pgResult([]));

    // act
    const response = await getWatchlist('2026-02-04');

    // assert
    expect(queryMock.mock.calls[0][1]).toContain(PRODUCTION_CHANNEL);
    expect(response.run).toBeNull();
  });
});
