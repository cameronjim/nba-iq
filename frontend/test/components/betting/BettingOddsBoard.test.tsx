import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BettingOddsBoard } from '../../../src/components/betting/BettingOddsBoard';
import type { BettingGame } from '../../../src/types';

const unpricedGame: BettingGame = {
  espn_event_id: '401859966',
  home_team: 'New York Knicks',
  away_team: 'San Antonio Spurs',
  home_abbrev: 'NY',
  away_abbrev: 'SA',
  game_date: '2026-06-10',
  tipoff: '6/10 - 8:30 PM EDT',
  provider: 'Draft Kings',
  markets: {
    spread: { home_line: -2.5, away_line: 2.5, home_price: null, away_price: null, home_implied: null, away_implied: null },
    total: { line: 216.5, over_price: -112, under_price: null, over_implied: 0.5283, under_implied: null },
  },
};

describe('BettingOddsBoard', () => {
  it('renders a dash and no implied badge for a missing price', () => {
    render(<BettingOddsBoard games={[unpricedGame]} loading={false} error="" onRetry={vi.fn()} />);

    expect(screen.getByText('+2.5 (-)')).toBeInTheDocument();
    expect(screen.getByText('U 216.5 (-)')).toBeInTheDocument();
    expect(screen.getByText('O 216.5 (-112)')).toBeInTheDocument();
    expect(screen.getByText('52.8%')).toBeInTheDocument();
    expect(screen.queryByText('NaN%')).not.toBeInTheDocument();
  });
});
