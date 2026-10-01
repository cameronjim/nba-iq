import { describe, it, expect, vi, beforeEach } from 'vitest';
import { pgResult } from '../helpers/mockDb.js';

const { buildTeamContext, buildWaiverContext } = await import('../../src/services/ai.js');
const { query } = await import('../../src/db.js');
const { PRODUCTION_CHANNEL } = await import('../../src/services/slate.js');
const queryMock = vi.mocked(query);


const rosterRow = {
  id: 7,
  nba_id: '2544',
  name: 'LeBron James',
  team: 'LAL',
  position: 'SF',
  points_per_game: 25.4,
  rebounds_per_game: 7.2,
  assists_per_game: 8.1,
  steals_per_game: 1.1,
  blocks_per_game: 0.6,
  field_goal_percentage: 51.2,
  free_throw_percentage: 75.4,
  three_pointers_made: 2.1,
  turnovers_per_game: 3.4,
  injury_status: null,
};

function analyticsRow(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    nba_player_id: '2544',
    games: 41,
    pts_l10: 27.5,
    pts_season: 25.4,
    reb_l10: 6.9,
    reb_season: 7.2,
    ast_l10: 8.6,
    ast_season: 8.1,
    min_l10: 36.1,
    min_season: 34.2,
    prob_active: 0.93,
    ...overrides,
  };
}

beforeEach(() => {
  queryMock.mockReset();
});

describe('buildTeamContext analytics enrichment', () => {
  it('appends last-10 deltas and availability under the roster', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([rosterRow]))
      .mockResolvedValueOnce(pgResult([analyticsRow()]));

    const context = await buildTeamContext(1);

    expect(context).toContain('ROSTER AVERAGES:');
    expect(context).toContain('MY ROSTER (1):');
    expect(context).toContain('RECENT FORM (last 10 games, change vs season average)');
    expect(context).toContain('LeBron James (41g): PTS 27.5 (+2.1) REB 6.9 (-0.3) AST 8.6 (+0.5) MIN 36.1 (+1.9)');
    expect(context).toContain('P(active next game) 93%');
  });

  it('drops the availability clause when no run has projected the player', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([rosterRow]))
      .mockResolvedValueOnce(pgResult([analyticsRow({ prob_active: null })]));

    const context = await buildTeamContext(1);

    expect(context).toContain('RECENT FORM (last 10 games, change vs season average):');
    expect(context).not.toContain('P(active next game)');
  });

  it('leaves the prompt exactly as it was when the analytics tables are missing', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([rosterRow]))
      .mockRejectedValueOnce(new Error('relation "player_game_logs" does not exist'));

    const context = await buildTeamContext(1);

    expect(context).toContain('MY ROSTER (1):');
    expect(context).toContain('LeBron James (SF/LAL)');
    expect(context).not.toContain('RECENT FORM');
  });

  it('skips the enrichment query entirely for a roster with no nba ids', async () => {
    queryMock.mockResolvedValueOnce(pgResult([{ ...rosterRow, nba_id: null }]));

    const context = await buildTeamContext(1);

    expect(queryMock).toHaveBeenCalledTimes(1);
    expect(context).not.toContain('RECENT FORM');
  });

  it('still short-circuits on an empty roster', async () => {
    queryMock.mockResolvedValueOnce(pgResult([]));

    expect(await buildTeamContext(1)).toBe('No players on roster.');
    expect(queryMock).toHaveBeenCalledTimes(1);
  });

  it('passes the roster ids to the enrichment query as a bound array', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([rosterRow]))
      .mockResolvedValueOnce(pgResult([analyticsRow()]));

    await buildTeamContext(1);

    const [sql, params] = queryMock.mock.calls[1];
    expect(params).toEqual([['2544'], 'complete', 'production']);
    expect(sql).toContain('ANY($1)');
  });
});

describe('buildWaiverContext analytics enrichment', () => {
  it('adds the same block above the waiver and trade candidates', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([rosterRow]))
      .mockResolvedValueOnce(pgResult([analyticsRow()]));

    const context = await buildWaiverContext(1, 12);

    expect(context).toContain('LEAGUE: 12 teams');
    expect(context.indexOf('RECENT FORM')).toBeGreaterThan(context.indexOf('MY ROSTER'));
    expect(context.indexOf('RECENT FORM')).toBeLessThan(context.indexOf('WAIVER CANDIDATES'));
  });

  it('leaves the waiver prompt intact when the enrichment fails', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([rosterRow]))
      .mockRejectedValueOnce(new Error('relation "prediction_runs" does not exist'));

    const context = await buildWaiverContext(1);

    expect(context).toContain('WAIVER CANDIDATES');
    expect(context).toContain('TRADE TARGETS');
    expect(context).not.toContain('RECENT FORM');
  });
});

describe('buildTeamContext prediction run selection', () => {
  it('reads availability only from a complete production run', async () => {
    // arrange
    queryMock
      .mockResolvedValueOnce(pgResult([rosterRow]))
      .mockResolvedValueOnce(pgResult([analyticsRow()]));

    // act
    await buildTeamContext(1);

    // assert
    const [sql, params] = queryMock.mock.calls[1];
    expect(params).toContain(PRODUCTION_CHANNEL);
    expect(sql).toMatch(/channel = \$3/);
  });
});

const { getRankedPlayers } = await import('../../src/services/fantasyScore.js');
const getRankedPlayersMock = vi.mocked(getRankedPlayers);
type PlayerWithScore = Awaited<ReturnType<typeof getRankedPlayers>>[number];

interface StoreFixture {
  run?: boolean;
  predictions?: Array<Record<string, unknown>>;
}

function projectionRow(gameId: string, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    game_date: '2026-10-02',
    nba_game_id: gameId,
    nba_player_id: '2544',
    name: 'LeBron James',
    team_abbr: 'LAL',
    position: 'SF',
    prob_active: 0.9,
    proj_min_p50: 36,
    u_pts: 24, u_reb: 7, u_ast: 8, u_stl: 1, u_blk: 0.5, u_tov: 3, u_fg3m: 2,
    u_fgm: 9, u_fga: 18, u_ftm: 4, u_fta: 5,
    ...overrides,
  };
}

// routes each query by what it reads, so the test does not depend on call order
function routeStore(fixture: StoreFixture): void {
  queryMock.mockImplementation(async (sql: string) => {
    if (sql.includes('FROM my_roster')) return pgResult([rosterRow]);
    if (sql.includes('WITH logs AS')) return pgResult([analyticsRow()]);
    if (sql.includes('FROM nba_schedule')) {
      return pgResult([
        { nba_game_id: 'g1', home_team_abbr: 'LAL', away_team_abbr: 'BOS' },
        { nba_game_id: 'g2', home_team_abbr: 'NYK', away_team_abbr: 'LAL' },
      ]);
    }
    if (sql.includes('FROM prediction_runs')) {
      return pgResult(
        fixture.run === false ? [] : [{ id: 41, model_version: 'v2.3', predicted_at: '2026-10-01T12:00:00Z' }]
      );
    }
    if (sql.includes('FROM player_game_predictions pgp')) return pgResult(fixture.predictions ?? []);
    if (sql.includes('WITH played AS')) {
      return pgResult([
        { nba_player_id: '2544', games: 15, minutes: 33.5, pts: 25, pts_recent: 26, pts_sd: 5, last_played_date: '2026-04-10' },
      ]);
    }
    return pgResult([]);
  });
}

function rankedPlayer(id: number, overrides: Partial<PlayerWithScore> = {}): PlayerWithScore {
  return {
    id,
    nba_id: String(5000 + id),
    name: `Candidate ${id}`,
    team: 'BOS',
    position: 'PG',
    points_per_game: 10,
    rebounds_per_game: 4,
    assists_per_game: 3,
    steals_per_game: 0.8,
    blocks_per_game: 0.4,
    field_goal_percentage: 45,
    free_throw_percentage: 78,
    three_point_percentage: 35,
    three_pointers_made: 1.2,
    turnovers_per_game: 1.4,
    minutes_per_game: 24,
    games_played: 40,
    injury_status: null,
    injury_detail: null,
    headshot_url: null,
    fantasy_score: 20,
    fantasy_rank: id,
    ...overrides,
  };
}

describe('buildTeamContext projected window', () => {
  it('adds one projection line per rostered player when the production store has rows', async () => {
    // arrange
    routeStore({ predictions: [projectionRow('g1'), projectionRow('g2')] });

    // act
    const context = await buildTeamContext(1);

    // assert
    expect(context).toContain('PROJECTED NEXT 7 DAYS (');
    expect(context).toContain('production model v2.3; unconditional totals, so sitting risk is priced in');
    expect(context).toContain(
      'LeBron James: 2 of 2 scheduled games, P(play) 90%, PTS 48.0 REB 14.0 AST 16.0 STL 2.0 BLK 1.0 3PM 4.0 ' +
      'FG% 50.0 (36.0 FGA) FT% 80.0 (10.0 FTA) TO 6.0, MIN vs usual +2.5'
    );
  });

  it('states why projections are absent when no production run exists', async () => {
    // arrange
    routeStore({ run: false });

    // act
    const context = await buildTeamContext(1);

    // assert
    expect(context).toContain('PROJECTED NEXT 7 DAYS: not available, no complete production model run exists yet.');
    expect(context).not.toContain('scheduled games, P(play)');
  });

  it('states why projections are absent when the run has nothing for this roster', async () => {
    // arrange
    routeStore({ predictions: [] });

    // act
    const context = await buildTeamContext(1);

    // assert
    expect(context).toMatch(
      /PROJECTED NEXT 7 DAYS: not available, the latest production run \(v2\.3\) has no projections for this roster/
    );
  });

  it('reads the run only from the production channel', async () => {
    // arrange
    routeStore({ predictions: [projectionRow('g1')] });

    // act
    await buildTeamContext(1);

    // assert
    const runReads = queryMock.mock.calls.filter(([sql]) => String(sql).includes('FROM prediction_runs'));
    expect(runReads.length).toBeGreaterThan(0);
    for (const [, params] of runReads) expect(params).toContain(PRODUCTION_CHANNEL);
  });
});

describe('buildWaiverContext ranked candidates', () => {
  const waiverBand = [
    rankedPlayer(140, { name: 'Plain Guard' }),
    rankedPlayer(141, { name: 'Shot Blocker', blocks_per_game: 3, points_per_game: 8 }),
    rankedPlayer(142, { name: 'Bench Wing', points_per_game: 6 }),
  ];
  const tradeTier = [
    rankedPlayer(5, { name: 'Star Big', points_per_game: 24, rebounds_per_game: 11, blocks_per_game: 2 }),
    rankedPlayer(6, { name: 'Role Wing', points_per_game: 14 }),
  ];

  it('lists candidates pre-ranked by score with drivers and basis, and tells Claude not to re-rank', async () => {
    // arrange
    routeStore({ predictions: [] });
    getRankedPlayersMock.mockResolvedValue([...tradeTier, ...waiverBand]);

    // act
    const context = await buildWaiverContext(1, 10);

    // assert
    expect(context).toContain('RANKING METHOD: both candidate lists below are pre-ranked numerically, best first.');
    expect(context).toContain('not to re-rank from scratch');
    const waiverSection = context.slice(context.indexOf('WAIVER CANDIDATES'), context.indexOf('TRADE TARGETS'));
    const lines = waiverSection.split('\n').filter((l) => /^\d+\. \[score/.test(l));
    expect(lines).toHaveLength(3);
    expect(lines[0]).toMatch(/^1\. \[score -?\d+\.\d{3} \| season_average, 1 scheduled g\] Shot Blocker \(PG\/BOS\)/);
    expect(lines[0]).toMatch(/\| drivers: BLK \+\d\.\d{3}, /);
    const scores = lines.map((l) => Number(/score (-?\d+\.\d+)/.exec(l)?.[1]));
    expect(scores).toEqual([...scores].sort((a, b) => b - a));
  });

  it('produces the identical prompt on repeated calls', async () => {
    // arrange
    routeStore({ predictions: [] });
    getRankedPlayersMock.mockResolvedValue([...tradeTier, ...waiverBand]);

    // act
    const first = await buildWaiverContext(1, 10);
    const second = await buildWaiverContext(1, 10);

    // assert
    expect(second).toBe(first);
  });

  it('marks a candidate the production run projects with the projection basis', async () => {
    // arrange
    routeStore({
      predictions: [
        projectionRow('g1', { nba_player_id: '5141', name: 'Shot Blocker', team_abbr: 'BOS', prob_active: 0.8 }),
      ],
    });
    getRankedPlayersMock.mockResolvedValue([...tradeTier, ...waiverBand]);

    // act
    const context = await buildWaiverContext(1, 10);

    // assert
    expect(context).toMatch(/\[score -?\d+\.\d{3} \| projection, 1g, P\(play\) 80%\] Shot Blocker/);
  });
});
