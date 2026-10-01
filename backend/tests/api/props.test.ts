import { describe, it, expect, vi, beforeEach } from 'vitest';
import request from 'supertest';
import { app } from '../../src/app.js';
import { query } from '../../src/db.js';
import { bearerFor } from '../helpers/authToken.js';
import { pgResult } from '../helpers/mockDb.js';

const queryMock = vi.mocked(query);

beforeEach(() => {
  queryMock.mockReset();
});

function mockAdminCheck(isAdmin: boolean): void {
  queryMock.mockResolvedValueOnce(pgResult([{ is_admin: isAdmin }]));
}

function missingRelation(): Error & { code: string } {
  const err = new Error('relation "model_prop_picks" does not exist') as Error & { code: string };
  err.code = '42P01';
  return err;
}

const RUN_ROW = {
  id: 42,
  model_version: 'v2.1',
  predicted_at: '2026-10-21T16:00:00.000Z',
  information_as_of: '2026-10-21T15:30:00.000Z',
  covers_from: '2026-10-21',
  covers_to: '2026-10-27',
};

describe('GET /api/betting/props', () => {
  it('returns an empty list with no run when nothing has been surfaced', async () => {
    // arrange
    queryMock.mockResolvedValueOnce(pgResult([]));

    // act
    const res = await request(app).get('/api/betting/props');

    // assert
    expect(res.status).toBe(200);
    expect(res.body.run).toBeNull();
    expect(res.body.picks).toEqual([]);
    expect(res.body.dates).toHaveLength(2);
    expect(res.body.rules).toMatchObject({ void_rule: 'dnp_void', paper_trading: true });
  });

  it('returns an empty list when the ledger table does not exist yet', async () => {
    // arrange
    queryMock.mockRejectedValueOnce(missingRelation());

    // act
    const res = await request(app).get('/api/betting/props');

    // assert
    expect(res.status).toBe(200);
    expect(res.body.picks).toEqual([]);
  });

  it('returns surfaced picks with the run provenance', async () => {
    // arrange
    queryMock.mockResolvedValueOnce(
      pgResult([
        {
          id: 7,
          prediction_run_id: 42,
          nba_player_id: '201939',
          player_name: 'Stephen Curry',
          nba_game_id: '0022600001',
          game_date: '2026-10-21',
          market: 'pts',
          line: 24.5,
          side: 'over',
          bookmaker: 'draftkings',
          price: -110,
          implied_prob: 0.5238,
          implied_prob_novig: 0.5,
          model_prob: 0.675,
          model_prob_plays: 0.675,
          prob_active: 0.95,
          void_rule: 'dnp_void',
          ev: 0.2886,
          kelly_fraction: 0.02,
          surfaced_at: '2026-10-21T17:00:00.000Z',
          model_version: 'v2.1',
          predicted_at: '2026-10-21T16:00:00.000Z',
          information_as_of: '2026-10-21T15:30:00.000Z',
        },
      ])
    );

    // act
    const res = await request(app).get('/api/betting/props');

    // assert
    expect(res.status).toBe(200);
    expect(res.body.run).toEqual({
      id: 42,
      model_version: 'v2.1',
      predicted_at: '2026-10-21T16:00:00.000Z',
      information_as_of: '2026-10-21T15:30:00.000Z',
    });
    expect(res.body.picks[0]).toMatchObject({
      id: 7,
      player_name: 'Stephen Curry',
      market: 'pts',
      line: 24.5,
      side: 'over',
      price: -110,
      edge: 0.175,
    });
  });

  it('returns an error shape when the query fails', async () => {
    // arrange
    queryMock.mockRejectedValueOnce(new Error('boom'));

    // act
    const res = await request(app).get('/api/betting/props');

    // assert
    expect(res.status).toBe(500);
    expect(res.body).toEqual({ error: 'Failed to load prop picks' });
  });
});

describe('GET /api/betting/props/summary', () => {
  it('returns no markets for an empty ledger', async () => {
    // arrange
    queryMock.mockResolvedValueOnce(pgResult([]));

    // act
    const res = await request(app).get('/api/betting/props/summary');

    // assert
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ markets: [] });
  });

  it('summarizes settled picks overall and by market', async () => {
    // arrange
    queryMock.mockResolvedValueOnce(
      pgResult([
        { market: 'pts', result: 'win', price: 100, line: 24.5, ev: 0.05, model_prob: 0.55, closing_price: -120, closing_line: 24.5 },
        { market: 'pts', result: 'loss', price: 100, line: 24.5, ev: 0.05, model_prob: 0.55, closing_price: null, closing_line: null },
      ])
    );

    // act
    const res = await request(app).get('/api/betting/props/summary');

    // assert
    expect(res.status).toBe(200);
    expect(res.body.markets.map((m: { market: string }) => m.market)).toEqual(['all', 'pts']);
    expect(res.body.markets[1]).toMatchObject({ wins: 1, losses: 1, hit_rate: 0.5, clv_count: 1 });
  });
});

describe('POST /api/betting/props/refresh', () => {
  it('requires a token', async () => {
    // act
    const res = await request(app).post('/api/betting/props/refresh');

    // assert
    expect(res.status).toBe(401);
    expect(queryMock).not.toHaveBeenCalled();
  });

  it('rejects a non-admin user', async () => {
    // arrange
    mockAdminCheck(false);

    // act
    const res = await request(app).post('/api/betting/props/refresh').set('Authorization', bearerFor(2));

    // assert
    expect(res.status).toBe(403);
  });

  it('returns an empty refresh when no production run exists', async () => {
    // arrange
    mockAdminCheck(true);
    queryMock.mockResolvedValueOnce(pgResult([]));

    // act
    const res = await request(app).post('/api/betting/props/refresh').set('Authorization', bearerFor(1));

    // assert
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ run: null, snapshots: 0, candidates: 0, recorded: 0, picks: [] });
  });

  it('returns an empty refresh when no prop snapshots exist', async () => {
    // arrange
    mockAdminCheck(true);
    queryMock.mockResolvedValueOnce(pgResult([RUN_ROW]));
    queryMock.mockRejectedValueOnce(missingRelation());

    // act
    const res = await request(app).post('/api/betting/props/refresh').set('Authorization', bearerFor(1));

    // assert
    expect(res.status).toBe(200);
    expect(res.body.run.id).toBe(42);
    expect(res.body.picks).toEqual([]);
    expect(queryMock).toHaveBeenCalledTimes(3);
  });

  it('records the picks that clear the thresholds for the latest run', async () => {
    // arrange
    mockAdminCheck(true);
    queryMock.mockResolvedValueOnce(pgResult([RUN_ROW]));
    queryMock.mockResolvedValueOnce(
      pgResult([
        {
          nba_player_id: '201939',
          player_name: 'Stephen Curry',
          nba_game_id: '0022600001',
          game_date: '2026-10-21',
          bookmaker: 'draftkings',
          market: 'pts',
          line: 24.5,
          over_price: -110,
          under_price: -110,
          captured_at: '2026-10-21T18:00:00.000Z',
        },
      ])
    );
    const forecast = (stat: string, quantile: number | null, value: number, conditional = true) => ({
      nba_player_id: '201939',
      nba_game_id: '0022600001',
      game_date: '2026-10-21',
      stat,
      quantile,
      value,
      conditional,
    });
    queryMock.mockResolvedValueOnce(
      pgResult([
        forecast('pts', null, 28),
        forecast('pts', 0.1, 20),
        forecast('pts', 0.5, 28),
        forecast('pts', 0.9, 36),
        forecast('prob_active', null, 0.95, false),
      ])
    );
    queryMock.mockResolvedValueOnce({ ...pgResult([]), rowCount: 1 });

    // act
    const res = await request(app).post('/api/betting/props/refresh').set('Authorization', bearerFor(1));

    // assert
    expect(res.status).toBe(200);
    expect(res.body).toMatchObject({ snapshots: 1, candidates: 2, recorded: 1 });
    expect(res.body.picks).toHaveLength(1);
    expect(res.body.picks[0]).toMatchObject({ side: 'over', void_rule: 'dnp_void', prob_active: 0.95 });
    expect(res.body.picks[0].model_prob).toBeCloseTo(0.675, 6);
    const [, insertParams] = queryMock.mock.calls[4];
    expect(insertParams?.[0]).toBe(42);
  });
});

describe('POST /api/betting/props/settle', () => {
  it('requires a token', async () => {
    // act
    const res = await request(app).post('/api/betting/props/settle');

    // assert
    expect(res.status).toBe(401);
  });

  it('rejects a non-admin user', async () => {
    // arrange
    mockAdminCheck(false);

    // act
    const res = await request(app).post('/api/betting/props/settle').set('Authorization', bearerFor(2));

    // assert
    expect(res.status).toBe(403);
  });

  it('settles nothing when no pick has a finished game', async () => {
    // arrange
    mockAdminCheck(true);
    queryMock.mockResolvedValueOnce(pgResult([]));

    // act
    const res = await request(app).post('/api/betting/props/settle').set('Authorization', bearerFor(1));

    // assert
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ examined: 0, settled: 0, by_result: { win: 0, loss: 0, push: 0, void: 0 } });
    expect(queryMock).toHaveBeenCalledTimes(2);
  });

  it('grades finished picks and writes the closing line', async () => {
    // arrange
    mockAdminCheck(true);
    const base = {
      market: 'pts', line: 24.5, side: 'over', void_rule: 'dnp_void',
      reb: 5, ast: 4, fg3m: 3, stl: 1, blk: 0, tov: 2,
    };
    queryMock.mockResolvedValueOnce(
      pgResult([
        { ...base, id: 1, has_log: true, minutes: 33, pts: 30, closing_line: 24.5, closing_price: -125 },
        { ...base, id: 2, has_log: false, minutes: null, pts: null, closing_line: null, closing_price: null },
      ])
    );
    queryMock.mockResolvedValueOnce({ ...pgResult([]), rowCount: 2 });

    // act
    const res = await request(app).post('/api/betting/props/settle').set('Authorization', bearerFor(1));

    // assert
    expect(res.status).toBe(200);
    expect(res.body).toMatchObject({ examined: 2, settled: 2, by_result: { win: 1, void: 1 } });
    const [, updateParams] = queryMock.mock.calls[2];
    expect(updateParams).toEqual([[1, 2], ['win', 'void'], [30, null], [-125, null], [24.5, null]]);
  });
});
