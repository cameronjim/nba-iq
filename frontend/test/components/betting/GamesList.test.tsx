import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { GamesList } from '../../../src/components/betting/GamesList';
import type { BettingGame } from '../../../src/types';

const pricedGame: BettingGame = {
  espn_event_id: '401859966',
  home_team: 'New York Knicks',
  away_team: 'San Antonio Spurs',
  home_abbrev: 'NY',
  away_abbrev: 'SA',
  game_date: '2026-06-10',
  tipoff: '6/10 - 8:30 PM EDT',
  provider: 'Draft Kings',
  markets: {
    spread: { home_line: -2.5, away_line: 2.5, home_price: -105, away_price: null, home_implied: 0.5122, away_implied: null },
    total: { line: 216.5, over_price: -112, under_price: -108, over_implied: 0.5283, under_implied: 0.5192 },
  },
};

describe('GamesList', () => {
  it('renders one plain sentence per game with no percentages', () => {
    // arrange + act
    render(<GamesList games={[pricedGame]} loading={false} error="" onRetry={vi.fn()} />);

    // assert
    expect(
      screen.getByText('San Antonio Spurs at New York Knicks · 6/10 - 8:30 PM EDT · New York Knicks favored by 2.5 · total 216.5')
    ).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it('shows American prices, missing prices, and the book under Prices', async () => {
    // arrange
    render(<GamesList games={[pricedGame]} loading={false} error="" onRetry={vi.fn()} />);
    const user = userEvent.setup();

    // act
    await user.click(screen.getByText('Prices'));

    // assert
    const table = screen.getByRole('table');
    expect(within(table).getByText('-2.5 (-105)')).toBeVisible();
    expect(within(table).getByText('+2.5 (not posted)')).toBeVisible();
    expect(within(table).getByText('Over 216.5 (-112)')).toBeVisible();
    expect(within(table).getAllByText('not posted')).toHaveLength(2);
    expect(screen.getByText('Prices from Draft Kings.')).toBeVisible();
  });

  it('collapses long slates behind a See more button', async () => {
    // arrange
    const games = Array.from({ length: 8 }, (_, i) => ({ ...pricedGame, espn_event_id: String(i), away_team: `Team ${i}` }));
    render(<GamesList games={games} loading={false} error="" onRetry={vi.fn()} />);
    const user = userEvent.setup();
    expect(screen.queryByText(/^Team 7 at/)).not.toBeInTheDocument();

    // act
    await user.click(screen.getByRole('button', { name: 'See 2 more' }));

    // assert
    expect(screen.getByText(/^Team 7 at/)).toBeInTheDocument();
  });

  it('shows a skeleton while loading', () => {
    // arrange + act
    render(<GamesList games={[]} loading error="" onRetry={vi.fn()} />);

    // assert
    expect(screen.getByRole('status', { name: "Loading tonight's games" })).toBeInTheDocument();
  });

  it('shows the one-sentence empty state', () => {
    // arrange + act
    render(<GamesList games={[]} loading={false} error="" onRetry={vi.fn()} />);

    // assert
    expect(screen.getByText('No games with posted odds in the next two days.')).toBeInTheDocument();
  });

  it('shows one sentence and a retry on error', async () => {
    // arrange
    const onRetry = vi.fn();
    render(<GamesList games={[]} loading={false} error="boom" onRetry={onRetry} />);
    const user = userEvent.setup();

    // act
    await user.click(screen.getByRole('button', { name: 'Try again' }));

    // assert
    expect(screen.getByText("Couldn't load the odds right now.")).toBeInTheDocument();
    expect(onRetry).toHaveBeenCalled();
  });
});
