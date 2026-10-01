import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PicksList } from '../../../src/components/betting/PicksList';
import type { BettingPick, BettingPicksResponse } from '../../../src/types';

const makePick = (overrides: Partial<BettingPick> = {}): BettingPick => ({
  game_id: '401859966',
  category: 'best_value',
  market: 'spread',
  selection: 'home',
  matchup: 'San Antonio Spurs @ New York Knicks',
  game_date: '2026-06-10',
  tipoff: '6/10 - 8:30 PM EDT',
  selection_label: 'New York Knicks -2.5',
  line: -2.5,
  american_odds: -105,
  implied_prob: 0.5122,
  implied_prob_novig: 0.4891,
  estimated_win_prob: 0.58,
  estimate_source: 'claude',
  edge: 0.0678,
  rationale: 'Home team has the rest advantage.',
  confidence: 'medium',
  ...overrides,
});

const makeResponse = (overrides: Partial<BettingPicksResponse> = {}): BettingPicksResponse => ({
  picks: [
    makePick({ category: 'hail_mary', market: 'moneyline', selection: 'away', selection_label: 'San Antonio Spurs ML (+105)', american_odds: 105, line: null, implied_prob_novig: null, implied_prob: 0.4651, confidence: 'low' }),
    makePick(),
    makePick({ category: 'safe', market: 'moneyline', selection: 'home', selection_label: 'New York Knicks ML (-130)', american_odds: -130, line: null, confidence: 'high' }),
  ],
  parlay: {
    legs: [
      { game_id: '401859966', market: 'spread', selection: 'home', selection_label: 'New York Knicks -2.5', matchup: 'San Antonio Spurs @ New York Knicks', american_odds: -105 },
      { game_id: '401859967', market: 'total', selection: 'under', selection_label: 'Under 216.5', matchup: 'Boston Celtics @ Miami Heat', american_odds: -108 },
    ],
    combined_american: 271,
    combined_implied_prob: 0.2695,
    rationale: 'Both legs lean on slow pace.',
    ev_note: 'Parlays multiply the house edge, so treat this as entertainment.',
  },
  summary: 'A thin slate with one strong value play.',
  ...overrides,
});

const renderList = (picks: BettingPicksResponse | null, extra: Partial<Parameters<typeof PicksList>[0]> = {}): void => {
  render(<PicksList picks={picks} loading={false} refreshing={false} error="" onReload={vi.fn()} {...extra} />);
};

describe('PicksList', () => {
  it('renders each pick as a bet in words with a plain category label and confidence word', () => {
    // arrange + act
    renderList(makeResponse({ parlay: null }));

    // assert
    expect(screen.getByText('New York Knicks -2.5 (-105)')).toBeInTheDocument();
    expect(screen.getByText(/Best value · Medium confidence/)).toBeInTheDocument();
    expect(screen.getByText('New York Knicks to win (-130)')).toBeInTheDocument();
    expect(screen.getByText(/Safer · High confidence/)).toBeInTheDocument();
    expect(screen.getByText('San Antonio Spurs to win (+105)')).toBeInTheDocument();
    expect(screen.getByText(/Long shot · Low confidence/)).toBeInTheDocument();
  });

  it('orders picks best value, safer, then long shot', () => {
    // arrange + act
    renderList(makeResponse({ parlay: null }));

    // assert
    const labels = screen.getAllByText(/confidence$/).map((el) => el.textContent?.trim());
    expect(labels).toEqual(['· Best value · Medium confidence', '· Safer · High confidence', '· Long shot · Low confidence']);
  });

  it('compares Claude to the no-vig price, falling back to the raw price', () => {
    // arrange + act
    renderList(makeResponse({ parlay: null }));

    // assert
    expect(screen.getAllByText('Claude thinks this hits about 58% of the time; the price implies 49%.')).toHaveLength(2);
    expect(screen.getByText('Claude thinks this hits about 58% of the time; the price implies 47%.')).toBeInTheDocument();
    expect(screen.queryByText(/\+6\.8%/)).not.toBeInTheDocument();
  });

  it('keeps the parlay folded under one disclosure with legs as sentences', async () => {
    // arrange
    renderList(makeResponse());
    const user = userEvent.setup();
    expect(screen.getByText('New York Knicks -2.5 (-105), San Antonio Spurs at New York Knicks.')).not.toBeVisible();

    // act
    await user.click(screen.getByText("Claude's parlay idea"));

    // assert
    expect(screen.getByText('New York Knicks -2.5 (-105), San Antonio Spurs at New York Knicks.')).toBeVisible();
    expect(screen.getByText('Under 216.5 (-108), Boston Celtics at Miami Heat.')).toBeVisible();
    expect(screen.getByText(/All 2 legs must win\. Together they pay \+271/)).toBeVisible();
    expect(screen.getByText(/Parlays multiply the house edge/)).toBeVisible();
  });

  it('shows a skeleton while loading', () => {
    // arrange + act
    renderList(null, { loading: true });

    // assert
    expect(screen.getByRole('status', { name: 'Loading picks' })).toBeInTheDocument();
  });

  it('shows the one-sentence empty state when there are no games', () => {
    // arrange + act
    renderList({ picks: [], parlay: null, summary: '', no_games: true });

    // assert
    expect(screen.getByText('No games with posted odds in the next two days.')).toBeInTheDocument();
  });

  it('shows one sentence and a retry on error', async () => {
    // arrange
    const onReload = vi.fn();
    renderList(null, { error: 'Failed to load AI picks', onReload });
    const user = userEvent.setup();

    // act
    await user.click(screen.getByRole('button', { name: 'Try again' }));

    // assert
    expect(screen.getByText("Couldn't load Claude's picks.")).toBeInTheDocument();
    expect(onReload).toHaveBeenCalled();
  });
});
