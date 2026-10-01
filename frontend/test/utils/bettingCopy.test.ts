import { describe, it, expect } from 'vitest';
import {
  chanceSentence,
  favoredText,
  gameSentence,
  ledgerBetText,
  ledgerSummarySentence,
  moneyText,
  priceText,
  resultWord,
  straightBetText,
  totalText,
} from '../../src/utils/bettingCopy';
import type { Bet, BettingGame, SpreadMarket } from '../../src/types';

const spread = (homeLine: number): SpreadMarket => ({
  home_line: homeLine,
  away_line: -homeLine,
  home_price: -110,
  away_price: -110,
  home_implied: 0.5238,
  away_implied: 0.5238,
});

const game: BettingGame = {
  espn_event_id: '1',
  home_team: 'Golden State Warriors',
  away_team: 'Los Angeles Lakers',
  home_abbrev: 'GS',
  away_abbrev: 'LAL',
  game_date: '2026-10-21',
  tipoff: '7:30 PM',
  provider: 'Draft Kings',
  markets: {
    spread: spread(-4.5),
    total: { line: 228.5, over_price: -110, under_price: -110, over_implied: 0.5238, under_implied: 0.5238 },
  },
};

const baseBet: Bet = {
  id: 1, market: 'spread', nba_game_id: '1', home_team: 'Golden State Warriors',
  away_team: 'Los Angeles Lakers', game_date: '2026-10-21', selection: 'home', line: -4.5,
  american_odds: -110, description: null, stake: 10, wager_type: 'cash', status: 'pending',
  created_at: '2026-10-20T12:00:00Z', settled_at: null, net: null,
};

describe('favoredText', () => {
  it('names the home team when it gives points', () => {
    // act + assert
    expect(favoredText(spread(-4.5), 'Warriors', 'Lakers')).toBe('Warriors favored by 4.5');
  });

  it('names the away team when the home team is the underdog', () => {
    // act + assert
    expect(favoredText(spread(3), 'Warriors', 'Lakers')).toBe('Lakers favored by 3');
  });

  it('calls a zero spread even', () => {
    // act + assert
    expect(favoredText(spread(0), 'Warriors', 'Lakers')).toBe('rated even');
  });

  it('says the spread is not posted when there is none', () => {
    // act + assert
    expect(favoredText(undefined, 'Warriors', 'Lakers')).toBe('spread not posted');
  });
});

describe('totalText and priceText', () => {
  it('reads a missing total and a missing price as not posted', () => {
    // act + assert
    expect(totalText(undefined)).toBe('total not posted');
    expect(priceText(null)).toBe('not posted');
    expect(priceText(150)).toBe('+150');
  });
});

describe('gameSentence', () => {
  it('describes the matchup, tipoff, favorite, and total in one line', () => {
    // act
    const sentence = gameSentence(game);

    // assert
    expect(sentence).toBe(
      'Los Angeles Lakers at Golden State Warriors · 7:30 PM · Golden State Warriors favored by 4.5 · total 228.5'
    );
  });

  it('says odds are not posted when the game has no markets', () => {
    // act
    const sentence = gameSentence({ ...game, markets: {} });

    // assert
    expect(sentence).toBe('Los Angeles Lakers at Golden State Warriors · 7:30 PM · odds not posted');
  });
});

describe('chanceSentence', () => {
  it('compares against the no-vig price when it is present', () => {
    // act
    const sentence = chanceSentence(0.55, 0.5238, 0.5);

    // assert
    expect(sentence).toBe('Claude thinks this hits about 55% of the time; the price implies 50%.');
  });

  it('falls back to the raw implied price when no-vig is missing', () => {
    // act
    const sentence = chanceSentence(0.55, 0.5238, null);

    // assert
    expect(sentence).toBe('Claude thinks this hits about 55% of the time; the price implies 52%.');
  });
});

describe('straightBetText', () => {
  it('appends the price to spread and total labels', () => {
    // act + assert
    expect(straightBetText({
      market: 'spread', selection: 'home', selection_label: 'Golden State Warriors -4.5',
      matchup: 'Los Angeles Lakers @ Golden State Warriors', american_odds: -110,
    })).toBe('Golden State Warriors -4.5 (-110)');
  });

  it('turns a moneyline into a team to win', () => {
    // act + assert
    expect(straightBetText({
      market: 'moneyline', selection: 'away', selection_label: 'Los Angeles Lakers ML (+150)',
      matchup: 'Los Angeles Lakers @ Golden State Warriors', american_odds: 150,
    })).toBe('Los Angeles Lakers to win (+150)');
  });
});

describe('resultWord', () => {
  it('maps each status to a plain word', () => {
    // act + assert
    expect((['won', 'lost', 'push', 'pending'] as const).map((s) => resultWord(s)))
      .toEqual(['Won', 'Lost', 'Push', 'Pending']);
  });
});

describe('moneyText', () => {
  it('drops cents on whole dollars and keeps them otherwise', () => {
    // act + assert
    expect(moneyText(42)).toBe('$42');
    expect(moneyText(47.6)).toBe('$47.60');
    expect(moneyText(-5)).toBe('-$5');
  });
});

describe('ledgerSummarySentence', () => {
  it('says how far up the user is on settled bets', () => {
    // act
    const sentence = ledgerSummarySentence({ wins: 7, losses: 3, pushes: 1, pending: 0, net: 42 }, true);

    // assert
    expect(sentence).toBe('You are up $42 on 11 settled bets.');
  });

  it('says down for a loss and mentions pending bets', () => {
    // act
    const sentence = ledgerSummarySentence({ wins: 0, losses: 1, pushes: 0, pending: 2, net: -10 }, true);

    // assert
    expect(sentence).toBe('You are down $10 on 1 settled bet, 2 pending.');
  });

  it('handles a ledger with nothing settled', () => {
    // act + assert
    expect(ledgerSummarySentence({ wins: 0, losses: 0, pushes: 0, pending: 1, net: 0 }, true))
      .toBe('No settled bets yet, 1 pending.');
  });

  it('counts wins instead of money when no stakes were recorded', () => {
    // act + assert
    expect(ledgerSummarySentence({ wins: 2, losses: 1, pushes: 0, pending: 0, net: 0 }, false))
      .toBe('You have 3 settled bets, 2 won.');
  });
});

describe('ledgerBetText', () => {
  it('describes spread, moneyline, total, and text bets in words', () => {
    // act + assert
    expect(ledgerBetText(baseBet)).toBe('Golden State Warriors -4.5 (-110)');
    expect(ledgerBetText({ ...baseBet, market: 'moneyline', line: null, selection: 'away', american_odds: 150 }))
      .toBe('Los Angeles Lakers to win (+150)');
    expect(ledgerBetText({ ...baseBet, market: 'total', selection: 'under', line: 228.5 }))
      .toBe('Under 228.5 (-110)');
    expect(ledgerBetText({ ...baseBet, market: 'custom', description: 'First basket: Curry', american_odds: 600 }))
      .toBe('First basket: Curry (+600)');
  });
});
