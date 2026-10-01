import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TradeCheckCard } from '../../../src/components/fantasy/TradeCheckCard';
import { getPlayers } from '../../../src/api/client';
import type { Player, TradeCheckResponse } from '../../../src/types';

vi.mock('../../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../src/api/client')>();
  return { ...actual, getPlayers: vi.fn() };
});

const getPlayersMock = vi.mocked(getPlayers);

const ROSTER = [
  { id: 3, name: 'Ann Guard' },
  { id: 4, name: 'Bo Center' },
];

function trade(over: Partial<TradeCheckResponse> = {}): TradeCheckResponse {
  return {
    status: 'ok',
    window: { from: '2026-01-15', to: '2026-01-21', days: 7 },
    run: { model_version: 'v-test', predicted_at: null },
    seed: 20260930,
    opponent_definition: 'typical opponent',
    give: [{ id: 3, nba_id: '101', name: 'Ann Guard', games: 3 }],
    get: [{ id: 9, nba_id: '909', name: 'Cy Forward', games: 4 }],
    before_expected_wins: 4.12,
    after_expected_wins: 4.72,
    delta_expected_wins: 0.6,
    categories: [
      { category: 'reb', label: 'REB', lower_is_better: false, before: 0.4, after: 0.71, delta: 0.31 },
      { category: 'blk', label: 'BLK', lower_is_better: false, before: 0.35, after: 0.6, delta: 0.25 },
      { category: 'fg3m', label: '3PM', lower_is_better: false, before: 0.6, after: 0.48, delta: -0.12 },
    ],
    verdict: 'This trade helps: +0.6 expected category wins, mainly REB and BLK; you lose some 3PM.',
    ...over,
  };
}

beforeEach(() => {
  getPlayersMock.mockReset();
});

describe('TradeCheckCard', () => {
  it('checks the picked give and get ids', async () => {
    // arrange
    getPlayersMock.mockResolvedValue([
      { id: 9, name: 'Cy Forward', position: 'SF', team: 'BOS' } as Player,
      { id: 4, name: 'Bo Center Lookalike', position: 'C', team: 'NYK' } as Player,
    ]);
    const onCheck = vi.fn();
    render(<TradeCheckCard roster={ROSTER} state={{ status: 'idle' }} onCheck={onCheck} />);
    const check = screen.getByRole('button', { name: 'Check trade' });
    expect(check).toBeDisabled();

    // act
    await userEvent.click(screen.getByRole('button', { name: 'Ann Guard' }));
    await userEvent.type(screen.getByRole('textbox', { name: /search players to get/i }), 'cy');
    await userEvent.click(await screen.findByRole('button', { name: /Cy Forward/ }));
    await userEvent.click(check);

    // assert
    expect(screen.getByRole('button', { name: 'Ann Guard' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Remove Cy Forward' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Bo Center Lookalike/ })).not.toBeInTheDocument();
    expect(onCheck).toHaveBeenCalledWith([3], [9]);
    expect(getPlayersMock).toHaveBeenCalledWith({ search: 'cy' });
  });

  it('relabels the button and shows a skeleton while checking', () => {
    render(<TradeCheckCard roster={ROSTER} state={{ status: 'checking' }} onCheck={vi.fn()} />);

    expect(screen.getByRole('button', { name: 'Checking' })).toBeDisabled();
    expect(screen.getByRole('status', { name: /checking trade/i })).toBeInTheDocument();
  });

  it('shows the verdict, the illustrative note and a per-category table', () => {
    render(<TradeCheckCard roster={ROSTER} state={{ status: 'ready', data: trade() }} onCheck={vi.fn()} />);

    expect(screen.getByTestId('trade-verdict')).toHaveTextContent(
      'This trade helps: +0.6 expected category wins, mainly REB and BLK; you lose some 3PM.'
    );
    expect(screen.getByTestId('trade-note')).toHaveTextContent(
      "illustrative verdict: based on the model's projections and a typical opponent"
    );
    expect(screen.getByText('Expected category wins: 4.1 now, 4.7 after.')).toBeInTheDocument();
    const rows = within(screen.getByRole('table')).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('REB40%71%+0.31');
    expect(rows[3]).toHaveTextContent('3PM60%48%-0.12');
  });

  it.each([
    ['no_run', 'No model run is available yet, so there is nothing to simulate.'],
    ['no_games', 'None of these players have projected games in the next week.'],
  ] as const)('explains the %s state in one sentence', (status, message) => {
    render(
      <TradeCheckCard
        roster={ROSTER}
        state={{ status: 'ready', data: trade({ status, verdict: null, categories: [] }) }}
        onCheck={vi.fn()}
      />
    );

    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('shows the error sentence', () => {
    render(
      <TradeCheckCard roster={ROSTER} state={{ status: 'error', message: 'Could not check this trade.' }} onCheck={vi.fn()} />
    );

    expect(screen.getByText('Could not check this trade.')).toBeInTheDocument();
  });
});
