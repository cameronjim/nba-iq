import { describe, it, expect, beforeEach } from 'vitest';
import {
  setAuthToken,
  getAuthToken,
  normalizeSlate,
  normalizeWatchlist,
  slateParams,
  watchlistParams,
} from '../../src/api/client';
import type { SlateGame, SlatePlayer, WatchlistPlayer, WatchlistResponse } from '../../src/types';

describe('watchlistParams', () => {
  it('omits the default window and every position from the URL', () => {
    expect(watchlistParams('2026-02-04', 1, null)).toEqual({ date: '2026-02-04' });
    expect(watchlistParams('2026-02-04')).toEqual({ date: '2026-02-04' });
  });

  it('sends the window and the position once they are chosen', () => {
    expect(watchlistParams('2026-02-04', 7, 'G')).toEqual({
      date: '2026-02-04',
      days: 7,
      position: 'G',
    });
  });

  it('lets the server decide the date when none is given', () => {
    expect(watchlistParams(undefined, 14, null)).toEqual({ days: 14 });
  });
});

describe('normalizeWatchlist', () => {
  it('reads a windowless response as a one-day, one-game answer', () => {
    const data = {
      date: '2026-02-04',
      players: [{ nba_player_id: '1', name: 'A', score: 1.2 } as unknown as WatchlistPlayer],
    } as Partial<WatchlistResponse>;

    const res = normalizeWatchlist(data);

    expect(res.window).toEqual({ from: '2026-02-04', to: '2026-02-04', days: 1 });
    expect(res.position).toBeNull();
    expect(res.position_options).toEqual(['G', 'F', 'C', 'PG', 'SG', 'SF', 'PF']);
    expect(res.position_coverage).toEqual({ known: 0, unknown: 0 });
    expect(res.players[0]).toMatchObject({
      position: null,
      games_count: 1,
      games: [],
      score_per_game: 1.2,
      totals: {},
      reasons: [],
      drivers: [],
      evidence: {},
    });
  });

  it('keeps a full payload exactly as the server sent it', () => {
    const data = {
      date: '2026-02-04',
      window: { from: '2026-02-04', to: '2026-02-10', days: 7 },
      position: 'G' as const,
      position_options: ['G', 'F', 'C'] as const,
      position_coverage: { known: 500, unknown: 3 },
      players: [],
    } as unknown as Partial<WatchlistResponse>;

    const res = normalizeWatchlist(data);

    expect(res.window).toEqual({ from: '2026-02-04', to: '2026-02-10', days: 7 });
    expect(res.position).toBe('G');
    expect(res.position_options).toEqual(['G', 'F', 'C']);
    expect(res.position_coverage).toEqual({ known: 500, unknown: 3 });
  });

  it('survives an empty body rather than throwing on it', () => {
    const res = normalizeWatchlist({});
    expect(res.players).toEqual([]);
    expect(res.window.days).toBe(1);
  });
});

describe('slateParams', () => {
  it('leaves the default sort off the url', () => {
    expect(slateParams('2026-02-04', 'impact')).toEqual({ date: '2026-02-04' });
    expect(slateParams('2026-02-04')).toEqual({ date: '2026-02-04' });
  });

  it('sends the edge sort', () => {
    expect(slateParams('2026-02-04', 'edge')).toEqual({ date: '2026-02-04', sort: 'edge' });
  });
});

describe('normalizeSlate', () => {
  it('fills the explanation fields an older server does not send', () => {
    // a row from before vs_usual and reasons existed.
    const legacy = { nba_player_id: '1', name: 'Old Row' } as unknown as SlatePlayer;
    const game = { nba_game_id: 'g', players: [legacy] } as unknown as SlateGame;

    const res = normalizeSlate({ date: '2026-02-04', games: [game] }, 'edge');

    expect(res.sort).toBe('edge');
    expect(res.games[0].top_edge).toBeNull();
    expect(res.games[0].players[0]).toMatchObject({
      edge: null,
      vs_usual: null,
      reasons: [],
      evidence: {},
    });
  });
});

describe('auth token storage', () => {
  beforeEach(() => {
    setAuthToken(null);
  });

  it('persists the token to localStorage when set', () => {
    setAuthToken('abc.def.ghi');

    expect(getAuthToken()).toBe('abc.def.ghi');
    expect(localStorage.getItem('auth_token')).toBe('abc.def.ghi');
  });

  it('clears the token from localStorage when set to null', () => {
    setAuthToken('a-token');

    setAuthToken(null);

    expect(getAuthToken()).toBeNull();
    expect(localStorage.getItem('auth_token')).toBeNull();
  });
});
