import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BettingPicksPanel } from '../../../src/components/betting/BettingPicksPanel';
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
    makePick(),
    makePick({ category: 'safe', market: 'moneyline', selection: 'home', selection_label: 'New York Knicks ML (-130)', american_odds: -130, line: null }),
    makePick({ category: 'hail_mary', market: 'moneyline', selection: 'away', selection_label: 'San Antonio Spurs ML (+105)', american_odds: 105, line: null, edge: -0.01 }),
  ],
  parlay: {
    legs: [
      { game_id: '401859966', market: 'spread', selection: 'home', selection_label: 'New York Knicks -2.5', matchup: 'SAS @ NYK', american_odds: -105 },
      { game_id: '401859967', market: 'total', selection: 'under', selection_label: 'Under 216.5', matchup: 'BOS @ MIA', american_odds: -108 },
    ],
    combined_american: 271,
    combined_implied_prob: 0.2695,
    rationale: 'Both legs lean on slow pace.',
    ev_note: 'Parlays multiply the house edge, so treat this as entertainment.',
  },
  summary: 'A thin slate with one strong value play.',
  ...overrides,
});

describe('BettingPicksPanel', () => {
  it('renders the three category sections with their picks', () => {
    render(
      <BettingPicksPanel
        picks={makeResponse()}
        loading={false}
        refreshing={false}
        error=""
        onReload={vi.fn()}
      />
    );

    expect(screen.getByRole('heading', { name: 'Best Value' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Safe' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Hail Mary' })).toBeInTheDocument();
    // the spread pick's label also appears as a parlay leg, so match all of them.
    expect(screen.getAllByText('New York Knicks -2.5').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('New York Knicks ML (-130)')).toBeInTheDocument();
    expect(screen.getByText('San Antonio Spurs ML (+105)')).toBeInTheDocument();
  });

  it('labels the edge as a Claude estimate', () => {
    render(
      <BettingPicksPanel
        picks={makeResponse({ picks: [makePick()], parlay: null })}
        loading={false}
        refreshing={false}
        error=""
        onReload={vi.fn()}
      />
    );

    expect(screen.getByText('Claude estimate')).toBeInTheDocument();
  });

  it('shows implied probability, AI estimate, a signed edge, and the confidence badge', () => {
    render(
      <BettingPicksPanel
        picks={makeResponse({ picks: [makePick()], parlay: null })}
        loading={false}
        refreshing={false}
        error=""
        onReload={vi.fn()}
      />
    );

    expect(screen.getByText('51.2%')).toBeInTheDocument();
    expect(screen.getByText('58.0%')).toBeInTheDocument();
    expect(screen.getByText('+6.8%')).toBeInTheDocument();
    const badge = screen.getByText('medium confidence');
    expect(badge.className).toContain('badge');
    expect(badge.className).toContain('whitespace-nowrap');
  });

  it('renders the parlay with combined odds and the value warning', () => {
    render(
      <BettingPicksPanel
        picks={makeResponse()}
        loading={false}
        refreshing={false}
        error=""
        onReload={vi.fn()}
      />
    );

    expect(screen.getByRole('heading', { name: 'Suggested Parlay' })).toBeInTheDocument();
    expect(screen.getByText('+271')).toBeInTheDocument();
    expect(screen.getByText(/27\.0% to hit/)).toBeInTheDocument();
    expect(screen.getByText(/Parlays multiply the house edge/i)).toBeInTheDocument();
  });

  it('shows the no-games empty state', () => {
    render(
      <BettingPicksPanel
        picks={{ picks: [], parlay: null, summary: '', no_games: true }}
        loading={false}
        refreshing={false}
        error=""
        onReload={vi.fn()}
      />
    );

    expect(screen.getByText(/No bettable games right now/i)).toBeInTheDocument();
  });
});
