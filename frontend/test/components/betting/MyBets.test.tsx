import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MyBets } from '../../../src/components/betting/MyBets';
import type { Bet, BettingGame, LedgerSummary } from '../../../src/types';

const summary: LedgerSummary = { wins: 1, losses: 0, pushes: 0, pending: 2, net: 47.62 };

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
    id: 3, market: 'total', nba_game_id: '402', home_team: 'Miami Heat',
    away_team: 'Boston Celtics', game_date: '2026-06-11T00:00:00.000Z', selection: 'under',
    line: 216.5, american_odds: -108, description: null, stake: 10, wager_type: 'cash',
    status: 'pending', created_at: '2026-06-09T14:00:00Z', settled_at: null, net: null, to_win: 9.26,
  },
];

const game: BettingGame = {
  espn_event_id: '401859966',
  home_team: 'New York Knicks',
  away_team: 'San Antonio Spurs',
  home_abbrev: 'NY',
  away_abbrev: 'SA',
  game_date: '2026-06-10',
  tipoff: '6/10 - 8:30 PM EDT',
  provider: 'Draft Kings',
  markets: {
    spread: { home_line: -2.5, away_line: 2.5, home_price: -105, away_price: -115, home_implied: 0.5122, away_implied: 0.5349 },
    moneyline: { home: -130, away: 105, home_implied: 0.5652, away_implied: 0.4878 },
  },
};

const EMPTY: LedgerSummary = { wins: 0, losses: 0, pushes: 0, pending: 0, net: 0 };

type Props = Parameters<typeof MyBets>[0];

const renderBets = (overrides: Partial<Props> = {}): Props => {
  const props: Props = {
    bets,
    summary,
    loading: false,
    error: '',
    games: [game],
    onRetry: vi.fn(),
    onTrackBet: vi.fn().mockResolvedValue(undefined),
    onSettleBet: vi.fn().mockResolvedValue(undefined),
    onRemoveBet: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  render(<MyBets {...props} />);
  return props;
};

describe('MyBets', () => {
  it('puts the totals in one sentence above the table', () => {
    // arrange + act
    renderBets();

    // assert
    expect(screen.getByText('You are up $47.62 on 1 settled bet, 2 pending.')).toBeInTheDocument();
  });

  it('renders the ledger as date, bet in words, stake, result word, and profit', () => {
    // arrange + act
    renderBets();

    // assert
    expect(screen.getAllByRole('columnheader').map((h) => h.textContent)).toEqual(
      ['Date', 'Bet', 'Stake', 'Result', 'Profit', 'Manage']
    );
    const won = screen.getByText('New York Knicks -2.5 (-105)').closest('tr') as HTMLElement;
    expect(within(won).getByText('2026-06-10')).toBeInTheDocument();
    expect(within(won).getByText('$50.00')).toBeInTheDocument();
    expect(within(won).getByText('Won')).toBeInTheDocument();
    expect(within(won).getByText('+$47.62')).toBeInTheDocument();
    const prop = screen.getByText('Brunson over 28.5 points (-115)').closest('tr') as HTMLElement;
    expect(within(prop).getByText('San Antonio Spurs at New York Knicks · Player prop · Bonus bet')).toBeInTheDocument();
    expect(within(prop).getByText('Pending')).toBeInTheDocument();
    expect(within(prop).getByText('to win $21.74')).toBeInTheDocument();
  });

  it('keeps settlement controls in a per-row disclosure, only for hand-graded bets', async () => {
    // arrange
    const props = renderBets();
    const user = userEvent.setup();

    // act
    await user.click(screen.getByLabelText('Manage bet: Brunson over 28.5 points (-115)'));
    await user.click(screen.getByRole('button', { name: 'Mark won' }));

    // assert
    expect(props.onSettleBet).toHaveBeenCalledWith(2, 'won');
    expect(screen.getAllByRole('button', { name: 'Mark won', hidden: true })).toHaveLength(1);
    expect(screen.getByText('Settles from the final score.')).not.toBeVisible();
  });

  it('deletes a bet from its disclosure', async () => {
    // arrange
    const props = renderBets();
    const user = userEvent.setup();

    // act
    await user.click(screen.getByLabelText('Manage bet: New York Knicks -2.5 (-105)'));
    await user.click(screen.getByRole('button', { name: 'Delete bet: New York Knicks -2.5 (-105)' }));

    // assert
    expect(props.onRemoveBet).toHaveBeenCalledWith(1);
  });

  it('prefills line and odds from the selected market and lets them be edited', async () => {
    // arrange
    const props = renderBets({ bets: [], summary: EMPTY });
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Add a bet' }));

    // act
    await user.selectOptions(screen.getByLabelText('Which game'), '401859966');
    await user.selectOptions(screen.getByLabelText('Which side'), 'away');

    // assert
    expect(screen.getByText('Line and odds: +2.5 at -115')).toBeInTheDocument();
    expect(screen.getByLabelText('Line')).toHaveValue('2.5');
    expect(screen.getByLabelText('Odds')).toHaveValue('-115');

    // act
    await user.click(screen.getByText('Line and odds: +2.5 at -115'));
    await user.clear(screen.getByLabelText('Odds'));
    await user.type(screen.getByLabelText('Odds'), '-110');
    await user.type(screen.getByLabelText('Stake'), '20');
    await user.click(screen.getByRole('button', { name: 'Add bet' }));

    // assert
    expect(props.onTrackBet).toHaveBeenCalledWith(
      expect.objectContaining({ market: 'spread', selection: 'away', line: 2.5, american_odds: -110, stake: 20 }),
      expect.objectContaining({ home_team: 'New York Knicks' })
    );
  });

  it('refills the price when switching to the team-to-win market', async () => {
    // arrange
    renderBets({ bets: [], summary: EMPTY });
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Add a bet' }));
    await user.selectOptions(screen.getByLabelText('Which game'), '401859966');

    // act
    await user.selectOptions(screen.getByLabelText('What kind of bet'), 'moneyline');

    // assert
    expect(screen.getByText('Line and odds: -130')).toBeInTheDocument();
    expect(screen.queryByLabelText('Line')).not.toBeInTheDocument();
  });

  it('shows a skeleton while loading', () => {
    // arrange + act
    renderBets({ bets: [], summary: EMPTY, loading: true });

    // assert
    expect(screen.getByRole('status', { name: 'Loading bets' })).toBeInTheDocument();
  });

  it('shows the one-sentence empty state', () => {
    // arrange + act
    renderBets({ bets: [], summary: EMPTY });

    // assert
    expect(screen.getByText("You haven't tracked any bets yet.")).toBeInTheDocument();
  });

  it('shows one sentence and a retry when the ledger fails to load', async () => {
    // arrange
    const props = renderBets({ bets: [], summary: EMPTY, error: 'Failed to load your bets' });
    const user = userEvent.setup();

    // act
    await user.click(screen.getByRole('button', { name: 'Try again' }));

    // assert
    expect(screen.getByText("Couldn't load your bets.")).toBeInTheDocument();
    expect(props.onRetry).toHaveBeenCalled();
  });
});
