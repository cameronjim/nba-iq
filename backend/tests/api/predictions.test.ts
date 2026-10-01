import { describe, it, expect, vi, beforeEach } from 'vitest';
import request from 'supertest';
import { pgResult } from '../helpers/mockDb.js';

const { app } = await import('../../src/app.js');
const { query } = await import('../../src/db.js');
const { IMPACT_PERCENTILE_FLOOR, MAX_WINDOW_DAYS, POSITION_FILTERS } = await import(
  '../../src/services/watchlist.js'
);
const { baselineDescriptor, BASELINE_STATS } = await import('../../src/services/baselines.js');
const {
  IMPACT_POOL_KEY,
  IMPACT_POOL_LABEL,
  IMPACT_POOL_DEFINITION,
  POINTS_UNCOND_STAT,
  PRODUCTION_CHANNEL,
} = await import('../../src/services/slate.js');
const queryMock = vi.mocked(query);

function poolOf(size: number): Record<string, unknown> {
  return {
    key: IMPACT_POOL_KEY,
    label: IMPACT_POOL_LABEL,
    definition: IMPACT_POOL_DEFINITION,
    sample_size: size,
  };
}

const baseline = baselineDescriptor();

const FLAT_BASELINE: Record<string, number> = {
  minutes: 31,
  pts: 8,
  reb: 3,
  ast: 2,
  stl: 0.5,
  blk: 0.3,
  fg3m: 1,
  fga: 7,
};

function baselineRow(
  nbaPlayerId: string,
  overrides: Record<string, unknown> = {}
): Record<string, unknown> {
  const row: Record<string, unknown> = {
    nba_player_id: nbaPlayerId,
    games: 15,
    pts_recent: FLAT_BASELINE.pts,
    pts_sd: 4,
    last_played_date: '2026-02-02',
  };
  for (const stat of BASELINE_STATS) row[stat] = FLAT_BASELINE[stat] ?? 0;
  return { ...row, ...overrides };
}


function undefinedTable(relation: string): Error & { code: string } {
  const err = new Error(`relation "${relation}" does not exist`) as Error & { code: string };
  err.code = '42P01';
  return err;
}

const scheduleRows = [
  {
    nba_game_id: '0022500555',
    game_status: 'Scheduled',
    home_team_id: '1610612747',
    away_team_id: '1610612744',
  },
];

const teamRows = [
  { team_id: '1610612747', team_abbr: 'LAL' },
  { team_id: '1610612744', team_abbr: 'GSW' },
];

const runRow = {
  id: 42,
  model_version: 'v1-decomposed',
  predicted_at: new Date('2026-02-04T11:00:00.000Z'),
};

function predictionRow(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    nba_game_id: '0022500555',
    nba_player_id: '2544',
    name: 'LeBron James',
    team_abbr: 'LAL',
    prob_active: 0.93,
    proj_min_p50: 34.2,
    pts: 24.6,
    reb: 7.4,
    ast: 8.1,
    stl: 1.1,
    blk: 0.6,
    tov: 3.2,
    fg3m: 2.0,
    fgm: 9.2,
    fga: 18.0,
    ftm: 4.1,
    fta: 5.6,
    ...overrides,
  };
}

beforeEach(() => {
  queryMock.mockReset();
});

describe('GET /api/predictions/slate', () => {
  it('returns each scheduled game with its top projected players', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult(scheduleRows))
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(teamRows))
      .mockResolvedValueOnce(
        pgResult([
          predictionRow(),
          predictionRow({
            nba_player_id: '201939',
            name: 'Stephen Curry',
            team_abbr: 'GSW',
            prob_active: 0.99,
            pts: 28.4,
            proj_min_p50: 33.1,
          }),
        ])
      )
      .mockResolvedValueOnce(pgResult([baselineRow('201939', { minutes: 30, pts: 30 }), baselineRow('2544', { minutes: 30, pts: 30 })]))
      .mockResolvedValueOnce(pgResult([]));

    const res = await request(app).get('/api/predictions/slate').query({ date: '2026-02-04' });

    expect(res.status).toBe(200);
    expect(res.body.date).toBe('2026-02-04');
    expect(res.body.run).toEqual({
      model_version: 'v1-decomposed',
      predicted_at: '2026-02-04T11:00:00.000Z',
    });
    expect(res.body.pool).toEqual(poolOf(2));
    expect(res.body.games).toHaveLength(1);
    expect(res.body.games[0]).toMatchObject({
      nba_game_id: '0022500555',
      game_status: 'Scheduled',
      home_team_id: '1610612747',
      home_team_abbr: 'LAL',
      away_team_id: '1610612744',
      away_team_abbr: 'GSW',
    });
    expect(res.body.games[0].players[0]).toEqual({
      nba_player_id: '201939',
      name: 'Stephen Curry',
      name_is_placeholder: false,
      team_abbr: 'GSW',
      prob_active: 0.99,
      proj_pts: 28.4,
      proj_min_p50: 33.1,
      projected: { reb: 7.4, ast: 8.1, stl: 1.1, blk: 0.6, tov: 3.2, fg3m: 2 },
      usual_min: 30,
      usual_pts: 30,
      min_vs_usual: 3.1,
      pts_vs_usual: -1.6,
      baseline_games: 15,
      impact: 1,
      spotlight: true,
      slate_spotlight: true,
      injury_status: null,
      injury_status_raw: null,
      injury_detail: null,
      injury_as_of: null,
      injury_changed_after_run: false,
    });
    expect(res.body.games[0].top_impact).toBe(1);
    expect(res.body.baseline).toEqual(baseline);
  });

  it('overlays the current injury designation and flags a post-run change', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult(scheduleRows))
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(teamRows))
      .mockResolvedValueOnce(
        pgResult([
          predictionRow(),
          predictionRow({ nba_player_id: '201939', name: 'Stephen Curry', team_abbr: 'GSW' }),
        ])
      )
      .mockResolvedValueOnce(pgResult([]))
      .mockResolvedValueOnce(
        pgResult([
          {
            nba_id: '2544',
            status_raw: 'Out',
            detail: 'Ankle',
            current_normalized: 'out',
            current_captured_at: new Date('2026-02-04T20:00:00.000Z'),
            run_normalized: null,
          },
          {
            nba_id: '201939',
            status_raw: 'Game Time Decision',
            detail: 'Knee',
            current_normalized: 'questionable',
            current_captured_at: new Date('2026-02-04T20:00:00.000Z'),
            run_normalized: 'questionable',
          },
        ])
      );

    const res = await request(app).get('/api/predictions/slate').query({ date: '2026-02-04' });

    expect(res.status).toBe(200);
    const byId = new Map(
      (res.body.games[0].players as Array<Record<string, unknown>>).map((p) => [
        p.nba_player_id,
        p,
      ])
    );
    expect(byId.get('2544')).toMatchObject({
      injury_status: 'out',
      injury_status_raw: 'Out',
      injury_detail: 'Ankle',
      injury_as_of: '2026-02-04T20:00:00.000Z',
      injury_changed_after_run: true,
    });
    expect(byId.get('201939')).toMatchObject({
      injury_status: 'questionable',
      injury_status_raw: 'Game Time Decision',
      injury_changed_after_run: false,
    });
  });

  it('reads the unconditional stat names, not `conditional = false` on the bare ones', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult(scheduleRows))
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(teamRows))
      .mockResolvedValueOnce(pgResult([predictionRow()]))
      .mockResolvedValueOnce(pgResult([]))
      .mockResolvedValueOnce(pgResult([]));

    await request(app).get('/api/predictions/slate').query({ date: '2026-02-04' });

    const [sql, params] = queryMock.mock.calls[3];
    expect(params).toContain(POINTS_UNCOND_STAT);
    expect(params).not.toContain('pts');
    expect(sql).not.toMatch(/conditional\s*=\s*false/);
  });

  it('caps each game at eight players', async () => {
    const many = Array.from({ length: 15 }, (_, i) =>
      predictionRow({ nba_player_id: String(i), name: `Player ${i}`, pts: i })
    );
    queryMock
      .mockResolvedValueOnce(pgResult(scheduleRows))
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(teamRows))
      .mockResolvedValueOnce(pgResult(many))
      .mockResolvedValueOnce(pgResult([]))
      .mockResolvedValueOnce(pgResult([]));

    const res = await request(app).get('/api/predictions/slate').query({ date: '2026-02-04' });

    expect(res.body.games[0].players).toHaveLength(8);
    expect(res.body.games[0].players[0].name).toBe('Player 14');
  });

  it('labels a player with no roster row instead of rendering a blank name', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult(scheduleRows))
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(teamRows))
      .mockResolvedValueOnce(
        pgResult([
          predictionRow({ nba_player_id: '1642850', name: null, team_abbr: null, pts: 4.1 }),
          predictionRow({ nba_player_id: '201939', name: 'Stephen Curry', pts: 28.4 }),
        ])
      )
      .mockResolvedValueOnce(pgResult([]))
      .mockResolvedValueOnce(pgResult([]));

    const res = await request(app).get('/api/predictions/slate').query({ date: '2026-02-04' });

    const [first, second] = res.body.games[0].players;
    expect(first.name).toBe('Stephen Curry');
    expect(second.name).toBe('NBA #1642850 (new roster)');
    expect(second.name_is_placeholder).toBe(true);
    expect(first.name_is_placeholder).toBe(false);
  });

  it('spotlights the top impact players per game and across the slate', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult(scheduleRows))
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(teamRows))
      .mockResolvedValueOnce(
        pgResult(
          [30, 24, 18, 12, 6].map((pts, i) =>
            predictionRow({ nba_player_id: `p${i}`, name: `Player ${i}`, pts })
          )
        )
      )
      .mockResolvedValueOnce(pgResult([]))
      .mockResolvedValueOnce(pgResult([]));

    const res = await request(app).get('/api/predictions/slate').query({ date: '2026-02-04' });

    const players = res.body.games[0].players;
    expect(players.map((p: { spotlight: boolean }) => p.spotlight)).toEqual([
      true,
      true,
      true,
      false,
      false,
    ]);
    expect(players.every((p: { slate_spotlight: boolean }) => p.slate_spotlight)).toBe(true);
    expect(players[2].impact).toBe(0);
  });

  it('orders the game cards by the biggest projected impact on them', async () => {
    queryMock
      .mockResolvedValueOnce(
        pgResult([
          { ...scheduleRows[0], nba_game_id: '0022500111' },
          { ...scheduleRows[0], nba_game_id: '0022500999' },
        ])
      )
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(teamRows))
      .mockResolvedValueOnce(
        pgResult([
          predictionRow({ nba_game_id: '0022500111', nba_player_id: 'a', pts: 9.5 }),
          predictionRow({ nba_game_id: '0022500999', nba_player_id: 'b', pts: 31.5 }),
        ])
      )
      .mockResolvedValueOnce(pgResult([]))
      .mockResolvedValueOnce(pgResult([]));

    const res = await request(app).get('/api/predictions/slate').query({ date: '2026-02-04' });

    expect(res.body.games.map((g: { nba_game_id: string }) => g.nba_game_id)).toEqual([
      '0022500999',
      '0022500111',
    ]);
    expect(res.body.games[0].top_impact).toBeGreaterThan(res.body.games[1].top_impact);
  });

  it('still lists the games when no run has finished yet', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult(scheduleRows))
      .mockResolvedValueOnce(pgResult([]))
      .mockResolvedValueOnce(pgResult(teamRows));

    const res = await request(app).get('/api/predictions/slate').query({ date: '2026-02-04' });

    expect(res.status).toBe(200);
    expect(res.body.run).toBeNull();
    expect(res.body.games).toHaveLength(1);
    expect(res.body.games[0].players).toEqual([]);
    expect(queryMock).toHaveBeenCalledTimes(3);
  });

  it('returns an empty slate for a day with no games', async () => {
    queryMock.mockResolvedValueOnce(pgResult([])).mockResolvedValueOnce(pgResult([runRow]));

    const res = await request(app).get('/api/predictions/slate').query({ date: '2026-07-04' });

    expect(res.status).toBe(200);
    expect(res.body).toEqual({
      date: '2026-07-04',
      run: { model_version: 'v1-decomposed', predicted_at: '2026-02-04T11:00:00.000Z' },
      pool: poolOf(0),
      baseline,
      games: [],
    });
  });

  it('degrades to an empty slate when the prediction tables do not exist yet', async () => {
    queryMock.mockRejectedValue(undefinedTable('nba_schedule'));

    const res = await request(app).get('/api/predictions/slate').query({ date: '2026-02-04' });

    expect(res.status).toBe(200);
    expect(res.body).toEqual({
      date: '2026-02-04',
      run: null,
      pool: poolOf(0),
      baseline,
      games: [],
    });
  });

  it('keeps the games when only the prediction tables are missing', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult(scheduleRows))
      .mockRejectedValueOnce(undefinedTable('prediction_runs'))
      .mockResolvedValueOnce(pgResult(teamRows));

    const res = await request(app).get('/api/predictions/slate').query({ date: '2026-02-04' });

    expect(res.status).toBe(200);
    expect(res.body.run).toBeNull();
    expect(res.body.games).toHaveLength(1);
  });

  it('defaults to today when no date is given', async () => {
    queryMock.mockResolvedValueOnce(pgResult([])).mockResolvedValueOnce(pgResult([]));

    const res = await request(app).get('/api/predictions/slate');

    expect(res.status).toBe(200);
    expect(res.body.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(queryMock.mock.calls[0][1]).toEqual([res.body.date]);
  });

  it('returns 400 for a malformed date without touching the database', async () => {
    const res = await request(app).get('/api/predictions/slate').query({ date: '04-02-2026' });

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/YYYY-MM-DD/);
    expect(queryMock).not.toHaveBeenCalled();
  });

  it('returns 500 when the database is actually down', async () => {
    queryMock.mockRejectedValue(new Error('db down'));

    const res = await request(app).get('/api/predictions/slate').query({ date: '2026-02-04' });

    expect(res.status).toBe(500);
    expect(res.body.error).toBe('Failed to fetch slate');
  });

  it('binds the date as a query parameter rather than interpolating it', async () => {
    queryMock.mockResolvedValueOnce(pgResult([])).mockResolvedValueOnce(pgResult([]));

    await request(app).get('/api/predictions/slate').query({ date: '2026-02-04' });

    const [sql, params] = queryMock.mock.calls[0];
    expect(params).toEqual(['2026-02-04']);
    expect(sql).toContain('$1');
  });
});

function watchRow(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const row: Record<string, unknown> = {
    game_date: '2026-02-04',
    nba_game_id: '0022500555',
    nba_player_id: '1630559',
    name: 'Breakout Wing',
    team_abbr: 'OKC',
    position: 'SG,SF',
    prob_active: 0.88,
    proj_min_p50: 31,
  };
  const uncond: Record<string, number> = {
    pts: 8,
    reb: 3,
    ast: 2,
    stl: 0.5,
    blk: 0.3,
    tov: 1,
    fg3m: 1,
    fgm: 3,
    fga: 7,
    ftm: 1,
    fta: 1.4,
  };
  for (const [stat, value] of Object.entries(uncond)) row[`u_${stat}`] = value;
  for (const stat of ['pts', 'reb', 'ast', 'stl', 'blk', 'fg3m', 'fga']) {
    row[`c_${stat}`] = uncond[stat];
  }
  return { ...row, ...overrides };
}

function benchPool(count: number): Record<string, unknown>[] {
  return Array.from({ length: count }, (_, i) =>
    watchRow({
      nba_player_id: `f${i}`,
      name: `Bench ${i}`,
      team_abbr: 'OKC',
      proj_min_p50: 10,
      u_pts: i * 0.1,
      c_pts: i * 0.1,
    })
  );
}

function benchBaselines(count: number): Record<string, unknown>[] {
  return Array.from({ length: count }, (_, i) => baselineRow(`f${i}`, { minutes: 10, pts: i * 0.1 }));
}

const gameTeamRows = [{ nba_game_id: '0022500555', home_team_abbr: 'OKC', away_team_abbr: 'LAL' }];

function watchRowOn(
  date: string,
  overrides: Record<string, unknown> = {}
): Record<string, unknown> {
  return watchRow({ game_date: date, nba_game_id: `00225005${date.slice(-2)}`, ...overrides });
}

function benchPoolOn(date: string, count: number): Record<string, unknown>[] {
  return benchPool(count).map((row) => ({
    ...row,
    game_date: date,
    nba_game_id: `00225005${date.slice(-2)}`,
  }));
}

describe('GET /api/watchlist', () => {
  it('ranks a role increase with its deltas, both score factors and its reasons', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult([watchRow({ u_pts: 20, c_pts: 20 }), ...benchPool(60)]))
      .mockResolvedValueOnce(
        pgResult([baselineRow('1630559', { minutes: 22, pts: 11 }), ...benchBaselines(60)])
      )
      .mockResolvedValueOnce(pgResult(gameTeamRows));

    const res = await request(app).get('/api/watchlist').query({ date: '2026-02-04' });

    expect(res.status).toBe(200);
    expect(res.body.date).toBe('2026-02-04');
    expect(res.body.run).toEqual({
      model_version: 'v1-decomposed',
      predicted_at: '2026-02-04T11:00:00.000Z',
    });
    expect(res.body.pool).toEqual(poolOf(61));
    expect(res.body.baseline).toEqual(baseline);

    const candidate = res.body.players[0];
    expect(candidate.nba_player_id).toBe('1630559');
    expect(candidate.name).toBe('Breakout Wing');
    expect(candidate.team_abbr).toBe('OKC');
    expect(candidate.opponent_team_abbr).toBe('LAL');
    expect(candidate.game_date).toBe('2026-02-04');
    expect(candidate.prob_active).toBe(0.88);
    expect(candidate.minutes).toEqual({ usual: 22, projected: 31, delta: 9 });
    expect(candidate.points).toEqual({ usual: 11, projected: 20, delta: 9 });
    expect(candidate.baseline_games).toBe(15);
    expect(candidate.reasons).toContain('ROLE_INCREASE');
    expect(candidate.score).toBeCloseTo(candidate.upside * candidate.relevance, 3);
    expect(candidate.relevance).toBeGreaterThan(0);
    expect(candidate.impact_percentile).toBeGreaterThan(IMPACT_PERCENTILE_FLOOR);
    expect(candidate.drivers.length).toBeGreaterThan(0);
  });

  it('keeps a bench jump off the list however large the jump is', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(
        pgResult([
          watchRow({
            nba_player_id: 'scrub',
            name: 'Deep Bench',
            proj_min_p50: 15,
            u_pts: 4,
            c_pts: 4,
          }),
          ...benchPool(60),
        ])
      )
      .mockResolvedValueOnce(pgResult([baselineRow('scrub', { minutes: 5, pts: 2 }), ...benchBaselines(60)]))
      .mockResolvedValueOnce(pgResult(gameTeamRows));

    const res = await request(app).get('/api/watchlist').query({ date: '2026-02-04' });

    expect(res.status).toBe(200);
    expect(res.body.players.map((p: { nba_player_id: string }) => p.nba_player_id)).not.toContain(
      'scrub'
    );
  });

  it('flags a teammate the run does not expect to play, with the minutes it frees', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(
        pgResult([
          watchRow({ u_pts: 20, c_pts: 20 }),
          watchRow({
            nba_player_id: '203507',
            name: 'Franchise Player',
            prob_active: 0.05,
            proj_min_p50: 2,
          }),
          ...benchPool(60),
        ])
      )
      .mockResolvedValueOnce(
        pgResult([
          baselineRow('1630559', { minutes: 22, pts: 11 }),
          baselineRow('203507', { minutes: 34.6 }),
          ...benchBaselines(60),
        ])
      )
      .mockResolvedValueOnce(pgResult(gameTeamRows));

    const res = await request(app).get('/api/watchlist').query({ date: '2026-02-04' });

    const candidate = res.body.players.find(
      (p: { nba_player_id: string }) => p.nba_player_id === '1630559'
    );
    expect(candidate.reasons).toContain('TEAMMATE_ABSENCE');
    expect(candidate.evidence.teammate_out).toBe('Franchise Player');
    expect(candidate.evidence.teammate_out_minutes).toBe(34.6);
    expect(candidate.evidence.teammate_out_prob_active).toBe(0.05);
  });

  it('does not count a player as his own absent teammate', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(
        pgResult([watchRow({ prob_active: 0.05, u_pts: 20, c_pts: 20 }), ...benchPool(60)])
      )
      .mockResolvedValueOnce(
        pgResult([baselineRow('1630559', { minutes: 22, pts: 11 }), ...benchBaselines(60)])
      )
      .mockResolvedValueOnce(pgResult(gameTeamRows));

    const res = await request(app).get('/api/watchlist').query({ date: '2026-02-04' });

    expect(res.body.players[0].reasons).not.toContain('TEAMMATE_ABSENCE');
  });

  it('drops a player with too little history to have a usual', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult([watchRow({ u_pts: 20, c_pts: 20 }), ...benchPool(60)]))
      .mockResolvedValueOnce(
        pgResult([baselineRow('1630559', { minutes: 22, pts: 11, games: 4 }), ...benchBaselines(60)])
      )
      .mockResolvedValueOnce(pgResult(gameTeamRows));

    const res = await request(app).get('/api/watchlist').query({ date: '2026-02-04' });

    expect(res.body.players.map((p: { nba_player_id: string }) => p.nba_player_id)).not.toContain(
      '1630559'
    );
  });

  it('answers with an empty list when no run has completed', async () => {
    queryMock.mockResolvedValueOnce(pgResult([]));

    const res = await request(app).get('/api/watchlist').query({ date: '2026-02-04' });

    expect(res.status).toBe(200);
    expect(res.body).toEqual({
      date: '2026-02-04',
      window: { from: '2026-02-04', to: '2026-02-04', days: 1 },
      run: null,
      pool: poolOf(0),
      baseline,
      position: null,
      position_options: [...POSITION_FILTERS],
      position_coverage: { known: 0, unknown: 0 },
      players: [],
    });
    expect(queryMock).toHaveBeenCalledTimes(1);
  });

  it('answers with an empty list when the run projected nothing for the date', async () => {
    queryMock.mockResolvedValueOnce(pgResult([runRow])).mockResolvedValueOnce(pgResult([]));

    const res = await request(app).get('/api/watchlist').query({ date: '2026-02-04' });

    expect(res.status).toBe(200);
    expect(res.body.players).toEqual([]);
    expect(res.body.run).not.toBeNull();
    expect(queryMock).toHaveBeenCalledTimes(2);
  });

  it('degrades to an empty list when the prediction tables do not exist yet', async () => {
    queryMock.mockRejectedValue(undefinedTable('prediction_runs'));

    const res = await request(app).get('/api/watchlist').query({ date: '2026-02-04' });

    expect(res.status).toBe(200);
    expect(res.body.players).toEqual([]);
    expect(res.body.run).toBeNull();
  });

  it('degrades to an empty list when the game logs do not exist yet', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult([watchRow()]))
      .mockRejectedValueOnce(undefinedTable('player_game_logs'))
      .mockResolvedValueOnce(pgResult([]));

    const res = await request(app).get('/api/watchlist').query({ date: '2026-02-04' });

    expect(res.status).toBe(200);
    expect(res.body.players).toEqual([]);
  });

  it('returns 400 for a malformed date without touching the database', async () => {
    const res = await request(app).get('/api/watchlist').query({ date: 'yesterday' });

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/YYYY-MM-DD/);
    expect(queryMock).not.toHaveBeenCalled();
  });

  it('returns 500 when the database is actually down', async () => {
    queryMock.mockRejectedValue(new Error('db down'));

    const res = await request(app).get('/api/watchlist').query({ date: '2026-02-04' });

    expect(res.status).toBe(500);
    expect(res.body.error).toBe('Failed to fetch watchlist');
  });

  it('reads baselines only from games strictly before the requested date', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult([watchRow()]))
      .mockResolvedValueOnce(pgResult([]))
      .mockResolvedValueOnce(pgResult([]));

    await request(app).get('/api/watchlist').query({ date: '2026-02-04' });

    const [sql, params] = queryMock.mock.calls[2];
    expect(sql).toContain('g.game_date < $1');
    expect(params?.[0]).toBe('2026-02-04');
  });

  it('binds the run and the window as parameters rather than interpolating them', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult([watchRow()]))
      .mockResolvedValueOnce(pgResult([]))
      .mockResolvedValueOnce(pgResult([]));

    await request(app).get('/api/watchlist').query({ date: '2026-02-04' });

    const [sql, params] = queryMock.mock.calls[1];
    expect(params?.slice(0, 3)).toEqual([42, '2026-02-04', '2026-02-04']);
    expect(sql).toContain('$1');
  });

  it('carries the position and the games count on every row', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult([watchRow({ u_pts: 20, c_pts: 20 }), ...benchPool(60)]))
      .mockResolvedValueOnce(
        pgResult([baselineRow('1630559', { minutes: 22, pts: 11 }), ...benchBaselines(60)])
      )
      .mockResolvedValueOnce(pgResult(gameTeamRows));

    const res = await request(app).get('/api/watchlist').query({ date: '2026-02-04' });

    const candidate = res.body.players[0];
    expect(candidate.position).toBe('SG/SF');
    expect(candidate.games_count).toBe(1);
    expect(candidate.score_per_game).toBe(candidate.score);
    expect(candidate.games).toHaveLength(1);
    expect(candidate.games[0]).toMatchObject({
      game_date: '2026-02-04',
      opponent_team_abbr: 'LAL',
      minutes_p50: 31,
      proj_pts: 20,
    });
    expect(res.body.position).toBeNull();
    expect(res.body.position_coverage).toEqual({ known: 61, unknown: 0 });
  });
});

describe('GET /api/watchlist over a window', () => {
  const DATES = ['2026-02-04', '2026-02-05', '2026-02-06'];

  it('sums a player over the window and lists his games', async () => {
    const rows = DATES.flatMap((date) => [
      watchRowOn(date, { u_pts: 20, c_pts: 20 }),
      ...benchPoolOn(date, 60),
    ]);
    queryMock
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(rows))
      .mockResolvedValueOnce(
        pgResult([baselineRow('1630559', { minutes: 22, pts: 11 }), ...benchBaselines(60)])
      )
      .mockResolvedValueOnce(pgResult([]));

    const res = await request(app)
      .get('/api/watchlist')
      .query({ date: '2026-02-04', days: 3 });

    expect(res.status).toBe(200);
    expect(res.body.window).toEqual({ from: '2026-02-04', to: '2026-02-06', days: 3 });
    const candidate = res.body.players[0];
    expect(candidate.games_count).toBe(3);
    expect(candidate.games.map((g: { game_date: string }) => g.game_date)).toEqual(DATES);
    expect(candidate.score).toBeCloseTo(3 * candidate.score_per_game, 3);
    expect(candidate.score).toBeGreaterThan(candidate.upside * candidate.relevance);
    expect(candidate.totals.pts).toBe(60);
  });

  it('takes three round trips regardless of how many days the window covers', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(DATES.flatMap((date) => [watchRowOn(date)])))
      .mockResolvedValueOnce(pgResult([]))
      .mockResolvedValueOnce(pgResult([]));

    await request(app).get('/api/watchlist').query({ date: '2026-02-04', days: 14 });

    expect(queryMock).toHaveBeenCalledTimes(4);
    const [, predictionParams] = queryMock.mock.calls[1];
    expect(predictionParams?.slice(0, 3)).toEqual([42, '2026-02-04', '2026-02-17']);
    const [, scheduleParams] = queryMock.mock.calls[3];
    expect(scheduleParams).toEqual(['2026-02-04', '2026-02-17']);
  });

  it('takes the baseline once, as of the window start', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(DATES.flatMap((date) => [watchRowOn(date)])))
      .mockResolvedValueOnce(pgResult([]))
      .mockResolvedValueOnce(pgResult([]));

    await request(app).get('/api/watchlist').query({ date: '2026-02-04', days: 7 });

    const [sql, params] = queryMock.mock.calls[2];
    expect(sql).toContain('g.game_date < $1');
    expect(params?.[0]).toBe('2026-02-04');
  });

  it('lets a four-game week beat a two-game week for a better player', async () => {
    const busy = DATES.map((date) =>
      watchRowOn(date, { nba_player_id: 'busy', name: 'Busy Wing', u_pts: 17, c_pts: 17 })
    );
    const rested = DATES.slice(0, 1).map((date) =>
      watchRowOn(date, { nba_player_id: 'rested', name: 'Rested Wing', u_pts: 24, c_pts: 24 })
    );
    const rows = [...busy, ...rested, ...DATES.flatMap((date) => benchPoolOn(date, 60))];
    queryMock
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(rows))
      .mockResolvedValueOnce(
        pgResult([
          baselineRow('busy', { minutes: 22, pts: 11 }),
          baselineRow('rested', { minutes: 22, pts: 11 }),
          ...benchBaselines(60),
        ])
      )
      .mockResolvedValueOnce(pgResult([]));

    const res = await request(app)
      .get('/api/watchlist')
      .query({ date: '2026-02-04', days: 3 });

    const ids = res.body.players.map((p: { nba_player_id: string }) => p.nba_player_id);
    expect(ids.indexOf('busy')).toBeLessThan(ids.indexOf('rested'));
    const busyRow = res.body.players.find((p: { nba_player_id: string }) => p.nba_player_id === 'busy');
    const restedRow = res.body.players.find(
      (p: { nba_player_id: string }) => p.nba_player_id === 'rested'
    );
    expect(busyRow.games_count).toBe(3);
    expect(restedRow.games_count).toBe(1);
    expect(busyRow.points.projected).toBeLessThan(restedRow.points.projected);
    expect(busyRow.score).toBeGreaterThan(restedRow.score);
  });

  it('filters to a roster slot, and echoes which filter it applied', async () => {
    const rows = [
      watchRow({ nba_player_id: 'guard', name: 'Combo Guard', position: 'PG,SG', u_pts: 20, c_pts: 20 }),
      watchRow({ nba_player_id: 'big', name: 'Starting Five', position: 'C,PF', u_pts: 20, c_pts: 20 }),
      ...benchPool(60),
    ];
    const baselines = [
      baselineRow('guard', { minutes: 22, pts: 11 }),
      baselineRow('big', { minutes: 22, pts: 11 }),
      ...benchBaselines(60),
    ];
    queryMock
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(rows))
      .mockResolvedValueOnce(pgResult(baselines))
      .mockResolvedValueOnce(pgResult(gameTeamRows));

    const res = await request(app)
      .get('/api/watchlist')
      .query({ date: '2026-02-04', position: 'G' });

    expect(res.status).toBe(200);
    expect(res.body.position).toBe('G');
    expect(res.body.players.map((p: { nba_player_id: string }) => p.nba_player_id)).toEqual([
      'guard',
    ]);
    expect(res.body.players[0].position).toBe('PG/SG');
  });

  it('counts the candidates it could not place at a position', async () => {
    const rows = [
      watchRow({ nba_player_id: 'guard', position: 'PG,SG', u_pts: 20, c_pts: 20 }),
      watchRow({ nba_player_id: 'ghost', name: null, position: null, u_pts: 20, c_pts: 20 }),
      ...benchPool(60),
    ];
    const baselines = [
      baselineRow('guard', { minutes: 22, pts: 11 }),
      baselineRow('ghost', { minutes: 22, pts: 11 }),
      ...benchBaselines(60),
    ];
    queryMock
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(rows))
      .mockResolvedValueOnce(pgResult(baselines))
      .mockResolvedValueOnce(pgResult(gameTeamRows));

    const res = await request(app)
      .get('/api/watchlist')
      .query({ date: '2026-02-04', position: 'G' });

    expect(res.body.position_coverage).toEqual({ known: 61, unknown: 1 });
    expect(res.body.players.map((p: { nba_player_id: string }) => p.nba_player_id)).toEqual([
      'guard',
    ]);
  });

  it('rejects a window it will not answer for, without touching the database', async () => {
    const tooLong = await request(app)
      .get('/api/watchlist')
      .query({ date: '2026-02-04', days: MAX_WINDOW_DAYS + 1 });
    const fractional = await request(app)
      .get('/api/watchlist')
      .query({ date: '2026-02-04', days: '3.5' });

    expect(tooLong.status).toBe(400);
    expect(tooLong.body.error).toMatch(new RegExp(`between 1 and ${MAX_WINDOW_DAYS}`));
    expect(fractional.status).toBe(400);
    expect(queryMock).not.toHaveBeenCalled();
  });

  it('rejects a position it does not publish, without touching the database', async () => {
    const res = await request(app)
      .get('/api/watchlist')
      .query({ date: '2026-02-04', position: 'WING' });

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/position must be one of/);
    expect(queryMock).not.toHaveBeenCalled();
  });
});

describe('prediction run channel', () => {
  it('serves the slate from a production run, never a shadow one', async () => {
    // arrange
    queryMock
      .mockResolvedValueOnce(pgResult(scheduleRows))
      .mockResolvedValueOnce(pgResult([]))
      .mockResolvedValueOnce(pgResult(teamRows));

    // act
    const res = await request(app).get('/api/predictions/slate').query({ date: '2026-02-04' });

    // assert
    expect(res.status).toBe(200);
    const [sql, params] = queryMock.mock.calls[1];
    expect(sql).toContain('FROM prediction_runs');
    expect(params).toContain(PRODUCTION_CHANNEL);
  });

  it('builds the watchlist from a production run, never a shadow one', async () => {
    // arrange
    queryMock.mockResolvedValueOnce(pgResult([]));

    // act
    const res = await request(app).get('/api/watchlist').query({ date: '2026-02-04' });

    // assert
    expect(res.status).toBe(200);
    const [sql, params] = queryMock.mock.calls[0];
    expect(sql).toContain('FROM prediction_runs');
    expect(params).toContain(PRODUCTION_CHANNEL);
  });
});
