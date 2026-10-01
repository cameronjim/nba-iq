import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WeeklyOutlookCard } from '../../../src/components/fantasy/WeeklyOutlookCard';
import type { OutlookCategory, WeeklyOutlookResponse } from '../../../src/types';

function category(over: Partial<OutlookCategory>): OutlookCategory {
  return {
    category: 'pts',
    lower_is_better: false,
    mean: 410.2,
    p10: 360.4,
    p50: 408.9,
    p90: 462.1,
    opponent: 395,
    win_probability: 0.62,
    ...over,
  };
}

function outlook(over: Partial<WeeklyOutlookResponse> = {}): WeeklyOutlookResponse {
  return {
    status: 'ok',
    window: { from: '2026-01-15', to: '2026-01-21', days: 7 },
    roster_size: 2,
    run: { model_version: 'v-test', predicted_at: '2026-01-14T18:00:00.000Z' },
    simulation: { n: 2000, seed: 1, dependence_rho: 0.5 },
    opponent: {
      definition: 'top players scaled to your roster',
      league_teams: 12,
      pool_size: 300,
      players_used: 26,
      totals: {
        pts: 395, reb: 150, ast: 90, stl: 25, blk: 18, fg3m: 45, tov: 50, fg_pct: 0.47, ft_pct: 0.78,
      },
    },
    categories: [
      category({}),
      category({ category: 'fg_pct', mean: 0.481, p10: 0.452, p50: 0.48, p90: 0.51, opponent: 0.47, win_probability: 0.7 }),
      category({ category: 'tov', lower_is_better: true, p50: 52, opponent: 50, win_probability: 0.31 }),
    ],
    players: [
      {
        player_id: 3, nba_player_id: '101', name: 'Ann Guard', games_scheduled: 3,
        expected_games: 2.6, miss_risk: 0.24, simulated_miss_risk: 0.25, fallback_stats: [],
      },
      {
        player_id: 4, nba_player_id: '202', name: 'Bo Center', games_scheduled: 0,
        expected_games: 0, miss_risk: 0, simulated_miss_risk: 0, fallback_stats: [],
      },
    ],
    provenance: {
      model_version: 'v-test',
      predicted_at: '2026-01-14T18:00:00.000Z',
      fallback_spread_players: [{ nba_player_id: '101', name: 'Ann Guard', stats: ['blk'] }],
    },
    ...over,
  };
}

describe('WeeklyOutlookCard', () => {
  it('shows a table skeleton while loading', () => {
    render(<WeeklyOutlookCard state={{ status: 'loading' }} onReload={vi.fn()} />);

    expect(screen.getByRole('status', { name: /loading weekly outlook/i })).toBeInTheDocument();
  });

  it('shows the error with a retry that reloads', async () => {
    const onReload = vi.fn();
    render(
      <WeeklyOutlookCard state={{ status: 'error', message: 'Could not load the weekly outlook.' }} onReload={onReload} />
    );

    await userEvent.click(screen.getByRole('button', { name: /try again/i }));

    expect(screen.getByText(/could not load the weekly outlook/i)).toBeInTheDocument();
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['empty_roster', /add players to your roster/i],
    ['no_run', /no model run is available/i],
    ['no_games', /none of your players have projected games/i],
  ] as const)('explains the %s empty state instead of a table', (status, message) => {
    render(
      <WeeklyOutlookCard
        state={{ status: 'ready', data: outlook({ status, categories: [], players: [] }) }}
        onReload={vi.fn()}
      />
    );

    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('renders each category median, band and win probability bar', () => {
    render(<WeeklyOutlookCard state={{ status: 'ready', data: outlook() }} onReload={vi.fn()} />);

    const rows = screen.getAllByRole('row');
    const pts = within(rows[1]);
    expect(pts.getByText('PTS')).toBeInTheDocument();
    expect(pts.getByText('408.9')).toBeInTheDocument();
    expect(pts.getByText('360.4 to 462.1')).toBeInTheDocument();
    expect(pts.getByText('62%')).toBeInTheDocument();
    expect(screen.getByLabelText('PTS win probability')).toHaveAttribute('value', '62');

    const fg = within(rows[2]);
    expect(fg.getByText('48.0%')).toBeInTheDocument();
    expect(fg.getByText('45.2% to 51.0%')).toBeInTheDocument();

    expect(within(rows[3]).getByText(/lower wins/i)).toBeInTheDocument();
  });

  it('renders a miss-risk chip per player and the provenance line', () => {
    render(<WeeklyOutlookCard state={{ status: 'ready', data: outlook() }} onReload={vi.fn()} />);

    expect(screen.getByText('Ann Guard: 24% miss risk')).toBeInTheDocument();
    expect(screen.getByText('Bo Center: no games')).toBeInTheDocument();
    expect(screen.getByTestId('weekly-outlook-provenance')).toHaveTextContent(
      /^Published \w{3} \d{1,2}, \d{1,2}:\d{2}\s[AP]M \S+ · illustrative outlook: category win odds are against a fixed typical opponent$/
    );
    expect(screen.queryByText(/v-test/)).not.toBeInTheDocument();
    expect(screen.getByText(/default spreads used/i)).toHaveTextContent('Ann Guard (blk)');
    expect(screen.getByText('2026-01-15 to 2026-01-21')).toBeInTheDocument();
  });
});
