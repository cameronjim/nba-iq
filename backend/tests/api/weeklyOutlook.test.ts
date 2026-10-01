import { describe, it, expect, vi, beforeEach } from 'vitest';
import request from 'supertest';
import { app } from '../../src/app.js';
import { query } from '../../src/db.js';
import { bearerFor } from '../helpers/authToken.js';
import { pgResult } from '../helpers/mockDb.js';

const queryMock = vi.mocked(query);

const RUN_ROW = { id: 9, model_version: 'v-test', predicted_at: '2026-01-14T18:00:00.000Z' };

function predictionRows(
  nbaId: string,
  gameId: string,
  gameDate: string,
  prob: number,
  values: Record<string, number>,
  withQuantiles = true
): Array<Record<string, unknown>> {
  const rows: Array<Record<string, unknown>> = [
    { nba_player_id: nbaId, nba_game_id: gameId, game_date: gameDate, stat: 'prob_active', quantile: null, value: prob, conditional: false },
  ];
  for (const [stat, value] of Object.entries(values)) {
    rows.push({ nba_player_id: nbaId, nba_game_id: gameId, game_date: gameDate, stat, quantile: null, value, conditional: true });
    if (!withQuantiles) continue;
    rows.push({ nba_player_id: nbaId, nba_game_id: gameId, game_date: gameDate, stat, quantile: 0.1, value: value * 0.6, conditional: true });
    rows.push({ nba_player_id: nbaId, nba_game_id: gameId, game_date: gameDate, stat, quantile: 0.5, value, conditional: true });
    rows.push({ nba_player_id: nbaId, nba_game_id: gameId, game_date: gameDate, stat, quantile: 0.9, value: value * 1.4, conditional: true });
  }
  return rows;
}

const LINE = { pts: 20, reb: 6, ast: 4, stl: 1, blk: 0.5, tov: 2, fg3m: 2, fgm: 7, fga: 15, ftm: 4, fta: 5 };

function poolRow(id: string, scale: number): Record<string, unknown> {
  const row: Record<string, unknown> = { nba_player_id: id };
  for (const [stat, value] of Object.entries(LINE)) row[stat] = value * scale;
  return row;
}

beforeEach(() => {
  queryMock.mockReset();
});

describe('GET /api/fantasy/weekly-outlook', () => {
  it('requires authentication', async () => {
    // act
    const res = await request(app).get('/api/fantasy/weekly-outlook');

    // assert
    expect(res.status).toBe(401);
    expect(queryMock).not.toHaveBeenCalled();
  });

  it('rejects a malformed start date', async () => {
    // act
    const res = await request(app)
      .get('/api/fantasy/weekly-outlook?start=2026-02-31')
      .set('Authorization', bearerFor(1));

    // assert
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/start/);
  });

  it('rejects days outside the window limit', async () => {
    // act
    const res = await request(app)
      .get('/api/fantasy/weekly-outlook?start=2026-01-15&days=30')
      .set('Authorization', bearerFor(1));

    // assert
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/days/);
  });

  it('returns an explicit empty-roster shape bound to the caller', async () => {
    // arrange
    queryMock.mockResolvedValueOnce(pgResult([]));

    // act
    const res = await request(app)
      .get('/api/fantasy/weekly-outlook?start=2026-01-15&user_id=99')
      .set('Authorization', bearerFor(7));

    // assert
    expect(res.status).toBe(200);
    expect(res.body).toMatchObject({
      status: 'empty_roster',
      window: { from: '2026-01-15', to: '2026-01-21', days: 7 },
      roster_size: 0,
      run: null,
      categories: [],
      players: [],
    });
    expect(queryMock.mock.calls[0][1]).toEqual([7]);
  });

  it('returns a no-run shape listing the roster when no production run exists', async () => {
    // arrange
    queryMock
      .mockResolvedValueOnce(pgResult([{ player_id: 3, nba_id: '101', name: 'Ann Guard' }]))
      .mockResolvedValueOnce(pgResult([]));

    // act
    const res = await request(app)
      .get('/api/fantasy/weekly-outlook?start=2026-01-15')
      .set('Authorization', bearerFor(1));

    // assert
    expect(res.status).toBe(200);
    expect(res.body.status).toBe('no_run');
    expect(res.body.players).toEqual([
      expect.objectContaining({ player_id: 3, name: 'Ann Guard', games_scheduled: 0 }),
    ]);
    expect(queryMock.mock.calls[1][1]).toContain('production');
  });

  it('returns a no-games shape when the roster has nothing scheduled in the window', async () => {
    // arrange
    queryMock
      .mockResolvedValueOnce(pgResult([{ player_id: 3, nba_id: '101', name: 'Ann Guard' }]))
      .mockResolvedValueOnce(pgResult([RUN_ROW]))
      .mockResolvedValueOnce(pgResult([]));

    // act
    const res = await request(app)
      .get('/api/fantasy/weekly-outlook?start=2026-01-15')
      .set('Authorization', bearerFor(1));

    // assert
    expect(res.status).toBe(200);
    expect(res.body).toMatchObject({
      status: 'no_games',
      run: { model_version: 'v-test' },
      provenance: { model_version: 'v-test', fallback_spread_players: [] },
    });
  });

  it('simulates the roster week and reports categories, miss risk and provenance', async () => {
    // arrange
    queryMock
      .mockResolvedValueOnce(
        pgResult([
          { player_id: 3, nba_id: '101', name: 'Ann Guard' },
          { player_id: 4, nba_id: '202', name: 'Bo Center' },
        ])
      )
      .mockResolvedValueOnce(pgResult([RUN_ROW]))
      .mockResolvedValueOnce(
        pgResult([
          ...predictionRows('101', 'g1', '2026-01-15', 0.9, LINE),
          ...predictionRows('101', 'g2', '2026-01-17', 0.8, LINE),
          ...predictionRows('202', 'g3', '2026-01-16', 1, LINE, false),
        ])
      )
      .mockResolvedValueOnce(pgResult([poolRow('101', 2), poolRow('202', 1), poolRow('303', 1.5)]));

    // act
    const res = await request(app)
      .get('/api/fantasy/weekly-outlook?start=2026-01-15&days=7')
      .set('Authorization', bearerFor(5));

    // assert
    expect(res.status).toBe(200);
    expect(res.body.status).toBe('ok');
    expect(queryMock.mock.calls[0][1]).toEqual([5]);
    expect(queryMock.mock.calls[2][1]).toEqual(
      expect.arrayContaining(['production', ['101', '202'], '2026-01-15', '2026-01-21'])
    );
    expect(res.body.categories.map((c: { category: string }) => c.category)).toEqual([
      'pts', 'reb', 'ast', 'stl', 'blk', 'fg3m', 'fg_pct', 'ft_pct', 'tov',
    ]);
    for (const category of res.body.categories) {
      expect(category.win_probability).toBeGreaterThanOrEqual(0);
      expect(category.win_probability).toBeLessThanOrEqual(1);
    }
    const ann = res.body.players.find((p: { player_id: number }) => p.player_id === 3);
    expect(ann.games_scheduled).toBe(2);
    expect(ann.miss_risk).toBeGreaterThan(0.2);
    expect(ann.miss_risk).toBeLessThan(0.28);
    expect(res.body.simulation).toMatchObject({ n: 2000, dependence_rho: 0.5 });
    expect(res.body.opponent.players_used).toBe(3);
    expect(res.body.provenance).toMatchObject({
      model_version: 'v-test',
      predicted_at: '2026-01-14T18:00:00.000Z',
      fallback_spread_players: [{ nba_player_id: '202', name: 'Bo Center' }],
    });
  });
});
