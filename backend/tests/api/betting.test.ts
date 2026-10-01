import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import request from 'supertest';
import { pgResult } from '../helpers/mockDb.js';
import { bearerFor } from '../helpers/authToken.js';

vi.mock('../../src/services/ai.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/services/ai.js')>();
  return {
    ...actual,
    callClaude: vi.fn(),
    buildBettingContext: vi.fn().mockResolvedValue('CONTEXT'),
  };
});

const { app } = await import('../../src/app.js');
const { query } = await import('../../src/db.js');
const { callClaude } = await import('../../src/services/ai.js');
const queryMock = vi.mocked(query);
const claudeMock = vi.mocked(callClaude);

beforeEach(() => {
  queryMock.mockReset();
  claudeMock.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const espnEvent = {
  id: '401859966',
  date: '2026-06-11T00:30Z',
  status: { type: { name: 'STATUS_SCHEDULED', detail: 'Wed at 8:30 PM EDT', shortDetail: '6/10 - 8:30 PM EDT' } },
  competitions: [
    {
      competitors: [
        { homeAway: 'home', team: { displayName: 'New York Knicks', abbreviation: 'NY' } },
        { homeAway: 'away', team: { displayName: 'San Antonio Spurs', abbreviation: 'SA' } },
      ],
      odds: [
        {
          provider: { name: 'Draft Kings' },
          details: 'NY -2.5',
          overUnder: 216.5,
          spread: -2.5,
          moneyline: { home: { close: { odds: '-130' } }, away: { close: { odds: '+105' } } },
          pointSpread: {
            home: { close: { line: '-2.5', odds: '-105' } },
            away: { close: { line: '+2.5', odds: '-115' } },
          },
          total: {
            over: { close: { line: 'o216.5', odds: '-112' } },
            under: { close: { line: 'u216.5', odds: '-108' } },
          },
        },
      ],
    },
  ],
};

describe('GET /api/betting/odds', () => {
  it('returns 502 when ESPN responds with a non-OK status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 } as Response));

    const res = await request(app).get('/api/betting/odds');

    expect(res.status).toBe(502);
    expect(res.body.error).toBe('ESPN API unavailable');
  });

  it('returns 504 when the ESPN request aborts', async () => {
    const abortError = new Error('aborted');
    abortError.name = 'AbortError';
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(abortError));

    const res = await request(app).get('/api/betting/odds');

    expect(res.status).toBe(504);
    expect(res.body.error).toBe('ESPN timed out');
  });

  it('reports no_games on /picks when the window has no scheduled games', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ events: [] }),
    } as Response));

    const res = await request(app)
      .get('/api/betting/picks')
      .set('Authorization', bearerFor(5));

    expect(res.status).toBe(200);
    expect(res.body.no_games).toBe(true);
    expect(res.body.picks).toEqual([]);
    expect(queryMock).not.toHaveBeenCalled();
    expect(claudeMock).not.toHaveBeenCalled();
  });

  it('parses scheduled games with per-side prices and implied probabilities', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ events: [espnEvent] }),
    } as Response));

    const res = await request(app).get('/api/betting/odds');

    expect(res.status).toBe(200);
    expect(res.body.games).toHaveLength(1);
    const game = res.body.games[0];
    expect(game.espn_event_id).toBe('401859966');
    expect(game.markets.spread.home_line).toBe(-2.5);
    expect(game.markets.spread.home_price).toBe(-105);
    expect(game.markets.total.line).toBe(216.5);
    expect(game.markets.moneyline.home).toBe(-130);
    expect(game.markets.moneyline.home_implied).toBeCloseTo(0.5652, 3);
  });
});

describe('GET /api/betting/picks', () => {
  it('rejects requests without a token', async () => {
    const res = await request(app).get('/api/betting/picks');

    expect(res.status).toBe(401);
  });

  it('serves cached picks without calling the model', async () => {
    const cachedPicks = {
      picks: [
        {
          game_id: '401859966', category: 'best_value', market: 'spread', selection: 'home',
          matchup: 'San Antonio Spurs @ New York Knicks', game_date: '2026-06-10',
          tipoff: '6/10 - 8:30 PM EDT', selection_label: 'New York Knicks -2.5',
          line: -2.5, american_odds: -105, implied_prob: 0.5122,
          estimated_win_prob: 0.58, edge: 0.0678, rationale: 'rest advantage', confidence: 'medium',
        },
      ],
      parlay: null,
      summary: 'One strong play tonight.',
    };
    queryMock
      .mockResolvedValueOnce(pgResult([{ ai_preferences: {} }]))
      .mockResolvedValueOnce(pgResult([{ picks: cachedPicks, created_at: '2026-06-09T12:00:00Z' }]));

    const res = await request(app)
      .get('/api/betting/picks')
      .set('Authorization', bearerFor(5));

    expect(res.status).toBe(200);
    expect(res.body.cached).toBe(true);
    expect(res.body.picks).toHaveLength(1);
    expect(claudeMock).not.toHaveBeenCalled();
  });

  it('serves the previous picks with stale:true when lines moved, without calling the model', async () => {
    const oldPicks = { picks: [{ game_id: '401859966', category: 'safe' }], parlay: null, summary: 'old slate' };
    queryMock
      .mockResolvedValueOnce(pgResult([{ ai_preferences: {} }]))
      .mockResolvedValueOnce(pgResult([]))
      .mockResolvedValueOnce(pgResult([{ picks: oldPicks, created_at: '2026-06-09T12:00:00Z' }]));

    const res = await request(app)
      .get('/api/betting/picks')
      .set('Authorization', bearerFor(5));

    expect(res.status).toBe(200);
    expect(res.body.stale).toBe(true);
    expect(res.body.cached_at).toBe('2026-06-09T12:00:00Z');
    expect(res.body.summary).toBe('old slate');
    expect(claudeMock).not.toHaveBeenCalled();
  });

  it('generates fresh picks, re-attaches snapshot odds, and caps each category at 2', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([{ ai_preferences: {} }]))
      .mockResolvedValueOnce(pgResult([]));
    const pick = (market: string, selection: string, category: string) => ({
      game_id: '401859966', category, market, selection,
      estimated_win_prob: 0.58, rationale: 'edge', confidence: 'medium',
    });
    claudeMock.mockResolvedValue(JSON.stringify({
      picks: [
        pick('spread', 'home', 'best_value'),
        pick('total', 'over', 'best_value'),
        pick('moneyline', 'home', 'best_value'),
        pick('moneyline', 'away', 'safe'),
        { game_id: '999999', category: 'hail_mary', market: 'moneyline', selection: 'away', estimated_win_prob: 0.4, rationale: 'ghost', confidence: 'low' },
      ],
      parlay: { legs: [{ game_id: '401859966', market: 'spread', selection: 'home' }], rationale: 'one leg only' },
      summary: 'Take the home side.',
    }));

    const res = await request(app)
      .get('/api/betting/picks')
      .query({ refresh: 'true' })
      .set('Authorization', bearerFor(5));

    expect(res.status).toBe(200);
    const categories = res.body.picks.map((p: { category: string }) => p.category);
    expect(categories.filter((c: string) => c === 'best_value')).toHaveLength(2);
    expect(categories.filter((c: string) => c === 'safe')).toHaveLength(1);
    expect(categories.filter((c: string) => c === 'hail_mary')).toHaveLength(0);
    const spreadPick = res.body.picks[0];
    expect(spreadPick.american_odds).toBe(-105);
    expect(spreadPick.line).toBe(-2.5);
    expect(spreadPick.implied_prob).toBeCloseTo(0.5122, 3);
    expect(spreadPick.estimate_source).toBe('claude');
    expect(spreadPick.selection_label).toBe('New York Knicks -2.5');
    expect(res.body.parlay).toBeNull();
    const upsert = queryMock.mock.calls.find(([sql]) =>
      (sql as string).includes('INSERT INTO betting_cache')
    );
    expect(upsert).toBeDefined();
  });

  it('computes edge against the no-vig probability when both sides are priced', async () => {
    // arrange
    queryMock
      .mockResolvedValueOnce(pgResult([{ ai_preferences: {} }]))
      .mockResolvedValueOnce(pgResult([]));
    claudeMock.mockResolvedValue(JSON.stringify({
      picks: [{
        game_id: '401859966', category: 'best_value', market: 'spread', selection: 'home',
        estimated_win_prob: 0.58, rationale: 'edge', confidence: 'medium',
      }],
      parlay: null,
      summary: 'ok',
    }));
    const noVigHome = 0.51220 / (0.51220 + 0.53488);

    // act
    const res = await request(app)
      .get('/api/betting/picks')
      .query({ refresh: 'true' })
      .set('Authorization', bearerFor(5));

    // assert
    const pick = res.body.picks[0];
    expect(pick.implied_prob_novig).toBeCloseTo(noVigHome, 3);
    expect(pick.edge).toBeCloseTo(0.58 - noVigHome, 3);
    expect(pick.edge).toBeGreaterThan(0.58 - pick.implied_prob);
  });

  it('drops a pick whose selected price is missing', async () => {
    // arrange
    queryMock
      .mockResolvedValueOnce(pgResult([{ ai_preferences: {} }]))
      .mockResolvedValueOnce(pgResult([]));
    // odds cache is module-level; jump past its ttl so the stub is fetched
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(Date.now() + 11 * 60_000);
    const unpriced = structuredClone(espnEvent);
    unpriced.competitions[0].odds[0].pointSpread = undefined as never;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ events: [unpriced] }),
    } as Response));
    claudeMock.mockResolvedValue(JSON.stringify({
      picks: [
        { game_id: '401859966', category: 'best_value', market: 'spread', selection: 'home', estimated_win_prob: 0.58, rationale: 'x', confidence: 'low' },
        { game_id: '401859966', category: 'safe', market: 'moneyline', selection: 'home', estimated_win_prob: 0.6, rationale: 'x', confidence: 'low' },
      ],
      parlay: null,
      summary: 'ok',
    }));

    // act
    const res = await request(app)
      .get('/api/betting/picks')
      .query({ refresh: 'true' })
      .set('Authorization', bearerFor(5));

    // assert
    vi.useRealTimers();
    expect(res.body.picks).toHaveLength(1);
    expect(res.body.picks[0].market).toBe('moneyline');
  });

  it('surfaces an empty result without caching when every pick fails validation', async () => {
    queryMock.mockResolvedValueOnce(pgResult([{ ai_preferences: {} }]));
    claudeMock.mockResolvedValue(JSON.stringify({
      picks: [{ game_id: 'nope', category: 'safe', market: 'moneyline', selection: 'home', estimated_win_prob: 0.6, rationale: '', confidence: 'low' }],
      parlay: null,
      summary: 'nothing real',
    }));

    const res = await request(app)
      .get('/api/betting/picks')
      .query({ refresh: 'true' })
      .set('Authorization', bearerFor(5));

    expect(res.status).toBe(200);
    expect(res.body._empty).toBe(true);
    const upsert = queryMock.mock.calls.find(([sql]) =>
      (sql as string).includes('INSERT INTO betting_cache')
    );
    expect(upsert).toBeUndefined();
  });
});

describe('GET /api/betting/bets', () => {
  it('auto-settles pending straight bets whose games went final', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([
        { id: 42, market: 'spread', selection: 'home', line: -2.5, home_score: 110, away_score: 100 },
      ]))
      .mockResolvedValueOnce(pgResult([]))
      .mockResolvedValueOnce(pgResult([
        {
          id: 42, market: 'spread', nba_game_id: '401859966', home_team: 'New York Knicks',
          away_team: 'San Antonio Spurs', game_date: '2026-06-10',
          selection: 'home', line: -2.5, american_odds: -105, description: null,
          status: 'won', created_at: '2026-06-09T12:00:00Z', settled_at: '2026-06-11T04:00:00Z',
        },
        {
          id: 43, market: 'custom', nba_game_id: null, home_team: null,
          away_team: null, game_date: null,
          selection: null, line: null, american_odds: 600, description: 'Haliburton triple double',
          status: 'pending', created_at: '2026-06-09T13:00:00Z', settled_at: null,
        },
      ]));

    const res = await request(app)
      .get('/api/betting/bets')
      .set('Authorization', bearerFor(9));

    const [settleSql] = queryMock.mock.calls[0];
    expect(settleSql).toMatch(/b\.market IN \('spread', 'total', 'moneyline'\)/);
    const [updateSql, updateParams] = queryMock.mock.calls[1];
    expect(updateSql).toMatch(/UPDATE bets SET status/);
    expect(updateParams).toEqual(['won', 42, 9]);

    expect(res.status).toBe(200);
    expect(res.body.bets).toHaveLength(2);
    expect(res.body.summary).toEqual({ wins: 1, losses: 0, pushes: 0, pending: 1, net: 0 });
  });

  it('computes per-bet and total net when stakes were recorded', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([]))
      .mockResolvedValueOnce(pgResult([
        {
          id: 1, market: 'spread', nba_game_id: '401', home_team: 'New York Knicks',
          away_team: 'San Antonio Spurs', game_date: '2026-06-10', selection: 'home',
          line: -2.5, american_odds: -105, description: null, stake: 50, wager_type: 'cash',
          status: 'won', created_at: '2026-06-09T12:00:00Z', settled_at: '2026-06-11T04:00:00Z',
        },
        {
          id: 2, market: 'prop', nba_game_id: '401', home_team: 'New York Knicks',
          away_team: 'San Antonio Spurs', game_date: '2026-06-10', selection: null,
          line: null, american_odds: -115, description: 'Brunson over 28.5', stake: 25, wager_type: 'bonus_bet',
          status: 'lost', created_at: '2026-06-09T13:00:00Z', settled_at: '2026-06-11T04:00:00Z',
        },
        {
          id: 3, market: 'custom', nba_game_id: null, home_team: null,
          away_team: null, game_date: null, selection: null,
          line: null, american_odds: 600, description: 'First basket', stake: 20, wager_type: 'cash',
          status: 'lost', created_at: '2026-06-09T14:00:00Z', settled_at: '2026-06-11T04:00:00Z',
        },
      ]));

    const res = await request(app)
      .get('/api/betting/bets')
      .set('Authorization', bearerFor(9));

    expect(res.status).toBe(200);
    expect(res.body.bets[0].net).toBeCloseTo(47.62, 2);
    expect(res.body.bets[1].net).toBe(0);
    expect(res.body.bets[2].net).toBe(-20);
    expect(res.body.summary.net).toBeCloseTo(27.62, 2);
    expect(res.body.bets[0].to_win).toBeCloseTo(47.62, 2);
    expect(res.body.bets[1].to_win).toBeCloseTo(21.74, 2);
  });

  it('binds every query to the jwt user id', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([]))
      .mockResolvedValueOnce(pgResult([]));

    await request(app).get('/api/betting/bets').set('Authorization', bearerFor(77));

    for (const [, params] of queryMock.mock.calls) {
      expect(params).toContain(77);
    }
  });
});

describe('POST /api/betting/bets', () => {
  const straightBet = {
    nba_game_id: '888777',
    market: 'spread',
    selection: 'home',
    line: -4.5,
    american_odds: -110,
    stake: 25,
  };

  it('creates a straight bet, resolving game details from the db when not in the snapshot', async () => {
    queryMock
      .mockResolvedValueOnce(pgResult([
        { home_team: 'Boston Celtics', away_team: 'Miami Heat', game_date: '2026-06-12' },
      ]))
      .mockResolvedValueOnce(pgResult([
        { id: 1, ...straightBet, home_team: 'Boston Celtics', away_team: 'Miami Heat', game_date: '2026-06-12', description: null, status: 'pending', created_at: '2026-06-09T12:00:00Z', settled_at: null },
      ]));

    const res = await request(app)
      .post('/api/betting/bets')
      .set('Authorization', bearerFor(9))
      .send(straightBet);

    expect(res.status).toBe(201);
    expect(res.body.home_team).toBe('Boston Celtics');
    const insertCall = queryMock.mock.calls.find(([sql]) => (sql as string).includes('INSERT INTO bets'));
    expect(insertCall![1]![0]).toBe(9); // user_id from jwt
  });

  it('creates a custom bet from a description with no game reference', async () => {
    queryMock.mockResolvedValueOnce(pgResult([
      {
        id: 2, market: 'custom', nba_game_id: null, home_team: null, away_team: null,
        game_date: null, selection: null, line: null, american_odds: 600,
        description: 'SGA 40+ points and OKC wins', status: 'pending',
        created_at: '2026-06-09T12:00:00Z', settled_at: null,
      },
    ]));

    const res = await request(app)
      .post('/api/betting/bets')
      .set('Authorization', bearerFor(9))
      .send({ market: 'custom', description: 'SGA 40+ points and OKC wins', american_odds: 600, stake: 10 });

    expect(res.status).toBe(201);
    expect(res.body.description).toBe('SGA 40+ points and OKC wins');
    const insertCall = queryMock.mock.calls.find(([sql]) => (sql as string).includes('INSERT INTO bets'));
    const params = insertCall![1] as unknown[];
    expect(params[2]).toBeNull(); // nba_game_id
    expect(params[6]).toBeNull(); // selection
  });

  it('persists the stake and wager type', async () => {
    queryMock.mockResolvedValueOnce(pgResult([
      {
        id: 4, market: 'custom', nba_game_id: null, home_team: null, away_team: null,
        game_date: null, selection: null, line: null, american_odds: 600,
        description: 'First basket: Wembanyama', stake: 10, wager_type: 'bonus_bet',
        status: 'pending', created_at: '2026-06-09T12:00:00Z', settled_at: null,
      },
    ]));

    const res = await request(app)
      .post('/api/betting/bets')
      .set('Authorization', bearerFor(9))
      .send({ market: 'custom', description: 'First basket: Wembanyama', american_odds: 600, stake: 10, wager_type: 'bonus_bet' });

    expect(res.status).toBe(201);
    const insertCall = queryMock.mock.calls.find(([sql]) => (sql as string).includes('INSERT INTO bets'));
    const params = insertCall![1] as unknown[];
    expect(params[10]).toBe(10);          // stake
    expect(params[11]).toBe('bonus_bet'); // wager_type
  });

  it('rejects a junk wager type, a non-positive stake, and a missing stake', async () => {
    const badWager = await request(app)
      .post('/api/betting/bets')
      .set('Authorization', bearerFor(9))
      .send({ market: 'custom', description: 'whatever', american_odds: -110, stake: 5, wager_type: 'lottery' });
    const badStake = await request(app)
      .post('/api/betting/bets')
      .set('Authorization', bearerFor(9))
      .send({ market: 'custom', description: 'whatever', american_odds: -110, stake: 0 });
    const noStake = await request(app)
      .post('/api/betting/bets')
      .set('Authorization', bearerFor(9))
      .send({ market: 'custom', description: 'whatever', american_odds: -110 });

    expect(badWager.status).toBe(400);
    expect(badWager.body.error).toBe('wager_type must be cash, bonus_bet, or odds_boost');
    expect(badStake.status).toBe(400);
    expect(badStake.body.error).toBe('stake must be between 0 and 100000');
    expect(noStake.status).toBe(400);
    expect(noStake.body.error).toBe('stake must be between 0 and 100000');
  });

  it('creates a parlay bet with its combined odds', async () => {
    queryMock.mockResolvedValueOnce(pgResult([
      {
        id: 3, market: 'parlay', nba_game_id: null, home_team: null, away_team: null,
        game_date: null, selection: null, line: null, american_odds: 264,
        description: 'Knicks ML + Under 216.5 + Celtics -3', stake: 5, wager_type: 'cash',
        status: 'pending', created_at: '2026-06-09T12:00:00Z', settled_at: null,
      },
    ]));

    const res = await request(app)
      .post('/api/betting/bets')
      .set('Authorization', bearerFor(9))
      .send({ market: 'parlay', description: 'Knicks ML + Under 216.5 + Celtics -3', stake: 5, american_odds: 264 });

    expect(res.status).toBe(201);
    expect(res.body.to_win).toBeCloseTo(13.2, 2);
  });

  it.each([
    [{ ...straightBet, market: 'lottery' }, 'Invalid market'],
    [{ ...straightBet, market: 'total', selection: 'home' }, 'Invalid selection for this market'],
    [{ ...straightBet, market: 'moneyline', line: -4.5 }, 'Moneyline bets have no line'],
    [{ ...straightBet, line: undefined }, 'line is required for spread and total bets'],
    [{ ...straightBet, line: 99 }, 'Spread line out of range'],
    [{ ...straightBet, market: 'total', selection: 'over', line: 500 }, 'Total line out of range'],
    [{ ...straightBet, american_odds: -50 }, 'american_odds must be an integer like -110 or +150'],
    [{ ...straightBet, american_odds: undefined }, 'american_odds must be an integer like -110 or +150'],
    [{ market: 'parlay', description: 'three legs of chaos', stake: 5 }, 'american_odds must be an integer like -110 or +150'],
    [{ market: 'prop', description: 'x', stake: 10, american_odds: -110 }, 'description is required (3-300 characters)'],
    [{ market: 'custom', description: 'valid words here', stake: 10, american_odds: -110, selection: 'home' }, 'selection and line only apply to spread/total/moneyline bets'],
  ])('rejects invalid payload %#', async (payload, message) => {
    const res = await request(app)
      .post('/api/betting/bets')
      .set('Authorization', bearerFor(9))
      .send(payload);

    expect(res.status).toBe(400);
    expect(res.body.error).toBe(message);
  });

  it('returns 400 for a straight bet on a game neither ESPN nor the db knows', async () => {
    queryMock.mockResolvedValueOnce(pgResult([]));

    const res = await request(app)
      .post('/api/betting/bets')
      .set('Authorization', bearerFor(9))
      .send(straightBet);

    expect(res.status).toBe(400);
    expect(res.body.error).toBe('Unknown game');
  });
});

describe('PATCH /api/betting/bets/:id', () => {
  it('settles a bet manually and stamps settled_at', async () => {
    queryMock.mockResolvedValueOnce(pgResult([
      {
        id: 5, market: 'custom', nba_game_id: null, home_team: null, away_team: null,
        game_date: null, selection: null, line: null, american_odds: 600,
        description: 'weird exotic', status: 'won',
        created_at: '2026-06-09T12:00:00Z', settled_at: '2026-06-10T03:00:00Z',
      },
    ]));

    const res = await request(app)
      .patch('/api/betting/bets/5')
      .set('Authorization', bearerFor(9))
      .send({ status: 'won' });

    expect(res.status).toBe(200);
    expect(res.body.status).toBe('won');
    const [sql, params] = queryMock.mock.calls[0];
    expect(sql).toMatch(/UPDATE bets/);
    expect((params as unknown[])[0]).toBe('won');
    expect((params as unknown[])[1]).toBeInstanceOf(Date);
    expect((params as unknown[])[2]).toBe(5);
    expect((params as unknown[])[3]).toBe(9);
  });

  it('clears settled_at when a settle is undone back to pending', async () => {
    queryMock.mockResolvedValueOnce(pgResult([
      {
        id: 5, market: 'custom', nba_game_id: null, home_team: null, away_team: null,
        game_date: null, selection: null, line: null, american_odds: 600,
        description: 'weird exotic', stake: 10, wager_type: 'cash', status: 'pending',
        created_at: '2026-06-09T12:00:00Z', settled_at: null,
      },
    ]));

    const res = await request(app)
      .patch('/api/betting/bets/5')
      .set('Authorization', bearerFor(9))
      .send({ status: 'pending' });

    expect(res.status).toBe(200);
    const [, params] = queryMock.mock.calls[0];
    expect((params as unknown[])[1]).toBeNull();
  });

  it('rejects an invalid status', async () => {
    const res = await request(app)
      .patch('/api/betting/bets/5')
      .set('Authorization', bearerFor(9))
      .send({ status: 'maybe' });

    expect(res.status).toBe(400);
  });

  it("404s when the bet doesn't exist or belongs to someone else", async () => {
    queryMock.mockResolvedValueOnce(pgResult([]));

    const res = await request(app)
      .patch('/api/betting/bets/5')
      .set('Authorization', bearerFor(9))
      .send({ status: 'lost' });

    expect(res.status).toBe(404);
  });
});

describe('DELETE /api/betting/bets/:id', () => {
  it('deletes an owned bet', async () => {
    queryMock.mockResolvedValueOnce(pgResult([{ id: 5 }]));

    const res = await request(app)
      .delete('/api/betting/bets/5')
      .set('Authorization', bearerFor(9));

    expect(res.status).toBe(204);
    const [, params] = queryMock.mock.calls[0];
    expect(params).toEqual([5, 9]);
  });

  it("404s when the bet doesn't exist or belongs to someone else", async () => {
    queryMock.mockResolvedValueOnce(pgResult([]));

    const res = await request(app)
      .delete('/api/betting/bets/5')
      .set('Authorization', bearerFor(9));

    expect(res.status).toBe(404);
  });
});
