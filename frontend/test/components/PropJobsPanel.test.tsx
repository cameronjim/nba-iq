import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PropJobsPanel } from '../../src/components/admin/PropJobsPanel';

const client = vi.hoisted(() => ({
  refreshPropPicks: vi.fn(),
  settlePropPicks: vi.fn(),
  getPropPicksSummary: vi.fn(),
}));

vi.mock('../../src/api/client', () => client);

const POPULATED = {
  markets: [
    {
      market: 'all', picks: 12, settled: 9, wins: 5, losses: 3, pushes: 1, voids: 0,
      hit_rate: 0.625, avg_ev: 0.071, clv_count: 9, avg_clv_points: 1.3,
    },
    {
      market: 'pts', picks: 6, settled: 5, wins: 3, losses: 2, pushes: 0, voids: 0,
      hit_rate: 0.6, avg_ev: 0.05, clv_count: 5, avg_clv_points: null,
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  client.getPropPicksSummary.mockResolvedValue({ markets: [] });
});

describe('PropJobsPanel', () => {
  it('starts idle with both buttons and no result line', async () => {
    // act
    render(<PropJobsPanel />);

    // assert
    expect(screen.getByRole('button', { name: 'Refresh prop picks' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Settle prop picks' })).toBeEnabled();
    expect(await screen.findByText('No settled prop picks yet.')).toBeInTheDocument();
  });

  it('shows the running label while a refresh is in flight', async () => {
    // arrange
    client.refreshPropPicks.mockReturnValue(new Promise(() => {}));
    render(<PropJobsPanel />);

    // act
    await userEvent.click(screen.getByRole('button', { name: 'Refresh prop picks' }));

    // assert
    expect(screen.getByRole('button', { name: 'Refreshing' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Settle prop picks' })).toBeDisabled();
  });

  it('shows a result sentence after settling', async () => {
    // arrange
    client.settlePropPicks.mockResolvedValue({
      examined: 9, settled: 9, by_result: { win: 5, loss: 3, push: 1, void: 0 },
    });
    render(<PropJobsPanel />);

    // act
    await userEvent.click(screen.getByRole('button', { name: 'Settle prop picks' }));

    // assert
    expect(await screen.findByText('Settled 9 picks: 5 won, 3 lost, 1 pushed.')).toBeInTheDocument();
  });

  it('shows a result sentence after refreshing', async () => {
    // arrange
    client.refreshPropPicks.mockResolvedValue({ recorded: 14, candidates: 40, snapshots: 300 });
    render(<PropJobsPanel />);

    // act
    await userEvent.click(screen.getByRole('button', { name: 'Refresh prop picks' }));

    // assert
    expect(await screen.findByText(/^Recorded 14 picks from 40 candidates/)).toBeInTheDocument();
  });

  it('shows an error sentence when the job fails', async () => {
    // arrange
    client.refreshPropPicks.mockRejectedValue(new Error('boom'));
    render(<PropJobsPanel />);

    // act
    await userEvent.click(screen.getByRole('button', { name: 'Refresh prop picks' }));

    // assert
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not refresh prop picks. Try again.');
    expect(screen.getByRole('button', { name: 'Refresh prop picks' })).toBeEnabled();
  });

  it('renders the summary table when picks have settled', async () => {
    // arrange
    client.getPropPicksSummary.mockResolvedValue(POPULATED);

    // act
    render(<PropJobsPanel />);

    // assert
    expect(await screen.findByText('62.5%')).toBeInTheDocument();
    expect(screen.getByText('7.1%')).toBeInTheDocument();
    expect(screen.getByText('+1.3 pts')).toBeInTheDocument();
    expect(screen.queryByText('No settled prop picks yet.')).not.toBeInTheDocument();
  });
});
