import { describe, it, expect } from 'vitest';
import {
  HOT_STREAK_STDDEV_MULTIPLE,
  RETURN_GAP_DAYS,
  RETURN_MIN_PROB_ACTIVE,
  ROLE_INCREASE_MIN_DELTA,
  SHOT_VOLUME_SURGE_FGA_DELTA,
  TEAMMATE_ABSENCE_MAX_PROB_ACTIVE,
  TEAMMATE_ABSENCE_MIN_MINUTES,
  evidenceFor,
  groupTeammates,
  reasonInputFor,
  reasonsFor,
  teammatesOf,
  type ConditionalLine,
  type ReasonInput,
} from '../../src/services/projectionReasons.js';
import type { PlayerBaseline } from '../../src/services/baselines.js';

function input(overrides: Partial<ReasonInput> = {}): ReasonInput {
  return {
    prob_active: 0.95,
    baseline_games: 15,
    deltas: {},
    minutes: { usual: 30, projected: 30, delta: 0 },
    points: { usual: 15, projected: 15, delta: 0 },
    shots: { usual: 12, projected: 12, delta: 0 },
    days_since_played: 2,
    last_played_date: '2026-02-02',
    pts_recent: 15,
    pts_sd: 5,
    teammate_out: null,
    ...overrides,
  };
}

function baseline(overrides: Partial<PlayerBaseline> = {}): PlayerBaseline {
  return {
    nba_player_id: '1',
    games: 15,
    avg: { minutes: 24, pts: 10, reb: 4, ast: 3, stl: 1, blk: 0.5, fg3m: 1, fga: 8 },
    pts_recent: 10,
    pts_sd: 4,
    last_played_date: '2026-02-02',
    ...overrides,
  };
}

const LINE: ConditionalLine = { pts: 14, reb: 5, ast: 3, stl: 1, blk: 0.5, fg3m: 2, fga: 11 };

describe('reasonsFor', () => {
  it('returns nothing when the projection looks like his usual', () => {
    // act + assert
    expect(reasonsFor(input())).toEqual([]);
  });

  it('flags a role increase at the minutes bar', () => {
    // arrange
    const bumped = input({ minutes: { usual: 24, projected: 24 + ROLE_INCREASE_MIN_DELTA, delta: ROLE_INCREASE_MIN_DELTA } });

    // act
    const reasons = reasonsFor(bumped);

    // assert
    expect(reasons).toEqual(['ROLE_INCREASE']);
  });

  it('flags a shot volume surge', () => {
    // arrange
    const surge = input({ shots: { usual: 8, projected: 11, delta: SHOT_VOLUME_SURGE_FGA_DELTA + 0.5 } });

    // act
    const reasons = reasonsFor(surge);

    // assert
    expect(reasons).toEqual(['SHOT_VOLUME_SURGE']);
  });

  it('flags a return from a week or more out when he is expected to play', () => {
    // arrange
    const back = input({ days_since_played: RETURN_GAP_DAYS + 3, prob_active: RETURN_MIN_PROB_ACTIVE });

    // act
    const reasons = reasonsFor(back);

    // assert
    expect(reasons).toEqual(['RETURNING_FROM_ABSENCE']);
  });

  it('flags a hot streak well above his usual scoring', () => {
    // arrange
    const hot = input({ pts_recent: 15 + HOT_STREAK_STDDEV_MULTIPLE * 5 + 1, pts_sd: 5 });

    // act
    const reasons = reasonsFor(hot);

    // assert
    expect(reasons).toEqual(['HOT_STREAK']);
  });

  it('flags a heavy-minutes teammate the run does not expect to play', () => {
    // arrange
    const freed = input({
      teammate_out: { name: 'Big Starter', usual_minutes: 34, prob_active: 0.1 },
    });

    // act
    const reasons = reasonsFor(freed);

    // assert
    expect(reasons).toEqual(['TEAMMATE_ABSENCE']);
  });
});

describe('evidenceFor', () => {
  it('carries only the numbers behind the reasons that fired', () => {
    // arrange
    const freed = input({
      shots: { usual: 8.04, projected: 11.26, delta: 3.22 },
      teammate_out: { name: 'Big Starter', usual_minutes: 34.04, prob_active: 0.1234 },
    });

    // act
    const evidence = evidenceFor(freed, reasonsFor(freed));

    // assert
    expect(evidence).toEqual({
      fga_usual: 8,
      fga_projected: 11.3,
      fga_delta: 3.2,
      teammate_out: 'Big Starter',
      teammate_out_minutes: 34,
      teammate_out_prob_active: 0.123,
    });
  });

  it('is empty when nothing fired', () => {
    // act + assert
    expect(evidenceFor(input(), [])).toEqual({});
  });
});

describe('reasonInputFor', () => {
  it('compares the conditional line to his usual and dates the gap from the game', () => {
    // act
    const result = reasonInputFor({
      gameDate: '2026-02-12',
      probActive: 0.9,
      minutes: 31,
      conditional: LINE,
      baseline: baseline(),
      teammates: [],
    });

    // assert
    expect(result.minutes).toEqual({ usual: 24, projected: 31, delta: 7 });
    expect(result.points).toEqual({ usual: 10, projected: 14, delta: 4 });
    expect(result.shots).toEqual({ usual: 8, projected: 11, delta: 3 });
    expect(result.deltas).toMatchObject({ minutes: 7, pts: 4, reb: 1, fg3m: 1 });
    expect(result.days_since_played).toBe(10);
    expect(reasonsFor(result)).toEqual([
      'ROLE_INCREASE',
      'SHOT_VOLUME_SURGE',
      'RETURNING_FROM_ABSENCE',
    ]);
  });

  it('names the absent teammate with the most minutes', () => {
    // act
    const result = reasonInputFor({
      gameDate: '2026-02-04',
      probActive: 0.9,
      minutes: 24,
      conditional: LINE,
      baseline: baseline(),
      teammates: [
        { name: 'Sixth Man', usual_minutes: TEAMMATE_ABSENCE_MIN_MINUTES, prob_active: 0 },
        { name: 'Star', usual_minutes: 36, prob_active: TEAMMATE_ABSENCE_MAX_PROB_ACTIVE },
        { name: 'Healthy', usual_minutes: 38, prob_active: 0.9 },
      ],
    });

    // assert
    expect(result.teammate_out).toEqual({ name: 'Star', usual_minutes: 36, prob_active: 0.35 });
  });
});

describe('teammatesOf', () => {
  it('lists the rest of his team in the same game, never himself or the opponent', () => {
    // arrange
    const groups = groupTeammates([
      { id: '1', game_id: 'g1', team_abbr: 'OKC', name: 'Him', usual_minutes: 30, prob_active: 1 },
      { id: '2', game_id: 'g1', team_abbr: 'OKC', name: 'Mate', usual_minutes: 32, prob_active: 0.1 },
      { id: '3', game_id: 'g1', team_abbr: 'LAL', name: 'Rival', usual_minutes: 35, prob_active: 0 },
      { id: '4', game_id: 'g2', team_abbr: 'OKC', name: 'Other Night', usual_minutes: 35, prob_active: 0 },
    ]);

    // act
    const mates = teammatesOf({ id: '1', game_id: 'g1', team_abbr: 'OKC' }, groups);

    // assert
    expect(mates).toEqual([{ name: 'Mate', usual_minutes: 32, prob_active: 0.1 }]);
  });
});
