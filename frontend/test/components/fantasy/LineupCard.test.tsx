import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LineupCard } from '../../../src/components/fantasy/LineupCard';
import type { StartSitEntry, StartSitResponse } from '../../../src/types';

function entry(id: number, name: string, start: boolean, sentence: string): StartSitEntry {
  return {
    player_id: id, nba_player_id: String(100 + id), name, nba_game_id: 'g1', opponent: 'LAL', home: true,
    impact: 3 - id, points_if_plays: 20, minutes_if_plays: 25, prob_active: 0.82, start, sentence,
  };
}

function lineup(over: Partial<StartSitResponse> = {}): StartSitResponse {
  return {
    status: 'ok',
    window: { from: '2026-01-15', to: '2026-01-17', days: 3 },
    slots: 2,
    run: { model_version: 'v-test', predicted_at: '2026-01-14T18:00:00.000Z' },
    value_basis: 'slate impact',
    days: [
      { date: '2026-01-15', players: [], recommendation: 'No one on your roster plays.' },
      {
        date: '2026-01-16',
        players: [
          entry(1, 'Stephen Curry', true, 'Stephen Curry · vs LAL · 20 pts, 25 min if he plays · 82% to play'),
          entry(2, 'Jonathan Kuminga', true, 'Jonathan Kuminga · vs LAL · 14 pts, 27 min if he plays · 95% to play'),
          entry(3, 'Draymond Green', false, 'Draymond Green · vs LAL · 8 pts, 30 min if he plays · 60% to play'),
        ],
        recommendation: 'Start these 2; sit Draymond Green.',
      },
      {
        date: '2026-01-17',
        players: [entry(1, 'Stephen Curry', true, 'Stephen Curry · @ BOS · 22 pts, 34 min if he plays · 90% to play')],
        recommendation: 'Start your 1 player with a game; they fit in your 2 slots.',
      },
    ],
    ...over,
  };
}

describe('LineupCard', () => {
  it('shows a skeleton while loading', () => {
    render(<LineupCard state={{ status: 'loading' }} onReload={vi.fn()} />);

    expect(screen.getByRole('status', { name: /loading this week's lineup/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /refreshing/i })).toBeDisabled();
  });

  it('shows the error in one sentence with a retry', async () => {
    const onReload = vi.fn();
    render(<LineupCard state={{ status: 'error', message: "Could not load this week's lineup." }} onReload={onReload} />);

    await userEvent.click(screen.getByRole('button', { name: /try again/i }));

    expect(screen.getByText("Could not load this week's lineup.")).toBeInTheDocument();
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['empty_roster', 'Add players to your roster to see a daily lineup.'],
    ['no_run', 'No model run is available yet, so there is no lineup to set.'],
    ['no_games', 'None of your players have projected games in the next week.'],
  ] as const)('explains the %s state', (status, message) => {
    render(<LineupCard state={{ status: 'ready', data: lineup({ status }) }} onReload={vi.fn()} />);

    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
  });

  it('opens on the first day with games and reads the start/sit sentence and rows', () => {
    render(<LineupCard state={{ status: 'ready', data: lineup() }} onReload={vi.fn()} />);

    expect(screen.getByRole('tab', { name: 'Fri, Jan 16 (3)' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('lineup-recommendation')).toHaveTextContent('Start these 2; sit Draymond Green.');
    const rows = within(screen.getByRole('tabpanel')).getAllByRole('listitem');
    expect(rows.map((r) => r.textContent)).toEqual([
      'StartStephen Curry · vs LAL · 20 pts, 25 min if he plays · 82% to play',
      'StartJonathan Kuminga · vs LAL · 14 pts, 27 min if he plays · 95% to play',
      'SitDraymond Green · vs LAL · 8 pts, 30 min if he plays · 60% to play',
    ]);
  });

  it('switches days from the tabs', async () => {
    render(<LineupCard state={{ status: 'ready', data: lineup() }} onReload={vi.fn()} />);

    await userEvent.click(screen.getByRole('tab', { name: 'Thu, Jan 15 (0)' }));

    expect(screen.getByTestId('lineup-recommendation')).toHaveTextContent('No one on your roster plays.');
    expect(within(screen.getByRole('tabpanel')).queryAllByRole('listitem')).toHaveLength(0);
  });
});
