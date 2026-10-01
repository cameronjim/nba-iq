import { describe, it, expect } from 'vitest';
import {
  clvOrPlaceholder, hasSettledPicks, percentOrPlaceholder, refreshSentence, settleSentence,
} from '../../src/utils/propJobs';
import type { PropMarketSummary } from '../../src/types';

function summary(overrides: Partial<PropMarketSummary>): PropMarketSummary {
  return {
    market: 'pts', picks: 0, settled: 0, wins: 0, losses: 0, pushes: 0, voids: 0,
    hit_rate: null, avg_ev: null, clv_count: 0, avg_clv_points: null, ...overrides,
  };
}

describe('refreshSentence', () => {
  it('reports recorded picks, candidates and snapshots', () => {
    // act
    const text = refreshSentence({ recorded: 14, candidates: 40, snapshots: 300 });

    // assert
    expect(text).toBe('Recorded 14 picks from 40 candidates across 300 odds snapshots.');
  });

  it('uses singular nouns for a count of one', () => {
    // act + assert
    expect(refreshSentence({ recorded: 1, candidates: 1, snapshots: 1 }))
      .toBe('Recorded 1 pick from 1 candidate across 1 odds snapshot.');
  });
});

describe('settleSentence', () => {
  it('lists each result', () => {
    // act
    const text = settleSentence({ examined: 9, settled: 9, by_result: { win: 5, loss: 3, push: 1, void: 0 } });

    // assert
    expect(text).toBe('Settled 9 picks: 5 won, 3 lost, 1 pushed.');
  });

  it('mentions voids only when there are some', () => {
    // act
    const text = settleSentence({ examined: 3, settled: 3, by_result: { win: 1, loss: 1, push: 0, void: 1 } });

    // assert
    expect(text).toBe('Settled 3 picks: 1 won, 1 lost, 0 pushed, 1 voided.');
  });

  it('says so when nothing was ready', () => {
    // act + assert
    expect(settleSentence({ examined: 2, settled: 0, by_result: { win: 0, loss: 0, push: 0, void: 0 } }))
      .toBe('No prop picks were ready to settle.');
  });
});

describe('summary formatting', () => {
  it('formats percentages and placeholders', () => {
    // act + assert
    expect(percentOrPlaceholder(0.5567, '-')).toBe('55.7%');
    expect(percentOrPlaceholder(null, '-')).toBe('-');
  });

  it('signs positive closing line value', () => {
    // act + assert
    expect(clvOrPlaceholder(1.24, '-')).toBe('+1.2 pts');
    expect(clvOrPlaceholder(-0.5, '-')).toBe('-0.5 pts');
    expect(clvOrPlaceholder(null, '-')).toBe('-');
  });

  it('detects whether any pick has settled', () => {
    // act + assert
    expect(hasSettledPicks([summary({ picks: 4 })])).toBe(false);
    expect(hasSettledPicks([summary({ picks: 4, settled: 2 })])).toBe(true);
  });
});
