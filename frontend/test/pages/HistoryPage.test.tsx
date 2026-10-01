import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HistoryPage } from '../../src/pages/HistoryPage';
import type { PlayerSeasonRow } from '../../src/types';

vi.mock('../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/client')>();
  return {
    ...actual,
    getHistorySeasons: vi.fn(),
    getHistoryPlayers: vi.fn(),
  };
});

const { getHistorySeasons, getHistoryPlayers } = await import('../../src/api/client');
const seasonsMock = vi.mocked(getHistorySeasons);
const playersMock = vi.mocked(getHistoryPlayers);

function row(overrides: Partial<PlayerSeasonRow> = {}): PlayerSeasonRow {
  return {
    nba_player_id: '977',
    player_name: 'Kobe Bryant',
    season: '2005-06',
    team: 'LAL',
    games_played: 80,
    minutes_per_game: 41.0,
    points_per_game: 35.4,
    rebounds_per_game: 5.3,
    assists_per_game: 4.5,
    steals_per_game: 1.8,
    blocks_per_game: 0.4,
    turnovers_per_game: 3.1,
    field_goal_percentage: 45.0,
    three_point_percentage: 34.7,
    free_throw_percentage: 85.0,
    three_pointers_made: 2.3,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  seasonsMock.mockResolvedValue(['2005-06', '1996-97']);
  playersMock.mockResolvedValue({
    season: '2005-06',
    total: 2,
    players: [
      row(),
      row({ nba_player_id: '2544', player_name: 'LeBron James', team: 'CLE', points_per_game: 31.4 }),
    ],
  });
});

describe('HistoryPage', () => {
  it('renders the newest season by default with its player rows', async () => {
    render(<HistoryPage />);

    expect(await screen.findByText('Kobe Bryant')).toBeInTheDocument();
    expect(screen.getByText('LeBron James')).toBeInTheDocument();
    expect(screen.getByLabelText('Season')).toHaveValue('2005-06');
    expect(playersMock).toHaveBeenCalledWith(expect.objectContaining({ season: '2005-06' }));
  });

  it('refetches when the user switches season', async () => {
    render(<HistoryPage />);
    await screen.findByText('Kobe Bryant');
    playersMock.mockResolvedValue({
      season: '1996-97',
      total: 1,
      players: [row({ nba_player_id: '893', player_name: 'Michael Jordan', season: '1996-97', team: 'CHI' })],
    });
    const user = userEvent.setup();

    await user.selectOptions(screen.getByLabelText('Season'), '1996-97');

    expect(await screen.findByText('Michael Jordan')).toBeInTheDocument();
    expect(playersMock).toHaveBeenCalledWith(expect.objectContaining({ season: '1996-97' }));
  });

  it('filters the rendered rows by the search box', async () => {
    render(<HistoryPage />);
    await screen.findByText('Kobe Bryant');
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Search players'), 'lebron');

    expect(screen.getByText('LeBron James')).toBeInTheDocument();
    expect(screen.queryByText('Kobe Bryant')).not.toBeInTheDocument();
  });

  it('keeps rendering rows after sorting by a stat column', async () => {
    render(<HistoryPage />);
    await screen.findByText('Kobe Bryant');
    const user = userEvent.setup();

    await user.click(screen.getByTitle('Points Per Game'));
    await user.click(screen.getByTitle('Points Per Game'));
    await user.click(screen.getByTitle('Player Name'));

    expect(screen.getByText('Kobe Bryant')).toBeInTheDocument();
    expect(screen.getByText('LeBron James')).toBeInTheDocument();
  });

  it('shows a table skeleton while the season rows load', async () => {
    playersMock.mockReturnValue(new Promise(() => {}));

    render(<HistoryPage />);

    expect(await screen.findByRole('status', { name: 'Loading season stats' })).toBeInTheDocument();
  });

  it('shows the no-data state and skips the season fetch when no seasons exist', async () => {
    seasonsMock.mockResolvedValue([]);

    render(<HistoryPage />);

    expect(await screen.findByText(/No historical data available yet/i)).toBeInTheDocument();
    expect(screen.queryByLabelText('Season')).not.toBeInTheDocument();
    expect(playersMock).not.toHaveBeenCalled();
  });

  it('shows an error state with a retry button when the season list fails', async () => {
    seasonsMock.mockRejectedValue(new Error('history down'));

    render(<HistoryPage />);

    expect(await screen.findByText(/Failed to load historical seasons/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Try Again/i })).toBeInTheDocument();
  });

  it('shows an error state when the season table fails to load', async () => {
    playersMock.mockRejectedValue(new Error('season down'));

    render(<HistoryPage />);

    expect(await screen.findByText(/Failed to load season stats/i)).toBeInTheDocument();
  });

  it('coerces string numerics and renders dashes for null stats', async () => {
    // pg serializes numeric columns as strings, hence the string values here.
    playersMock.mockResolvedValue({
      season: '1996-97',
      total: 1,
      players: [
        {
          nba_player_id: '893',
          player_name: 'Michael Jordan',
          season: '1996-97',
          team: 'CHI',
          games_played: '82',
          minutes_per_game: '37.90',
          points_per_game: '29.60',
          rebounds_per_game: '5.90',
          assists_per_game: '4.30',
          steals_per_game: null,
          blocks_per_game: null,
          turnovers_per_game: null,
          field_goal_percentage: '48.60',
          three_point_percentage: null,
          free_throw_percentage: '83.30',
          three_pointers_made: null,
        },
      ],
    });

    const { container } = render(<HistoryPage />);
    await screen.findByText('Michael Jordan');

    expect(screen.getByText('29.6')).toBeInTheDocument();
    expect(screen.getByText('82')).toBeInTheDocument();
    await waitFor(() => {
      expect(container.textContent).not.toMatch(/NaN|null|undefined/);
    });
  });
});
