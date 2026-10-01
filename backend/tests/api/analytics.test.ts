import { describe, it, expect, vi, beforeEach } from 'vitest';
import request from 'supertest';
import { pgResult } from '../helpers/mockDb.js';

const { app } = await import('../../src/app.js');
const { query } = await import('../../src/db.js');
const { clearAnalyticsCache, ANALYTICS_STATS, POOL_DEFINITION } = await import(
  '../../src/services/analytics.js'
);
const { clearPredictionsCache } = await import('../../src/services/predictions.js');
const { COMPLETE_RUN_STATUS, PRODUCTION_CHANNEL } = await import('../../src/services/slate.js');
const queryMock = vi.mocked(query);


const lebronRow = {
  id: 5,
  nba_id: '2544',
  name: 'LeBron James',
  team: 'LAL',
  position: 'SF',
  headshot_url: 'https://cdn.nba.com/headshots/2544.png',
  pts: 25.4,
  reb: 7.2,
  ast: 8.1,
  stl: 1.1,
  blk: 0.6,
  fg3m: 2.1,
  tov: 3.4,
  minutes: 35.2,
};

const poolRows = [
  { ...lebronRow },
  {
    id: 6, nba_id: '201939', name: 'Stephen Curry', pts: 29.1, reb: 5.0, ast: 6.3,
    stl: 1.3, blk: 0.2, fg3m: 5.1, tov: 3.1, minutes: 34.0,
  },
  {
    id: 7, nba_id: '203507', name: 'Giannis Antetokounmpo', pts: 30.2, reb: 11.4, ast: 5.7,
    stl: 1.0, blk: 1.2, fg3m: 0.8, tov: 3.9, minutes: 33.1,
  },
];

const poolTotalsRows = [
  { nba_player_id: '2544', fgm: 200, fga: 400, ftm: 80, fta: 100, games: 20 },
  { nba_player_id: '201939', fgm: 190, fga: 420, ftm: 90, fta: 100, games: 20 },
  { nba_player_id: '203507', fgm: 230, fga: 400, ftm: 110, fta: 180, games: 20 },
];

const teamRows = [
  { team_id: '1610612744', team_abbr: 'GSW' },
  { team_id: '1610612749', team_abbr: 'MIL' },
];

function logRow(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    game_date: '2026-01-02',
    opponent_team_id: '1610612744',
    is_home: true,
    minutes: 35,
    pts: 25, reb: 7, ast: 8, stl: 1, blk: 1, tov: 3,
    fgm: 10, fga: 20, fg3m: 2, fg3a: 5, ftm: 3, fta: 4,
    ...overrides,
  };
}

const logRows = Array.from({ length: 20 }, (_, i) =>
  logRow({
    game_date: `2026-01-${String(i + 1).padStart(2, '0')}`,
    pts: 20 + i,
    is_home: i % 2 === 0,
  })
);

function predictionRow(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    model_version: '2026-02-28',
    predicted_at: new Date('2026-03-01T13:30:00.000Z'),
    game_date: new Date(2026, 2, 2),
    stat: 'pts',
    quantile: null,
    value: 25,
    conditional: true,
    ...overrides,
  };
}

function mockPlayerAnalyticsQueries(
  logs: Array<Record<string, unknown>> = logRows,
  predictions: Array<Record<string, unknown>> = []
): void {
  queryMock
    .mockResolvedValueOnce(pgResult([lebronRow]))
    .mockResolvedValueOnce(pgResult(poolRows))
    .mockResolvedValueOnce(pgResult(poolTotalsRows))
    .mockResolvedValueOnce(pgResult(teamRows))
    .mockResolvedValueOnce(pgResult(logs))
    .mockResolvedValueOnce(pgResult(predictions));
}

function mockPoolQueries(): void {
  queryMock
    .mockResolvedValueOnce(pgResult(poolRows))
    .mockResolvedValueOnce(pgResult(poolTotalsRows))
    .mockResolvedValueOnce(pgResult(teamRows));
}

beforeEach(() => {
  queryMock.mockReset();
  clearAnalyticsCache();
  clearPredictionsCache();
});

describe('GET /api/players/:id/analytics', () => {
  it('returns the player, pool, percentiles, distributions and trends', async () => {
    mockPlayerAnalyticsQueries();

    const res = await request(app).get('/api/players/5/analytics');

    expect(res.status).toBe(200);
    expect(res.body.player).toEqual({
      id: 5,
      nba_id: '2544',
      name: 'LeBron James',
      team: 'LAL',
      position: 'SF',
      headshot_url: 'https://cdn.nba.com/headshots/2544.png',
      injury_status: null,
      injury_detail: null,
    });
    expect(res.body.pool).toEqual({
      key: 'rotation',
      label: 'Rotation players',
      definition: POOL_DEFINITION,
      sample_size: 3,
    });
    expect(res.body.prediction).toBeNull();
    expect(res.body.as_of.logs).toBe('2026-01-20T00:00:00.000Z');
    expect(typeof res.body.as_of.distributions).toBe('string');
  });

  it('covers every whitelisted stat in percentiles and distributions', async () => {
    mockPlayerAnalyticsQueries();

    const res = await request(app).get('/api/players/5/analytics');

    expect(res.body.percentiles.map((p: { stat: string }) => p.stat)).toEqual([
      ...ANALYTICS_STATS,
    ]);
    expect(res.body.distributions.map((d: { stat: string }) => d.stat)).toEqual([
      ...ANALYTICS_STATS,
    ]);
    const points = res.body.percentiles.find((p: { stat: string }) => p.stat === 'pts');
    expect(points.value).toBe(25.4);
    expect(points.percentile).toBeLessThan(50);
  });

  it('ranks turnovers in reverse, so the lowest-turnover player scores highest', async () => {
    mockPlayerAnalyticsQueries();

    const res = await request(app).get('/api/players/5/analytics');

    const turnovers = res.body.percentiles.find((p: { stat: string }) => p.stat === 'tov');
    expect(turnovers.percentile).toBe(50);
  });

  it('marks each distribution with the player value and equal-width buckets', async () => {
    mockPlayerAnalyticsQueries();

    const res = await request(app).get('/api/players/5/analytics');

    const points = res.body.distributions.find((d: { stat: string }) => d.stat === 'pts');
    expect(points.player_value).toBe(25.4);
    expect(points.mean).toBeCloseTo(28.233, 3);
    expect(points.buckets).toHaveLength(20);
    const counted = points.buckets.reduce(
      (sum: number, b: { count: number }) => sum + b.count,
      0
    );
    expect(counted).toBe(3);
  });

  it('returns the last 20 games oldest first with the opponent abbreviation', async () => {
    const many = Array.from({ length: 24 }, (_, i) =>
      logRow({ game_date: `2026-02-${String(i + 1).padStart(2, '0')}`, pts: i })
    );
    mockPlayerAnalyticsQueries(many);

    const res = await request(app).get('/api/players/5/analytics');

    expect(res.body.trends.games).toHaveLength(20);
    expect(res.body.trends.games[0].game_date).toBe('2026-02-05');
    expect(res.body.trends.games[19].game_date).toBe('2026-02-24');
    expect(res.body.trends.games[0].opponent_team_abbr).toBe('GSW');
    expect(res.body.trends.games[0].is_home).toBe(true);
  });

  it('aligns the rolling series with the returned games, using history before the window', async () => {
    const many = Array.from({ length: 24 }, (_, i) =>
      logRow({ game_date: `2026-02-${String(i + 1).padStart(2, '0')}`, pts: 20 + i })
    );
    mockPlayerAnalyticsQueries(many);

    const res = await request(app).get('/api/players/5/analytics');

    const { games, rolling } = res.body.trends;
    expect(rolling).toHaveLength(games.length);
    expect(rolling[0].game_date).toBe(games[0].game_date);
    expect(rolling[0].pts_r5).toBe(22);
    expect(rolling[0].pts_r10).toBeNull();
    expect(rolling[5].pts_r10).toBe(24.5);
  });

  it('compares the last ten games to the season with a volatility-scaled z', async () => {
    mockPlayerAnalyticsQueries();

    const res = await request(app).get('/api/players/5/analytics');

    const points = res.body.trends.last10_vs_season.find(
      (c: { stat: string }) => c.stat === 'pts'
    );
    expect(points).toMatchObject({ last10: 34.5, season: 29.5, delta: 5 });
    expect(points.z).toBeCloseTo(0.87, 2);
  });

  it('nulls the z-score for a player with fewer than 15 logged games', async () => {
    const few = Array.from({ length: 8 }, (_, i) =>
      logRow({ game_date: `2026-03-0${i + 1}`, pts: 10 + i })
    );
    mockPlayerAnalyticsQueries(few);

    const res = await request(app).get('/api/players/5/analytics');

    const points = res.body.trends.last10_vs_season.find(
      (c: { stat: string }) => c.stat === 'pts'
    );
    expect(points.z).toBeNull();
    expect(res.body.trends.rolling.every((r: { pts_r10: number | null }) => r.pts_r10 === null))
      .toBe(true);
  });

  it('still returns percentiles and distributions for a player with no game logs', async () => {
    mockPlayerAnalyticsQueries([]);

    const res = await request(app).get('/api/players/5/analytics');

    expect(res.status).toBe(200);
    expect(res.body.as_of.logs).toBeNull();
    expect(res.body.trends).toEqual({ games: [], rolling: [], last10_vs_season: [] });
    expect(res.body.percentiles).toHaveLength(ANALYTICS_STATS.length);
    expect(res.body.distributions).toHaveLength(ANALYTICS_STATS.length);
    const fgImpact = res.body.percentiles.find(
      (p: { stat: string }) => p.stat === 'fg_impact'
    );
    expect(fgImpact.value).toBe(0);
  });

  it('binds the player id as a query parameter rather than interpolating it', async () => {
    mockPlayerAnalyticsQueries();

    await request(app).get('/api/players/5/analytics');

    const [sql, params] = queryMock.mock.calls[0];
    expect(params).toEqual([5]);
    expect(sql).toContain('$1');
  });

  it('reuses the cached pool snapshot on the next request', async () => {
    mockPlayerAnalyticsQueries();
    await request(app).get('/api/players/5/analytics');
    const afterFirst = queryMock.mock.calls.length;
    queryMock
      .mockResolvedValueOnce(pgResult([lebronRow]))
      .mockResolvedValueOnce(pgResult(logRows));

    const res = await request(app).get('/api/players/5/analytics');

    expect(res.status).toBe(200);
    expect(afterFirst).toBe(6);
    expect(queryMock.mock.calls.length - afterFirst).toBe(2);
    expect(res.body.pool.sample_size).toBe(3);
  });

  it('returns 404 when no player has that id', async () => {
    queryMock.mockResolvedValueOnce(pgResult([]));

    const res = await request(app).get('/api/players/999999/analytics');

    expect(res.status).toBe(404);
    expect(res.body.error).toBe('Player not found');
  });

  it('returns 400 for a non-numeric player id', async () => {
    const res = await request(app).get('/api/players/lebron/analytics');

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/numeric player id/i);
    expect(queryMock).not.toHaveBeenCalled();
  });

  it('returns 500 when the database query fails', async () => {
    queryMock.mockRejectedValue(new Error('db down'));

    const res = await request(app).get('/api/players/5/analytics');

    expect(res.status).toBe(500);
    expect(res.body.error).toBe('Failed to fetch player analytics');
  });

  it('serves the newest stored prediction alongside the history', async () => {
    mockPlayerAnalyticsQueries(logRows, [
      predictionRow({ stat: 'prob_active', value: 0.82, conditional: false }),
      predictionRow({ stat: 'ast', value: 8 }),
      predictionRow({ stat: 'pts_uncond', value: 20.5, conditional: false }),
      predictionRow({ stat: 'minutes', quantile: 0.1, value: 26 }),
      predictionRow({ stat: 'minutes', quantile: 0.5, value: 34.5 }),
      predictionRow({ stat: 'minutes', quantile: 0.9, value: 41 }),
      predictionRow({ stat: 'pts', quantile: 0.1, value: 14 }),
      predictionRow({ stat: 'pts', quantile: 0.5, value: 24.5 }),
      predictionRow({ stat: 'pts', quantile: 0.9, value: 37 }),
    ]);

    const res = await request(app).get('/api/players/5/analytics');

    expect(res.status).toBe(200);
    expect(res.body.prediction).toMatchObject({
      as_of: '2026-03-01T13:30:00.000Z',
      model_version: '2026-02-28',
      game_date: '2026-03-02',
      prob_active: 0.82,
      conditional: true,
      unconditional_pts: 20.5,
    });
    expect(res.body.prediction.projected.minutes).toEqual({ p10: 26, p50: 34.5, p90: 41 });
    expect(res.body.prediction.projected.ast).toBe(8);
    expect(res.body.prediction.projected.reb).toBeNull();
  });

  it('binds the nba player id to the prediction query rather than the row id', async () => {
    mockPlayerAnalyticsQueries(logRows, [predictionRow()]);

    await request(app).get('/api/players/5/analytics');

    const [sql, params] = queryMock.mock.calls[5];
    expect(sql).toContain('player_game_predictions');
    expect(params).toEqual(['2544', COMPLETE_RUN_STATUS, PRODUCTION_CHANNEL]);
  });

  it('keeps the rest of the page when the prediction tables are missing', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([lebronRow]))
      .mockResolvedValueOnce(pgResult(poolRows))
      .mockResolvedValueOnce(pgResult(poolTotalsRows))
      .mockResolvedValueOnce(pgResult(teamRows))
      .mockResolvedValueOnce(pgResult(logRows))
      .mockRejectedValueOnce(new Error('relation "player_game_predictions" does not exist'));

    const res = await request(app).get('/api/players/5/analytics');

    expect(res.status).toBe(200);
    expect(res.body.prediction).toBeNull();
    expect(res.body.percentiles).toHaveLength(ANALYTICS_STATS.length);
  });
});

describe('GET /api/analytics/distributions', () => {
  it('returns the pool shape and every player value for one stat', async () => {
    mockPoolQueries();

    const res = await request(app).get('/api/analytics/distributions').query({ stat: 'pts' });

    expect(res.status).toBe(200);
    expect(res.body.stat).toBe('pts');
    expect(res.body.pool).toEqual({
      key: 'rotation',
      label: 'Rotation players',
      definition: POOL_DEFINITION,
      sample_size: 3,
    });
    expect(res.body.mean).toBeCloseTo(28.233, 3);
    expect(res.body.buckets).toHaveLength(20);
    expect(res.body.players).toHaveLength(3);
    expect(res.body.players[0]).toEqual({
      id: 7,
      name: 'Giannis Antetokounmpo',
      value: 30.2,
      percentile: 83.3,
    });
  });

  it('accepts an explicit rotation pool', async () => {
    mockPoolQueries();

    const res = await request(app)
      .get('/api/analytics/distributions')
      .query({ stat: 'blk', pool: 'rotation' });

    expect(res.status).toBe(200);
    expect(res.body.stat).toBe('blk');
  });

  it('orders turnovers best-first, so the fewest turnovers lead', async () => {
    mockPoolQueries();

    const res = await request(app).get('/api/analytics/distributions').query({ stat: 'tov' });

    expect(res.body.players[0].name).toBe('Stephen Curry');
    expect(res.body.players[0].percentile).toBe(83.3);
    expect(res.body.players[2].name).toBe('Giannis Antetokounmpo');
  });

  it('returns the attempt-weighted shooting impact as a stat', async () => {
    mockPoolQueries();

    const res = await request(app)
      .get('/api/analytics/distributions')
      .query({ stat: 'fg_impact' });

    expect(res.status).toBe(200);
    expect(res.body.players[0].name).toBe('Giannis Antetokounmpo');
    expect(res.body.players[0].value).toBeGreaterThan(0);
  });

  it('returns 400 for a stat outside the whitelist', async () => {
    const res = await request(app)
      .get('/api/analytics/distributions')
      .query({ stat: 'points_per_game' });

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/stat must be one of/i);
    expect(queryMock).not.toHaveBeenCalled();
  });

  it('returns 400 when stat is missing', async () => {
    const res = await request(app).get('/api/analytics/distributions');

    expect(res.status).toBe(400);
    expect(queryMock).not.toHaveBeenCalled();
  });

  it('returns 400 for an unknown pool', async () => {
    const res = await request(app)
      .get('/api/analytics/distributions')
      .query({ stat: 'pts', pool: 'starters' });

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/pool must be/i);
    expect(queryMock).not.toHaveBeenCalled();
  });

  it('returns 500 when the database query fails', async () => {
    queryMock.mockRejectedValue(new Error('db down'));

    const res = await request(app).get('/api/analytics/distributions').query({ stat: 'pts' });

    expect(res.status).toBe(500);
    expect(res.body.error).toBe('Failed to fetch stat distribution');
  });
});
