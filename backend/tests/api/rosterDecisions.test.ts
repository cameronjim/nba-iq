import { describe, it, expect, vi, beforeEach } from 'vitest';
import request from 'supertest';
import { app } from '../../src/app.js';
import { query } from '../../src/db.js';
import { getRankedPlayers, type PlayerWithScore } from '../../src/services/fantasyScore.js';
import { bearerFor } from '../helpers/authToken.js';
import { pgResult } from '../helpers/mockDb.js';

const queryMock = vi.mocked(query);
const rankedMock = vi.mocked(getRankedPlayers);

const RUN_ROW = { id: 9, model_version: 'v-test', predicted_at: '2026-01-14T18:00:00.000Z' };
const LINE = { pts: 20, reb: 6, ast: 4, stl: 1, blk: 0.5, tov: 2, fg3m: 2, fgm: 7, fga: 15, ftm: 4, fta: 5 };

type Rows = Array<Record<string, unknown>>;

// dispatches on the sql text so each test only names the tables it cares about.
function answer(routes: Array<[RegExp, Rows]>): void {
  queryMock.mockImplementation(async (sql: string) => {
    for (const [pattern, rows] of routes) if (pattern.test(sql)) return pgResult(rows);
    return pgResult([]);
  });
}

function callsMatching(pattern: RegExp): unknown[][] {
  return queryMock.mock.calls.filter(([sql]) => pattern.test(String(sql))).map(([, params]) => params as unknown[]);
}

const ROSTER_SQL = /FROM my_roster mr/;
const RUN_SQL = /FROM prediction_runs\s+WHERE status/;
const WINDOW_SQL = /MAX\(p\.name\) AS name/;
const SCHEDULE_SQL = /FROM nba_schedule/;
const PLAYERS_BY_ID_SQL = /FROM players\s+WHERE id = ANY/;
const ROSTER_PREDICTIONS_SQL = /pgp\.stat = ANY\(\$6\)/;
const POOL_SQL = /GROUP BY pgp\.nba_player_id$/m;

function windowRow(nbaId: string, gameId: string, date: string, scale: number): Record<string, unknown> {
  const row: Record<string, unknown> = {
    game_date: date, nba_game_id: gameId, nba_player_id: nbaId, name: null, team_abbr: 'GSW', position: 'G',
    prob_active: 0.82, proj_min_p50: 25, c_pts: 20 * scale,
  };
  for (const [stat, value] of Object.entries(LINE)) {
    row[`u_${stat}`] = stat === 'fga' || stat === 'fta' ? value : value * scale;
  }
  return row;
}

function predictionRows(nbaId: string, gameId: string, gameDate: string, scale: number): Rows {
  const rows: Rows = [
    { nba_player_id: nbaId, nba_game_id: gameId, game_date: gameDate, stat: 'prob_active', quantile: null, value: 0.9, conditional: false },
  ];
  for (const [stat, base] of Object.entries(LINE)) {
    const value = base * scale;
    rows.push({ nba_player_id: nbaId, nba_game_id: gameId, game_date: gameDate, stat, quantile: null, value, conditional: true });
    rows.push({ nba_player_id: nbaId, nba_game_id: gameId, game_date: gameDate, stat, quantile: 0.1, value: value * 0.6, conditional: true });
    rows.push({ nba_player_id: nbaId, nba_game_id: gameId, game_date: gameDate, stat, quantile: 0.5, value, conditional: true });
    rows.push({ nba_player_id: nbaId, nba_game_id: gameId, game_date: gameDate, stat, quantile: 0.9, value: value * 1.4, conditional: true });
  }
  return rows;
}

function poolRow(id: string, scale: number): Record<string, unknown> {
  const row: Record<string, unknown> = { nba_player_id: id };
  for (const [stat, value] of Object.entries(LINE)) row[stat] = value * scale;
  return row;
}

function rankedPlayer(id: number, rank: number, ppg: number): PlayerWithScore {
  return {
    id, nba_id: String(1000 + id), name: `Free Agent ${id}`, team: 'BOS', position: 'F',
    points_per_game: ppg, rebounds_per_game: 4 + (id % 4), assists_per_game: 2 + (id % 3),
    steals_per_game: 0.6 + (id % 3) * 0.2, blocks_per_game: 0.3 + (id % 4) * 0.2,
    field_goal_percentage: 46, free_throw_percentage: 78, three_point_percentage: 36,
    three_pointers_made: 1 + (id % 3) * 0.5, turnovers_per_game: 1.2 + (id % 2) * 0.4,
    minutes_per_game: 28, games_played: 40, injury_status: null, injury_detail: null, headshot_url: null,
    fantasy_score: 30, fantasy_rank: rank,
  };
}

beforeEach(() => {
  queryMock.mockReset();
  rankedMock.mockReset();
  rankedMock.mockResolvedValue([]);
});

describe('GET /api/fantasy/start-sit', () => {
  it('requires authentication', async () => {
    // act
    const res = await request(app).get('/api/fantasy/start-sit');

    // assert
    expect(res.status).toBe(401);
    expect(queryMock).not.toHaveBeenCalled();
  });

  it.each([
    ['start=2026-02-31', /start/],
    ['start=2026-01-15&days=30', /days/],
    ['start=2026-01-15&slots=0', /slots/],
  ])('rejects %s', async (qs, message) => {
    // act
    const res = await request(app).get(`/api/fantasy/start-sit?${qs}`).set('Authorization', bearerFor(1));

    // assert
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(message);
  });

  it('returns an empty-roster shape bound to the caller, ignoring a client user id', async () => {
    // arrange
    answer([]);

    // act
    const res = await request(app)
      .get('/api/fantasy/start-sit?start=2026-01-15&user_id=99')
      .set('Authorization', bearerFor(7));

    // assert
    expect(res.status).toBe(200);
    expect(res.body).toMatchObject({ status: 'empty_roster', slots: 10, run: null });
    expect(res.body.days).toHaveLength(7);
    expect(callsMatching(ROSTER_SQL)).toEqual([[7]]);
  });

  it('ranks the roster per day and recommends who to sit', async () => {
    // arrange
    answer([
      [ROSTER_SQL, [
        { player_id: 1, nba_id: '201939', name: 'Stephen Curry', team: 'GSW' },
        { player_id: 2, nba_id: '1630228', name: 'Jonathan Kuminga', team: 'GSW' },
        { player_id: 3, nba_id: '203110', name: 'Draymond Green', team: 'GSW' },
      ]],
      [RUN_SQL, [RUN_ROW]],
      [WINDOW_SQL, [
        windowRow('201939', 'g1', '2026-01-15', 1.4),
        windowRow('1630228', 'g1', '2026-01-15', 0.9),
        windowRow('203110', 'g1', '2026-01-15', 0.7),
        windowRow('999', 'g1', '2026-01-15', 1),
      ]],
      [SCHEDULE_SQL, [{ nba_game_id: 'g1', season_type: 'Regular Season', home_team_abbr: 'GSW', away_team_abbr: 'LAL' }]],
    ]);

    // act
    const res = await request(app)
      .get('/api/fantasy/start-sit?start=2026-01-15&days=2&slots=2')
      .set('Authorization', bearerFor(5));

    // assert
    expect(res.status).toBe(200);
    expect(res.body.status).toBe('ok');
    expect(callsMatching(ROSTER_SQL)).toEqual([[5]]);
    const [first, second] = res.body.days;
    expect(first.players.map((p: { name: string }) => p.name)).toEqual([
      'Stephen Curry', 'Jonathan Kuminga', 'Draymond Green',
    ]);
    expect(first.players[0].sentence).toBe('Stephen Curry · vs LAL · 28 pts, 25 min if he plays · 82% to play');
    expect(first.recommendation).toBe('Start these 2; sit Draymond Green.');
    expect(second).toEqual({ date: '2026-01-16', players: [], recommendation: 'No one on your roster plays.' });
    expect(res.body.value_basis).toMatch(/slate impact/);
  });
});

describe('GET /api/fantasy/streamers', () => {
  it('requires authentication', async () => {
    // act
    const res = await request(app).get('/api/fantasy/streamers');

    // assert
    expect(res.status).toBe(401);
  });

  it('rejects a malformed start date', async () => {
    // act
    const res = await request(app).get('/api/fantasy/streamers?start=nope').set('Authorization', bearerFor(1));

    // assert
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/start/);
  });

  it('returns an empty-roster shape bound to the caller', async () => {
    // arrange
    answer([]);

    // act
    const res = await request(app).get('/api/fantasy/streamers?start=2026-01-15').set('Authorization', bearerFor(4));

    // assert
    expect(res.status).toBe(200);
    expect(res.body).toMatchObject({ status: 'empty_roster', streamers: [] });
    expect(callsMatching(ROSTER_SQL)).toEqual([[4]]);
  });

  it('ranks free agents outside the roster with games, score, drivers and a sentence', async () => {
    // arrange
    const rostered = Array.from({ length: 130 }, (_, i) => rankedPlayer(i + 1, i + 1, 24 - i * 0.05));
    const free = Array.from({ length: 10 }, (_, i) => rankedPlayer(200 + i, 131 + i, 12 - i * 0.3));
    rankedMock.mockResolvedValue([...rostered, ...free]);
    answer([
      [ROSTER_SQL, [{ ...rankedPlayer(1, 1, 24), name: 'My Star' }]],
      [SCHEDULE_SQL, [{ nba_game_id: 'g1', season_type: 'Regular Season', home_team_abbr: 'BOS', away_team_abbr: 'NYK' }]],
    ]);

    // act
    const res = await request(app)
      .get('/api/fantasy/streamers?start=2026-01-15&days=7')
      .set('Authorization', bearerFor(4));

    // assert
    expect(res.status).toBe(200);
    expect(res.body.status).toBe('ok');
    expect(res.body.streamers).toHaveLength(5);
    for (const s of res.body.streamers) {
      expect(s.id).toBeGreaterThanOrEqual(200);
      expect(s.games).toBe(1);
      expect(typeof s.score).toBe('number');
      expect(s.drivers[0]).toEqual(expect.objectContaining({ label: expect.any(String) }));
      expect(s.sentence).toMatch(new RegExp(`^Pick up ${s.name}: 1 game this week, about [+-]\\d`));
    }
  });
});

describe('POST /api/fantasy/trade-check', () => {
  const ROSTER = [
    { player_id: 3, nba_id: '101', name: 'Ann Guard' },
    { player_id: 4, nba_id: '202', name: 'Bo Center' },
  ];

  it('requires authentication', async () => {
    // act
    const res = await request(app).post('/api/fantasy/trade-check').send({ give: [3], get: [9] });

    // assert
    expect(res.status).toBe(401);
    expect(queryMock).not.toHaveBeenCalled();
  });

  it.each([
    [{ give: [3] }, /get must be a non-empty list/],
    [{ give: ['x'], get: [9] }, /positive whole-number/],
    [{ give: [3], get: [3] }, /both sides/],
    [{ give: [3], get: [9], start: '2026-13-01' }, /start/],
  ])('rejects the body %j before touching the database', async (body, message) => {
    // act
    const res = await request(app).post('/api/fantasy/trade-check').set('Authorization', bearerFor(1)).send(body);

    // assert
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(message);
    expect(queryMock).not.toHaveBeenCalled();
  });

  it('rejects giving away a player who is not on the caller roster', async () => {
    // arrange
    answer([[ROSTER_SQL, ROSTER]]);

    // act
    const res = await request(app)
      .post('/api/fantasy/trade-check')
      .set('Authorization', bearerFor(8))
      .send({ give: [77], get: [9] });

    // assert
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/give must only list players on your roster/);
    expect(callsMatching(ROSTER_SQL)).toEqual([[8]]);
  });

  it('rejects getting a player already on the roster or one that does not exist', async () => {
    // arrange
    answer([[ROSTER_SQL, ROSTER], [PLAYERS_BY_ID_SQL, []]]);

    // act
    const onRoster = await request(app)
      .post('/api/fantasy/trade-check').set('Authorization', bearerFor(8)).send({ give: [3], get: [4] });
    const unknown = await request(app)
      .post('/api/fantasy/trade-check').set('Authorization', bearerFor(8)).send({ give: [3], get: [12345] });

    // assert
    expect(onRoster.status).toBe(400);
    expect(onRoster.body.error).toMatch(/not on your roster/);
    expect(unknown.status).toBe(400);
    expect(unknown.body.error).toMatch(/does not exist/);
  });

  it('simulates before and after with a fixed seed and returns deltas and a verdict', async () => {
    // arrange
    answer([
      [ROSTER_SQL, ROSTER],
      [PLAYERS_BY_ID_SQL, [{ id: 9, nba_id: '909', name: 'Cy Forward' }]],
      [RUN_SQL, [RUN_ROW]],
      [ROSTER_PREDICTIONS_SQL, [
        ...predictionRows('101', 'g1', '2026-01-15', 0.6),
        ...predictionRows('202', 'g2', '2026-01-16', 1),
        ...predictionRows('909', 'g3', '2026-01-15', 1.5),
        ...predictionRows('909', 'g4', '2026-01-17', 1.5),
      ]],
      [POOL_SQL, [poolRow('101', 0.6), poolRow('202', 1), poolRow('909', 3), poolRow('303', 1.2)]],
    ]);
    const send = (): request.Test =>
      request(app).post('/api/fantasy/trade-check').set('Authorization', bearerFor(8))
        .send({ give: [3], get: [9], start: '2026-01-15' });

    // act
    const res = await send();
    const again = await send();

    // assert
    expect(res.status).toBe(200);
    expect(res.body).toMatchObject({
      status: 'ok',
      give: [{ id: 3, name: 'Ann Guard', games: 1 }],
      get: [{ id: 9, name: 'Cy Forward', games: 2 }],
      window: { from: '2026-01-15', to: '2026-01-21' },
    });
    expect(res.body.categories).toHaveLength(9);
    const sum = res.body.categories.reduce((s: number, c: { delta: number }) => s + c.delta, 0);
    expect(sum).toBeCloseTo(res.body.delta_expected_wins, 1);
    expect(res.body.delta_expected_wins).toBeGreaterThan(0);
    expect(res.body.verdict).toMatch(/^This trade helps: \+\d/);
    expect(again.body).toEqual(res.body);
    expect(callsMatching(ROSTER_SQL)).toEqual([[8], [8]]);
  });
});
