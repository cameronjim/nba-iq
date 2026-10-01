import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { EspnEvent } from '../../src/services/odds.js';

const event = (id: string, name = 'STATUS_SCHEDULED'): EspnEvent => ({
  id,
  date: '2026-06-11T00:30Z',
  status: { type: { name, shortDetail: '6/10 - 8:30 PM EDT' } },
  competitions: [
    {
      competitors: [
        { homeAway: 'home', team: { displayName: 'New York Knicks', abbreviation: 'NY' } },
        { homeAway: 'away', team: { displayName: 'San Antonio Spurs', abbreviation: 'SA' } },
      ],
    },
  ],
});

const okResponse = (events: EspnEvent[]): Response =>
  new Response(JSON.stringify({ events }), { status: 200 });

const loadOdds = async (): Promise<typeof import('../../src/services/odds.js')> => {
  vi.resetModules();
  return import('../../src/services/odds.js');
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-06-10T16:00:00Z'));
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('scoreboardUrls', () => {
  it('builds one single-day url per et day', async () => {
    // arrange
    const { scoreboardUrls } = await loadOdds();

    // act
    const urls = scoreboardUrls(3);

    // assert
    expect(urls.map((u) => u.split('dates=')[1])).toEqual(['20260610', '20260611', '20260612']);
    expect(urls.every((u) => !/dates=\d{8}-/.test(u))).toBe(true);
  });

  it('uses the et calendar date when utc has already rolled over', async () => {
    // arrange
    vi.setSystemTime(new Date('2026-06-11T02:00:00Z'));
    const { scoreboardUrls } = await loadOdds();

    // act
    const urls = scoreboardUrls(1);

    // assert
    expect(urls[0]).toContain('dates=20260610');
  });
});

describe('getUpcomingOdds', () => {
  it('merges days, dedupes events and drops non-scheduled games', async () => {
    // arrange
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(okResponse([event('1'), event('2', 'STATUS_FINAL')]))
      .mockResolvedValueOnce(okResponse([event('1'), event('3')]))
      .mockResolvedValueOnce(okResponse([event('4')]));
    vi.stubGlobal('fetch', fetchMock);
    const { getUpcomingOdds } = await loadOdds();

    // act
    const games = await getUpcomingOdds();
    await getUpcomingOdds();

    // assert
    expect(games.map((g) => g.espn_event_id)).toEqual(['1', '3', '4']);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('returns partial data without caching when one day fails', async () => {
    // arrange
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(okResponse([event('1')]))
      .mockResolvedValueOnce(new Response('bad', { status: 400 }))
      .mockResolvedValueOnce(okResponse([event('3')]))
      .mockResolvedValue(okResponse([]));
    vi.stubGlobal('fetch', fetchMock);
    const { getUpcomingOdds } = await loadOdds();

    // act
    const games = await getUpcomingOdds();
    await getUpcomingOdds();

    // assert
    expect(games.map((g) => g.espn_event_id)).toEqual(['1', '3']);
    expect(fetchMock).toHaveBeenCalledTimes(6);
  });

  it('throws with espnStatus when every day fails', async () => {
    // arrange
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('bad', { status: 400 })));
    const { getUpcomingOdds } = await loadOdds();

    // act + assert
    await expect(getUpcomingOdds()).rejects.toMatchObject({
      message: 'ESPN API unavailable',
      espnStatus: 400,
    });
  });
});
