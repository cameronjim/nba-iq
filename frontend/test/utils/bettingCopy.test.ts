import { describe, it, expect } from 'vitest';
import {
  favoredText,
  gameSentence,
  ledgerBetText,
  ledgerSummarySentence,
  moneyText,
  priceText,
  propMarketWords,
  propSentence,
  resultWord,
  totalText,
} from '../../src/utils/bettingCopy';
import type { Bet, BettingGame, PropPick, SpreadMarket } from '../../src/types';

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

const curry: PropPick = {
  player_name: 'Stephen Curry', team: 'GSW', opponent: 'LAL', game_date: '2026-10-21',
  market: 'pts', line: 24.5, side: 'over', bookmaker: 'DraftKings', price: -115,
  model_prob: 0.58, implied_prob_novig: 0.5304, ev: 0.08, prob_active: 0.82, void_rule: 'dnp',
};

describe('propSentence', () => {
  it('reads a prop as one sentence with the model, the price, and the chance to play', () => {
    // act
    const sentence = propSentence(curry);

    // assert
    expect(sentence).toBe(
      'Stephen Curry over 24.5 points (-115, DraftKings) · the model gives this 58%; the price implies 53% · 82% to play'
    );
  });

  it('keeps the sign on plus prices and handles unders', () => {
    // act
    const sentence = propSentence({ ...curry, side: 'under', market: 'fg3m', line: 4.5, price: 120 });

    // assert
    expect(sentence).toMatch(/^Stephen Curry under 4\.5 threes \(\+120, DraftKings\)/);
  });

  it('drops the price chance and the chance to play when they are missing', () => {
    // act
    const sentence = propSentence({ ...curry, implied_prob_novig: null, prob_active: null });

    // assert
    expect(sentence).toBe('Stephen Curry over 24.5 points (-115, DraftKings) · the model gives this 58%');
  });
});

describe('propMarketWords', () => {
  it('names every prop market in words', () => {
    // act
    const words = (['pts', 'reb', 'ast', 'fg3m', 'pra', 'stl', 'blk', 'tov'] as const).map((m) => propMarketWords(m));

    // assert
    expect(words).toEqual([
      'points', 'rebounds', 'assists', 'threes', 'points + rebounds + assists', 'steals', 'blocks', 'turnovers',
    ]);
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
