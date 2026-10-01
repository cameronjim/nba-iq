import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BetLedger } from '../../../src/components/betting/BetLedger';
import type { Bet } from '../../../src/types';

const summary = { wins: 2, losses: 1, pushes: 0, pending: 2, net: 47.62 };

const bets: Bet[] = [
  {
    id: 1, market: 'spread', nba_game_id: '401', home_team: 'New York Knicks',
    away_team: 'San Antonio Spurs', game_date: '2026-06-10T00:00:00.000Z', selection: 'home',
    line: -2.5, american_odds: -105, description: null, stake: 50, wager_type: 'cash',
    status: 'won', created_at: '2026-06-09T12:00:00Z', settled_at: '2026-06-11T03:00:00Z',
    net: 47.62, to_win: 47.62,
  },
  {
    id: 2, market: 'prop', nba_game_id: '401', home_team: 'New York Knicks',
    away_team: 'San Antonio Spurs', game_date: '2026-06-10T00:00:00.000Z', selection: null,
    line: null, american_odds: -115, description: 'Brunson over 28.5 points',
    stake: 25, wager_type: 'bonus_bet',
    status: 'pending', created_at: '2026-06-09T13:00:00Z', settled_at: null,
    net: null, to_win: 21.74,
  },
  {
    id: 3, market: 'custom', nba_game_id: null, home_team: null,
    away_team: null, game_date: null, selection: null,
    line: null, american_odds: 600, description: 'First basket: Wembanyama',
    stake: null, wager_type: 'cash',
    status: 'pending', created_at: '2026-06-09T14:00:00Z', settled_at: null, net: null,
  },
];

const noopTrack = (): Promise<void> => Promise.resolve();
const noopSettle = (): Promise<void> => Promise.resolve();
const noopRemove = (): Promise<void> => Promise.resolve();

describe('BetLedger', () => {
  it('renders the pending count and money summary as simple text', () => {
    render(
      <BetLedger
        bets={bets}
        summary={summary}
        loading={false}
        error=""
        games={[]}
        onTrackBet={noopTrack}
        onSettleBet={noopSettle}
        onRemoveBet={noopRemove}
      />
    );

    expect(screen.getByText('2 pending')).toBeInTheDocument();
    expect(screen.getByText('Net: +$47.62')).toBeInTheDocument();
  });

  it('renders dates as plain days and shows stake, wager kind, and net', () => {
    render(
      <BetLedger
        bets={bets}
        summary={summary}
        loading={false}
        error=""
        games={[]}
        onTrackBet={noopTrack}
        onSettleBet={noopSettle}
        onRemoveBet={noopRemove}
      />
    );

    expect(screen.getAllByText('2026-06-10')).toHaveLength(2);
    expect(screen.queryByText(/T00:00:00/)).not.toBeInTheDocument();
    expect(screen.getByText('$50.00')).toBeInTheDocument();
    expect(screen.getByText('(Bonus bet)')).toBeInTheDocument();
    expect(screen.getByText('+$47.62')).toBeInTheDocument();
    expect(screen.getByText('to win $21.74')).toBeInTheDocument();
  });

  it('labels straight bets from their fields and text bets from their description', () => {
    render(
      <BetLedger
        bets={bets}
        summary={summary}
        loading={false}
        error=""
        games={[]}
        onTrackBet={noopTrack}
        onSettleBet={noopSettle}
        onRemoveBet={noopRemove}
      />
    );

    expect(screen.getByText('New York Knicks -2.5')).toBeInTheDocument();
    expect(screen.getByText('Brunson over 28.5 points')).toBeInTheDocument();
    expect(screen.getByText('First basket: Wembanyama')).toBeInTheDocument();
    expect(screen.getByText('W')).toBeInTheDocument();
    expect(screen.getAllByText('Pending')).toHaveLength(2);
  });

  it('offers Won/Lost buttons only on pending non-straight bets', async () => {
    const onSettleBet = vi.fn().mockResolvedValue(undefined);
    render(
      <BetLedger
        bets={bets}
        summary={summary}
        loading={false}
        error=""
        games={[]}
        onTrackBet={noopTrack}
        onSettleBet={onSettleBet}
        onRemoveBet={noopRemove}
      />
    );
    const user = userEvent.setup();

    const wonButtons = screen.getAllByRole('button', { name: 'Won' });
    expect(wonButtons).toHaveLength(2);

    await user.click(wonButtons[0]);

    expect(onSettleBet).toHaveBeenCalledWith(2, 'won');
  });

  it('shows the empty state when no bets are tracked', () => {
    render(
      <BetLedger
        bets={[]}
        summary={{ wins: 0, losses: 0, pushes: 0, pending: 0 }}
        loading={false}
        error=""
        games={[]}
        onTrackBet={noopTrack}
        onSettleBet={noopSettle}
        onRemoveBet={noopRemove}
      />
    );

    expect(screen.getByText(/No bets tracked yet/i)).toBeInTheDocument();
  });

  it('calls onRemoveBet when the delete button is clicked', async () => {
    const onRemoveBet = vi.fn().mockResolvedValue(undefined);
    render(
      <BetLedger
        bets={bets}
        summary={summary}
        loading={false}
        error=""
        games={[]}
        onTrackBet={noopTrack}
        onSettleBet={noopSettle}
        onRemoveBet={onRemoveBet}
      />
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: /Delete bet: New York Knicks -2\.5/i }));

    expect(onRemoveBet).toHaveBeenCalledWith(1);
  });

  it('reveals the type-aware add form', async () => {
    render(
      <BetLedger
        bets={[]}
        summary={{ wins: 0, losses: 0, pushes: 0, pending: 0 }}
        loading={false}
        error=""
        games={[]}
        onTrackBet={noopTrack}
        onSettleBet={noopSettle}
        onRemoveBet={noopRemove}
      />
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: '+ Add bet' }));
    expect(screen.getByLabelText('Bet type')).toBeInTheDocument();
    expect(screen.getByLabelText('Game')).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('Bet type'), 'parlay');
    expect(screen.getByLabelText('List the legs')).toBeInTheDocument();
    expect(screen.queryByLabelText('Game')).not.toBeInTheDocument();
  });

  it('shows a skeleton table while loading', () => {
    // arrange + act
    render(
      <BetLedger
        bets={[]}
        summary={{ wins: 0, losses: 0, pushes: 0, pending: 0 }}
        loading
        error=""
        games={[]}
        onTrackBet={noopTrack}
        onSettleBet={noopSettle}
        onRemoveBet={noopRemove}
      />
    );

    // assert
    expect(screen.getByRole('status', { name: 'Loading bets' })).toBeInTheDocument();
  });
});
