import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StreamingPickupsCard } from '../../../src/components/fantasy/StreamingPickupsCard';
import type { Streamer, StreamersResponse } from '../../../src/types';

const EDEY: Streamer = {
  id: 7, nba_id: '1641744', name: 'Zach Edey', team: 'MEM', position: 'C', games: 4, score: 1.82,
  drivers: [
    { category: 'blk', label: 'BLK', value: 0.4 },
    { category: 'reb', label: 'REB', value: 0.3 },
    { category: 'ft', label: 'FT%', value: -0.05 },
  ],
  basis: 'projection', mean_prob_play: 0.92,
  sentence: 'Pick up Zach Edey: 4 games this week, about +1.8 expected category wins.',
};

function streamers(over: Partial<StreamersResponse> = {}): StreamersResponse {
  return {
    status: 'ok',
    window: { from: '2026-01-15', to: '2026-01-21', days: 7 },
    run: null,
    score_basis: 'expected category wins',
    streamers: [EDEY],
    ...over,
  };
}

describe('StreamingPickupsCard', () => {
  it('shows a skeleton while loading', () => {
    render(<StreamingPickupsCard state={{ status: 'loading' }} onReload={vi.fn()} onAdd={vi.fn()} addingId={null} />);

    expect(screen.getByRole('status', { name: /loading streaming pickups/i })).toBeInTheDocument();
  });

  it('shows the error with a retry', async () => {
    const onReload = vi.fn();
    render(
      <StreamingPickupsCard
        state={{ status: 'error', message: 'Could not load streaming pickups.' }}
        onReload={onReload}
        onAdd={vi.fn()}
        addingId={null}
      />
    );

    await userEvent.click(screen.getByRole('button', { name: /try again/i }));

    expect(screen.getByText('Could not load streaming pickups.')).toBeInTheDocument();
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['empty_roster', 'Add players to your roster to see streaming pickups.'],
    ['no_candidates', 'No free agents in the ranking pool are projected for this window.'],
  ] as const)('explains the %s state', (status, message) => {
    render(
      <StreamingPickupsCard
        state={{ status: 'ready', data: streamers({ status, streamers: [] }) }}
        onReload={vi.fn()}
        onAdd={vi.fn()}
        addingId={null}
      />
    );

    expect(screen.getByText(message)).toBeInTheDocument();
  });

  it('lists the pickup sentence with its helping categories and adds on click', async () => {
    const onAdd = vi.fn();
    render(
      <StreamingPickupsCard state={{ status: 'ready', data: streamers() }} onReload={vi.fn()} onAdd={onAdd} addingId={null} />
    );

    await userEvent.click(screen.getByRole('button', { name: 'Add Zach Edey' }));

    expect(screen.getByText('Pick up Zach Edey: 4 games this week, about +1.8 expected category wins.')).toBeInTheDocument();
    expect(screen.getByText('Helps most in BLK, REB')).toBeInTheDocument();
    expect(onAdd).toHaveBeenCalledWith(EDEY);
  });

  it('relabels the button while the add is in flight', () => {
    render(
      <StreamingPickupsCard state={{ status: 'ready', data: streamers() }} onReload={vi.fn()} onAdd={vi.fn()} addingId={7} />
    );

    const button = screen.getByRole('button', { name: 'Add Zach Edey' });
    expect(button).toHaveTextContent('Adding');
    expect(button).toBeDisabled();
  });
});
