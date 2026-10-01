import { describe, it, expect } from 'vitest';
import {
  parseSpreadDetails,
  parseEventOdds,
  computeOddsHash,
  type EspnEvent,
} from '../../src/services/odds.js';

const scheduledEvent = (overrides: Partial<EspnEvent> = {}): EspnEvent => ({
  id: '401859966',
  date: '2026-06-11T00:30Z',
  status: { type: { name: 'STATUS_SCHEDULED', detail: 'Wed, June 10th at 8:30 PM EDT', shortDetail: '6/10 - 8:30 PM EDT' } },
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
          moneyline: {
            home: { close: { odds: '-130' } },
            away: { close: { odds: '+105' } },
          },
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
  ...overrides,
});

describe('parseSpreadDetails', () => {
  it('resolves the favorite abbreviation to a home-relative line', () => {
    expect(parseSpreadDetails('NY -2.5', 'NY', 'SA')).toBe(-2.5);
    expect(parseSpreadDetails('SA -2.5', 'NY', 'SA')).toBe(2.5);
  });

  it('treats EVEN as a pick-em', () => {
    expect(parseSpreadDetails('EVEN', 'NY', 'SA')).toBe(0);
  });

  it('returns undefined for garbage or unknown teams', () => {
    expect(parseSpreadDetails('not a spread', 'NY', 'SA')).toBeUndefined();
    expect(parseSpreadDetails('BOS -6.5', 'NY', 'SA')).toBeUndefined();
    expect(parseSpreadDetails(undefined, 'NY', 'SA')).toBeUndefined();
  });
});

describe('parseEventOdds', () => {
  it('parses all three markets with per-side prices and implied probabilities', () => {
    const game = parseEventOdds(scheduledEvent());

    expect(game).not.toBeNull();
    expect(game!.espn_event_id).toBe('401859966');
    expect(game!.home_team).toBe('New York Knicks');
    expect(game!.away_team).toBe('San Antonio Spurs');
    expect(game!.game_date).toBe('2026-06-10'); // 00:30 UTC = 8:30 PM ET previous day
    expect(game!.tipoff).toBe('6/10 - 8:30 PM EDT');
    expect(game!.provider).toBe('Draft Kings');

    expect(game!.markets.spread).toEqual({
      home_line: -2.5,
      away_line: 2.5,
      home_price: -105,
      away_price: -115,
      home_implied: expect.closeTo(0.5122, 3),
      away_implied: expect.closeTo(0.5349, 3),
    });
    expect(game!.markets.total).toEqual({
      line: 216.5,
      over_price: -112,
      under_price: -108,
      over_implied: expect.closeTo(0.5283, 3),
      under_implied: expect.closeTo(0.5192, 3),
    });
    expect(game!.markets.moneyline).toEqual({
      home: -130,
      away: 105,
      home_implied: expect.closeTo(0.5652, 3),
      away_implied: expect.closeTo(0.4878, 3),
    });
  });

  it('falls back to flat fields and leaves missing line prices null', () => {
    const event = scheduledEvent();
    event.competitions[0].odds = [
      {
        provider: { name: 'ESPN BET' },
        details: 'NY -2.5',
        overUnder: 216.5,
        spread: -2.5,
        homeTeamOdds: { moneyLine: -130 },
        awayTeamOdds: { moneyLine: 105 },
      },
    ];

    const game = parseEventOdds(event);

    expect(game!.markets.spread?.home_line).toBe(-2.5);
    expect(game!.markets.spread?.home_price).toBeNull();
    expect(game!.markets.total?.line).toBe(216.5);
    expect(game!.markets.total?.over_price).toBeNull();
    expect(game!.markets.moneyline?.home).toBe(-130);
    expect(game!.markets.moneyline?.away).toBe(105);
  });

  it('yields null prices and null implied for a spread with no prices', () => {
    // arrange
    const event = scheduledEvent();
    event.competitions[0].odds = [{ details: 'NY -2.5', spread: -2.5 }];

    // act
    const spread = parseEventOdds(event)!.markets.spread!;

    // assert
    expect(spread.home_price).toBeNull();
    expect(spread.away_price).toBeNull();
    expect(spread.home_implied).toBeNull();
    expect(spread.away_implied).toBeNull();
  });

  it('yields a null under price when a total only has an over price', () => {
    // arrange
    const event = scheduledEvent();
    event.competitions[0].odds = [{ total: { over: { close: { line: 'o216.5', odds: '-112' } } } }];

    // act
    const total = parseEventOdds(event)!.markets.total!;

    // assert
    expect(total.over_price).toBe(-112);
    expect(total.over_implied).toBeCloseTo(0.5283, 3);
    expect(total.under_price).toBeNull();
    expect(total.under_implied).toBeNull();
  });

  it('returns the game with empty markets when no odds node exists', () => {
    const event = scheduledEvent();
    event.competitions[0].odds = undefined;

    const game = parseEventOdds(event);

    expect(game).not.toBeNull();
    expect(game!.markets).toEqual({});
  });

  it('returns null when competitors are missing', () => {
    const event = scheduledEvent();
    event.competitions[0].competitors = [];

    expect(parseEventOdds(event)).toBeNull();
  });
});

describe('computeOddsHash', () => {
  it('is stable across game order but changes when a line moves', () => {
    const a = parseEventOdds(scheduledEvent())!;
    const b = parseEventOdds(scheduledEvent({ id: '401859967' }))!;
    const aMoved = { ...a, markets: { ...a.markets, total: { ...a.markets.total!, line: 218.5 } } };

    expect(computeOddsHash([a, b])).toBe(computeOddsHash([b, a]));
    expect(computeOddsHash([a, b])).not.toBe(computeOddsHash([aMoved, b]));
  });

  it('is stable when prices are null and differs from a priced market', () => {
    // arrange
    const event = scheduledEvent();
    event.competitions[0].odds = [{ details: 'NY -2.5', spread: -2.5, overUnder: 216.5 }];
    const a = parseEventOdds(event)!;
    const b = parseEventOdds(event)!;
    const priced = parseEventOdds(scheduledEvent())!;

    // act
    const hashA = computeOddsHash([a]);
    const hashB = computeOddsHash([b]);

    // assert
    expect(hashA).toBe(hashB);
    expect(hashA).not.toBe(computeOddsHash([priced]));
  });
});
