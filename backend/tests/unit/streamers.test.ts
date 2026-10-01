import { describe, it, expect } from 'vitest';
import {
  rankStreamers,
  rankingPools,
  type PlayerWindowProjection,
  type RankedCandidate,
  type RankingPlayer,
} from '../../src/services/candidateRanking.js';
import { formatSignedWins } from '../../src/services/decisionText.js';
import { streamerSentence, toStreamer } from '../../src/services/streamers.js';
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

const PER_GAME: Record<ProjectedStat, number> = {
  pts: 14, reb: 6, ast: 3, stl: 1, blk: 0.7, tov: 1.5, fg3m: 1.5, fgm: 5.5, fga: 12, ftm: 2, fta: 2.6,
};

function projection(games: number, talent = 1): PlayerWindowProjection {
  const totals = {} as Record<ProjectedStat, number | null>;
  for (const [stat, value] of Object.entries(PER_GAME)) {
    totals[stat as ProjectedStat] = value * talent * games;
  }
  return { games, totals, mean_prob_active: 0.95 };
}

describe('rankStreamers', () => {
  it('ranks a four-game player above a two-game player of similar per-game talent', () => {
    // arrange
    const roster = [player(1), player(2), player(3)];
    const fourGames = player(50, { name: 'Four Games' });
    const twoGames = player(51, { name: 'Two Games' });
    const projections = new Map([
      [fourGames.nba_id as string, projection(4)],
      [twoGames.nba_id as string, projection(2, 1.1)],
    ]);

    // act
    const ranked = rankStreamers(roster, [twoGames, fourGames, ...fillerPool()], projections, {
      scheduledGames: new Map([['BOS', 3]]),
      limit: 20,
    });

    // assert
    const four = ranked.find((c) => c.id === 50) as RankedCandidate;
    const two = ranked.find((c) => c.id === 51) as RankedCandidate;
    expect(ranked[0].id).toBe(50);
    expect(four.projected_games).toBe(4);
    expect(two.projected_games).toBe(2);
    expect(four.score).toBeGreaterThan(two.score);
  });

  it('returns at most five streamers', () => {
    // act
    const ranked = rankStreamers([player(1)], fillerPool(), new Map(), {
      scheduledGames: new Map([['BOS', 3]]),
    });

    // assert
    expect(ranked).toHaveLength(5);
  });
});

describe('rankingPools', () => {
  it('splits the ranked list into the rostered tier and the free-agent band, skipping my roster', () => {
    // arrange
    const ranked: Array<{ id: number; fantasy_rank: number | null }> = Array.from(
      { length: 400 },
      (_, i) => ({ id: i + 1, fantasy_rank: i + 1 })
    );
    ranked.push({ id: 999, fantasy_rank: null });

    // act
    const pools = rankingPools(ranked, new Set([5, 140]));

    // assert
    expect(pools.teams).toBe(10);
    expect(pools.rostered_cutoff).toBe(130);
    expect(pools.trade_pool).toHaveLength(129);
    expect(pools.waiver_pool[0].id).toBe(131);
    expect(pools.waiver_pool).toHaveLength(249);
    expect(pools.waiver_pool.some((p) => p.id === 140 || p.id === 999)).toBe(false);
  });
});

describe('streamerSentence', () => {
  it('names the pickup, his games in the window and the expected category wins', () => {
    // act + assert
    expect(streamerSentence('Zach Edey', 4, 1.82, 7)).toBe(
      'Pick up Zach Edey: 4 games this week, about +1.8 expected category wins.'
    );
  });

  it('describes a non-week window and a single game', () => {
    // act + assert
    expect(streamerSentence('Zach Edey', 1, 0.04, 3)).toBe(
      'Pick up Zach Edey: 1 game in the next 3 days, about +0.04 expected category wins.'
    );
  });

  it('carries games, score, labeled drivers and the sentence on the response row', () => {
    // arrange
    const candidate: RankedCandidate = {
      id: 7, nba_id: '1007', name: 'Zach Edey', team: 'MEM', position: 'C', score: 0.42,
      drivers: [{ category: 'blk', value: 0.2 }, { category: 'reb', value: 0.15 }],
      projected_games: 4, mean_prob_play: 0.9, basis: 'projection',
    };

    // act
    const streamer = toStreamer(candidate, 7);

    // assert
    expect(streamer).toMatchObject({
      id: 7,
      games: 4,
      score: 0.42,
      drivers: [{ category: 'blk', label: 'BLK', value: 0.2 }, { category: 'reb', label: 'REB', value: 0.15 }],
      sentence: 'Pick up Zach Edey: 4 games this week, about +0.4 expected category wins.',
    });
  });
});

describe('formatSignedWins', () => {
  it.each([
    [0.6, '+0.6'],
    [-0.55, '-0.6'],
    [0.04, '+0.04'],
    [-0.001, '+0.00'],
    [1.84, '+1.8'],
  ])('formats %s as %s', (value, expected) => {
    // act + assert
    expect(formatSignedWins(value)).toBe(expected);
  });
});
