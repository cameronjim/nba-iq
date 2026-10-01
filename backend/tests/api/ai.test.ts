import { describe, it, expect, vi, beforeEach } from 'vitest';
import request from 'supertest';
import { pgResult } from '../helpers/mockDb.js';
import { bearerFor } from '../helpers/authToken.js';

const narrate = vi.fn();

vi.mock('../../src/services/aiProvider.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/services/aiProvider.js')>();
  return {
    ...actual,
    activeProviderKind: vi.fn(() => 'anthropic'),
    getNarrator: vi.fn(() => ({ narrate })),
  };
});

const { app } = await import('../../src/app.js');
const { query } = await import('../../src/db.js');
const { getRankedPlayers } = await import('../../src/services/fantasyScore.js');

const queryMock = vi.mocked(query);
const getRankedPlayersMock = vi.mocked(getRankedPlayers);
type PlayerWithScore = Awaited<ReturnType<typeof getRankedPlayers>>[number];

beforeEach(() => {
  queryMock.mockReset();
  narrate.mockReset();
  getRankedPlayersMock.mockResolvedValue([]);
});

describe('POST /api/ai/chat input validation', () => {
  it('rejects an unauthenticated request with 401', async () => {
    const res = await request(app).post('/api/ai/chat').send({ message: 'hi' });

    expect(res.status).toBe(401);
  });

  it('returns 400 when message is missing', async () => {
    const res = await request(app)
      .post('/api/ai/chat')
      .set('Authorization', bearerFor(1))
      .send({});

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/message/i);
  });

  it('returns 400 when message is not a string', async () => {
    const res = await request(app)
      .post('/api/ai/chat')
      .set('Authorization', bearerFor(1))
      .send({ message: 42 });

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/message/i);
  });

  it('returns 400 when message exceeds the length cap', async () => {
    const res = await request(app)
      .post('/api/ai/chat')
      .set('Authorization', bearerFor(1))
      .send({ message: 'x'.repeat(5000) });

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/4000 characters/i);
  });
});

function candidate(id: number, overrides: Partial<PlayerWithScore> = {}): PlayerWithScore {
  return {
    id,
    nba_id: String(5000 + id),
    name: `Candidate ${id}`,
    team: 'BOS',
    position: 'C',
    points_per_game: 10,
    rebounds_per_game: 5,
    assists_per_game: 2,
    steals_per_game: 0.7,
    blocks_per_game: 0.5,
    field_goal_percentage: 48,
    free_throw_percentage: 75,
    three_point_percentage: 34,
    three_pointers_made: 1,
    turnovers_per_game: 1.3,
    minutes_per_game: 25,
    games_played: 40,
    injury_status: null,
    injury_detail: null,
    headshot_url: null,
    fantasy_score: 20,
    fantasy_rank: id,
    ...overrides,
  };
}

const ROSTER_ROW = {
  id: 1, nba_id: '2544', name: 'Roster Guard', team: 'LAL', position: 'PG',
  points_per_game: 22, rebounds_per_game: 4, assists_per_game: 8, steals_per_game: 1.5,
  blocks_per_game: 0.1, field_goal_percentage: 46, free_throw_percentage: 85,
  three_pointers_made: 2.5, turnovers_per_game: 2.8, injury_status: null,
};

function routeWaiverQueries(): void {
  queryMock.mockImplementation(async (sql: string) => {
    if (sql.includes('COUNT(*)::int AS n FROM my_roster')) return pgResult([{ n: 1 }]);
    if (sql.includes('SELECT player_id FROM my_roster')) return pgResult([{ player_id: 1 }]);
    if (sql.includes('SELECT ai_preferences FROM users')) return pgResult([{ ai_preferences: {} }]);
    if (sql.includes('FROM my_roster mr')) return pgResult([ROSTER_ROW]);
    return pgResult([]);
  });
}

describe('GET /api/ai/waiver-suggestions prompt', () => {
  it('sends Claude a pre-ranked candidate list and asks it to sanity-check rather than re-rank', async () => {
    // arrange
    routeWaiverQueries();
    getRankedPlayersMock.mockResolvedValue([
      candidate(5, { name: 'Tier Star', points_per_game: 25 }),
      candidate(140, { name: 'Bench Big', blocks_per_game: 2.8 }),
      candidate(141, { name: 'Bench Wing' }),
    ]);
    narrate.mockResolvedValue({
      text: JSON.stringify({
        trade_targets: [{ name: 'Tier Star', reasoning: 'helps points' }],
        waiver_pickups: [{ name: 'Bench Big', reasoning: 'helps blocks' }],
        summary: 'Add blocks.',
      }),
      model: 'test',
      provider: 'test',
    });

    // act
    const res = await request(app)
      .get('/api/ai/waiver-suggestions?refresh=true')
      .set('Authorization', bearerFor(1));

    // assert
    expect(res.status).toBe(200);
    expect(res.body.waiver_pickups).toEqual([{ name: 'Bench Big', reasoning: 'helps blocks' }]);
    const [{ system, messages }] = narrate.mock.calls[0];
    expect(system).toContain('already ranked numerically by marginal value to this roster');
    const prompt = String(messages[0].content);
    expect(prompt).toContain('RANKING METHOD: both candidate lists below are pre-ranked numerically');
    expect(prompt).toMatch(/1\. \[score -?\d+\.\d{3} \| season_average, per game \(schedule unknown\)\] Bench Big/);
    expect(prompt).toContain('PROJECTED NEXT 7 DAYS: not available, no complete production model run exists yet.');
  });
});
