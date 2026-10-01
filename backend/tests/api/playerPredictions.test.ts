import { describe, it, expect, vi, beforeEach } from 'vitest';
import request from 'supertest';
import { pgResult } from '../helpers/mockDb.js';

const { app } = await import('../../src/app.js');
const { query } = await import('../../src/db.js');
const { clearUpcomingPredictionsCache, DEFAULT_UPCOMING_LIMIT, MAX_UPCOMING_LIMIT } = await import(
  '../../src/services/playerPredictions.js'
);
const { COMPLETE_RUN_STATUS, PRODUCTION_CHANNEL } = await import('../../src/services/slate.js');
const queryMock = vi.mocked(query);


function undefinedTable(relation: string): Error & { code: string } {
  const err = new Error(`relation "${relation}" does not exist`) as Error & { code: string };
  err.code = '42P01';
  return err;
}

const playerRow = { nba_id: '1629029', team: 'LAL' };

const runRow = {
  id: 1,
  model_version: 'bt20260115',
  feature_version: 'v3',
  predicted_at: new Date('2026-08-17T22:08:18.285Z'),
  forecast_cutoff_at: new Date('2026-01-15T00:00:00.000Z'),
  notes: 'horizon=gameday (T-6h); backtest smoke run: cutoff 2026-01-15',
};

const day = (y: number, m: number, d: number): Date => new Date(y, m - 1, d);

interface RowOverrides {
  nba_game_id?: string;
  game_date?: Date;
  home_team_abbr?: string | null;
  away_team_abbr?: string | null;
  game_status?: string | null;
}

function gameRows(
  stat: string,
  quantile: number | null,
  value: number,
  conditional: boolean,
  overrides: RowOverrides = {}
): Record<string, unknown> {
  return {
    nba_game_id: '0022500586',
    game_date: day(2026, 1, 15),
    home_team_abbr: 'LAL',
    away_team_abbr: 'CHA',
    game_status: 'Final',
    ...overrides,
    stat,
    quantile,
    value,
    conditional,
  };
}

const HOME_GAME = [
  gameRows('prob_active', null, 0.9146, false),
  gameRows('prob_active_model', null, 0.9146, false),
  gameRows('minutes', null, 36.33, true),
  gameRows('minutes', 0.1, 28.48, true),
  gameRows('minutes', 0.5, 36.17, true),
  gameRows('minutes', 0.9, 43.5, true),
  gameRows('minutes_uncond', null, 33.23, false),
  gameRows('pts', null, 32.34, true),
  gameRows('pts', 0.1, 25.75, true),
  gameRows('pts', 0.5, 31.74, true),
  gameRows('pts', 0.9, 39.94, true),
  gameRows('pts_uncond', null, 29.58, false),
  gameRows('ast', null, 9.1, true),
  gameRows('ast_uncond', null, 8.33, false),
];

const AWAY_GAME_OVERRIDES: RowOverrides = {
  nba_game_id: '0022500601',
  game_date: day(2026, 1, 17),
  home_team_abbr: 'POR',
  away_team_abbr: 'LAL',
  game_status: 'Final',
};

const AWAY_GAME = [
  gameRows('prob_active', null, 0.41, false, AWAY_GAME_OVERRIDES),
  gameRows('minutes', 0.5, 30.2, true, AWAY_GAME_OVERRIDES),
  gameRows('pts', null, 27.1, true, AWAY_GAME_OVERRIDES),
];

beforeEach(() => {
  queryMock.mockReset();
  clearUpcomingPredictionsCache();
});

describe('GET /api/players/:id/predictions', () => {
  it('returns run metadata and one entry per predicted game, earliest first', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([playerRow]))
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult([...AWAY_GAME, ...HOME_GAME]));

    const res = await request(app).get('/api/players/373/predictions');

    expect(res.status).toBe(200);
    expect(res.body.player_id).toBe(373);
    expect(res.body.nba_player_id).toBe('1629029');
    expect(res.body.run).toEqual({
      id: 1,
      model_version: 'bt20260115',
      feature_version: 'v3',
      predicted_at: '2026-08-17T22:08:18.285Z',
      forecast_cutoff_at: '2026-01-15T00:00:00.000Z',
      horizon: 'gameday (T-6h)',
    });

    expect(res.body.games.map((g: { game_date: string }) => g.game_date)).toEqual([
      '2026-01-15',
      '2026-01-17',
    ]);

    const [home, away] = res.body.games;
    expect(home.nba_game_id).toBe('0022500586');
    expect(home.opponent_abbr).toBe('CHA');
    expect(home.is_home).toBe(true);
    expect(home.game_status).toBe('Final');
    expect(home.prob_active).toBeCloseTo(0.9146, 4);
    expect(home.prob_active_model).toBeCloseTo(0.9146, 4);
    expect(home.stats.minutes).toEqual({
      expected: 36.33,
      p10: 28.48,
      p50: 36.17,
      p90: 43.5,
      unconditional: 33.23,
    });
    expect(home.stats.pts).toEqual({
      expected: 32.34,
      p10: 25.75,
      p50: 31.74,
      p90: 39.94,
      unconditional: 29.58,
    });

    expect(away.opponent_abbr).toBe('POR');
    expect(away.is_home).toBe(false);
  });

  it('serves a stat with no quantile rows as an expected value with null bands', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([playerRow]))
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(HOME_GAME));

    const res = await request(app).get('/api/players/373/predictions');

    expect(res.body.games[0].stats.ast).toEqual({
      expected: 9.1,
      p10: null,
      p50: null,
      p90: null,
      unconditional: 8.33,
    });
  });

  it('passes through stats the serving layer has never heard of', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([playerRow]))
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(
        pgResult([
          gameRows('pts', null, 30, true),
          gameRows('fga', null, 21.4, true),
          gameRows('dunks_per_36', null, 1.2, true),
        ])
      );

    const res = await request(app).get('/api/players/373/predictions');

    expect(res.body.stats).toEqual(['pts', 'fga', 'dunks_per_36']);
    expect(res.body.games[0].stats.dunks_per_36.expected).toBe(1.2);
  });

  it('answers 200 with a null run when no run has ever completed', async () => {
    queryMock.mockResolvedValueOnce(pgResult([playerRow])).mockResolvedValueOnce(pgResult([]));

    const res = await request(app).get('/api/players/373/predictions');

    expect(res.status).toBe(200);
    expect(res.body.run).toBeNull();
    expect(res.body.games).toEqual([]);
    expect(res.body.stats).toEqual([]);
    expect(queryMock).toHaveBeenCalledTimes(2);
  });

  it('answers 200 with the run and an empty list when the run has nothing for this player', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([playerRow]))
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult([]));

    const res = await request(app).get('/api/players/373/predictions');

    expect(res.status).toBe(200);
    expect(res.body.run.model_version).toBe('bt20260115');
    expect(res.body.games).toEqual([]);
  });

  it('answers 200 with an empty payload when migration 014 has not been applied', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([playerRow]))
      .mockRejectedValueOnce(undefinedTable('prediction_runs'));

    const res = await request(app).get('/api/players/373/predictions');

    expect(res.status).toBe(200);
    expect(res.body.run).toBeNull();
    expect(res.body.games).toEqual([]);
  });

  it('leaves opponent and home/away unknown when the player is on neither side', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([{ nba_id: '1629029', team: 'BOS' }]))
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(HOME_GAME));

    const res = await request(app).get('/api/players/373/predictions');

    expect(res.body.games[0].opponent_abbr).toBeNull();
    expect(res.body.games[0].is_home).toBeNull();
  });

  it('serves a game whose schedule row is missing rather than dropping it', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([playerRow]))
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(
        pgResult([
          gameRows('pts', null, 30, true, {
            home_team_abbr: null,
            away_team_abbr: null,
            game_status: null,
          }),
        ])
      );

    const res = await request(app).get('/api/players/373/predictions');

    expect(res.body.games).toHaveLength(1);
    expect(res.body.games[0].opponent_abbr).toBeNull();
    expect(res.body.games[0].game_status).toBeNull();
    expect(res.body.games[0].stats.pts.expected).toBe(30);
  });

  it('defaults to no date filter and a 14-game limit', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([playerRow]))
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(HOME_GAME));

    await request(app).get('/api/players/373/predictions');

    const params = queryMock.mock.calls[2][1] as unknown[];
    expect(params).toEqual([1, '1629029', null, DEFAULT_UPCOMING_LIMIT]);
  });

  it('binds ?from= and ?limit= into the query', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([playerRow]))
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(HOME_GAME));

    const res = await request(app).get('/api/players/373/predictions?from=2026-01-17&limit=3');

    expect(res.status).toBe(200);
    const params = queryMock.mock.calls[2][1] as unknown[];
    expect(params).toEqual([1, '1629029', '2026-01-17', 3]);
  });

  it('rejects a from that is not a real calendar day', async () => {
    const res = await request(app).get('/api/players/373/predictions?from=2026-02-31');

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/YYYY-MM-DD/);
    expect(queryMock).not.toHaveBeenCalled();
  });

  it('rejects a limit outside the served range instead of clamping it', async () => {
    const tooBig = await request(app).get(
      `/api/players/373/predictions?limit=${MAX_UPCOMING_LIMIT + 1}`
    );
    const zero = await request(app).get('/api/players/373/predictions?limit=0');
    const notANumber = await request(app).get('/api/players/373/predictions?limit=lots');

    for (const res of [tooBig, zero, notANumber]) {
      expect(res.status).toBe(400);
      expect(res.body.error).toMatch(/limit/);
    }
    expect(queryMock).not.toHaveBeenCalled();
  });

  it('rejects a non-numeric player id', async () => {
    const res = await request(app).get('/api/players/not-a-player/predictions');

    expect(res.status).toBe(400);
    expect(queryMock).not.toHaveBeenCalled();
  });

  it('404s an id no player row matches', async () => {
    queryMock.mockResolvedValueOnce(pgResult([]));

    const res = await request(app).get('/api/players/999999/predictions');

    expect(res.status).toBe(404);
  });

  it('answers 200 with an empty list for a player row that carries no nba_id', async () => {
    queryMock.mockResolvedValueOnce(pgResult([{ nba_id: null, team: 'LAL' }]));

    const res = await request(app).get('/api/players/373/predictions');

    expect(res.status).toBe(200);
    expect(res.body.nba_player_id).toBeNull();
    expect(res.body.games).toEqual([]);
    expect(queryMock).toHaveBeenCalledTimes(1);
  });

  it('500s when the players lookup itself fails', async () => {
    queryMock.mockRejectedValueOnce(new Error('connection terminated'));

    const res = await request(app).get('/api/players/373/predictions');

    expect(res.status).toBe(500);
  });
});

describe('GET /api/players/:id/predictions run channel', () => {
  it('reads the run from the production channel only', async () => {
    // arrange
    queryMock
      .mockResolvedValueOnce(pgResult([playerRow]))
      .mockResolvedValueOnce(pgResult([runRow]))
      .mockResolvedValueOnce(pgResult(HOME_GAME));

    // act
    const res = await request(app).get('/api/players/373/predictions');

    // assert
    expect(res.status).toBe(200);
    expect(queryMock.mock.calls[1][1]).toEqual([COMPLETE_RUN_STATUS, PRODUCTION_CHANNEL]);
  });
});
