import { describe, it, expect } from 'vitest';
import {
  rankTradeTargets,
  rankWaiverCandidates,
  type PlayerWindowProjection,
  type RankingPlayer,
} from '../../src/services/candidateRanking.js';
import { zScoreRank } from '../../src/services/fantasyScore.js';
import type { ProjectedStat } from '../../src/services/slate.js';

function player(id: number, overrides: Partial<RankingPlayer> = {}): RankingPlayer {
  return {
    id,
    nba_id: String(1000 + id),
    name: `Player ${id}`,
    team: 'BOS',
    position: 'SF',
    points_per_game: 12,
    rebounds_per_game: 5,
    assists_per_game: 3,
    steals_per_game: 0.8,
    blocks_per_game: 0.5,
    three_pointers_made: 1.2,
    turnovers_per_game: 1.5,
    field_goal_percentage: 46,
    free_throw_percentage: 78,
    ...overrides,
  };
}

// a spread of ordinary players so every category has a non-zero standard deviation
function fillerPool(): RankingPlayer[] {
  return Array.from({ length: 12 }, (_, i) =>
    player(100 + i, {
      points_per_game: 8 + i,
      rebounds_per_game: 3 + (i % 5),
      assists_per_game: 1 + (i % 4),
      steals_per_game: 0.5 + (i % 3) * 0.3,
      blocks_per_game: 0.2 + (i % 4) * 0.3,
      three_pointers_made: 0.5 + (i % 3) * 0.6,
      turnovers_per_game: 1 + (i % 3) * 0.5,
    })
  );
}

function totals(values: Partial<Record<ProjectedStat, number>>): Record<ProjectedStat, number | null> {
  return {
    pts: null, reb: null, ast: null, stl: null, blk: null, tov: null,
    fg3m: null, fgm: null, fga: null, ftm: null, fta: null,
    ...values,
  };
}

const THREE_GAMES = new Map([['BOS', 3], ['NYK', 3], ['LAL', 3]]);
const NO_PROJECTIONS = new Map<string, PlayerWindowProjection>();

describe('rankWaiverCandidates', () => {
  it('returns the same order on every call, sorted by score', () => {
    // arrange
    const roster = [player(1), player(2)];
    const pool = fillerPool();
    const options = { scheduledGames: THREE_GAMES };

    // act
    const first = rankWaiverCandidates(roster, pool, NO_PROJECTIONS, options);
    const second = rankWaiverCandidates(roster, [...pool].reverse(), NO_PROJECTIONS, options);

    // assert
    expect(second.map((c) => c.id)).toEqual(first.map((c) => c.id));
    const scores = first.map((c) => c.score);
    expect(scores).toEqual([...scores].sort((a, b) => b - a));
  });

  it("prefers a player who fills the roster's weak category over a higher raw scorer", () => {
    // arrange
    const strongNoBlocks = { points_per_game: 26, rebounds_per_game: 10, assists_per_game: 8,
      steals_per_game: 1.8, three_pointers_made: 3, blocks_per_game: 0 };
    const roster = [player(1, strongNoBlocks), player(2, strongNoBlocks), player(3, strongNoBlocks)];
    const scorer = player(50, { name: 'Scorer', points_per_game: 24, rebounds_per_game: 9,
      assists_per_game: 7, steals_per_game: 1.6, three_pointers_made: 2.8, blocks_per_game: 0 });
    const rimProtector = player(51, { name: 'Rim Protector', points_per_game: 9, rebounds_per_game: 7,
      assists_per_game: 1, steals_per_game: 0.6, three_pointers_made: 0, blocks_per_game: 3.2 });
    const pool = [...fillerPool(), scorer, rimProtector];
    const raw = new Map(zScoreRank([...roster, ...pool]).map((p) => [p.id, p.z_score]));

    // act
    const ranked = rankWaiverCandidates(roster, pool, NO_PROJECTIONS, { scheduledGames: THREE_GAMES });

    // assert
    expect(raw.get(50)).toBeGreaterThan(raw.get(51) as number);
    const ids = ranked.map((c) => c.id);
    expect(ids.indexOf(51)).toBeLessThan(ids.indexOf(50));
    expect(ranked.find((c) => c.id === 51)?.drivers[0].category).toBe('blk');
  });

  it('uses projections when the store has them and season averages otherwise', () => {
    // arrange
    const projected = player(60, { team: 'NYK' });
    const seasonOnly = player(61, { team: 'LAL' });
    const projections = new Map<string, PlayerWindowProjection>([
      [projected.nba_id as string, {
        games: 2,
        mean_prob_active: 0.85,
        totals: totals({ pts: 30, reb: 12, ast: 6, stl: 2, blk: 1, tov: 3, fg3m: 3,
          fgm: 11, fga: 22, ftm: 5, fta: 6 }),
      }],
    ]);

    // act
    const ranked = rankWaiverCandidates([player(1)], [projected, seasonOnly], projections, {
      scheduledGames: new Map([['NYK', 3], ['LAL', 4]]),
    });

    // assert
    const byId = new Map(ranked.map((c) => [c.id, c]));
    expect(byId.get(60)).toMatchObject({ basis: 'projection', projected_games: 2, mean_prob_play: 0.85 });
    expect(byId.get(61)).toMatchObject({ basis: 'season_average', projected_games: 4, mean_prob_play: null });
  });

  it('gives an idle team zero games and falls back to per game when no schedule is known', () => {
    // arrange
    const idle = player(70, { team: 'MIA' });

    // act
    const withSchedule = rankWaiverCandidates([player(1)], [idle], NO_PROJECTIONS, {
      scheduledGames: THREE_GAMES,
    });
    const withoutSchedule = rankWaiverCandidates([player(1)], [idle], NO_PROJECTIONS, {
      scheduledGames: new Map(),
    });

    // assert
    expect(withSchedule[0]).toMatchObject({ basis: 'season_average', projected_games: 0 });
    expect(withoutSchedule[0]).toMatchObject({ basis: 'season_average', projected_games: null });
  });

  it('breaks score ties by id and never lists a rostered player', () => {
    // arrange
    const roster = [player(1)];
    const pool = [player(9), player(4), player(1), ...fillerPool()];

    // act
    const ranked = rankWaiverCandidates(roster, pool, NO_PROJECTIONS, { scheduledGames: THREE_GAMES });

    // assert
    const ids = ranked.map((c) => c.id);
    expect(ids).not.toContain(1);
    expect(ranked.find((c) => c.id === 4)?.score).toBe(ranked.find((c) => c.id === 9)?.score);
    expect(ids.indexOf(4)).toBe(ids.indexOf(9) - 1);
  });

  it('caps the list at 25 by default', () => {
    // arrange
    const pool = Array.from({ length: 40 }, (_, i) => player(200 + i, { points_per_game: 5 + i * 0.5 }));

    // act
    const ranked = rankWaiverCandidates([player(1)], pool, NO_PROJECTIONS, { scheduledGames: THREE_GAMES });

    // assert
    expect(ranked).toHaveLength(25);
  });
});

describe('rankTradeTargets', () => {
  it('caps the list at 20 by default and reports three drivers per target', () => {
    // arrange
    const pool = Array.from({ length: 30 }, (_, i) => player(300 + i, { rebounds_per_game: 2 + i * 0.4 }));

    // act
    const ranked = rankTradeTargets([player(1)], pool, NO_PROJECTIONS, { scheduledGames: THREE_GAMES });

    // assert
    expect(ranked).toHaveLength(20);
    expect(ranked.every((c) => c.drivers.length === 3)).toBe(true);
  });
});
