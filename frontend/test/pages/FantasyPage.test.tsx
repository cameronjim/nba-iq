import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { FantasyPage } from '../../src/pages/FantasyPage';
import type { RosterPlayer } from '../../src/types';

vi.mock('../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/client')>();
  return {
    ...actual,
    getMyRoster: vi.fn(),
    getTeamAnalysis: vi.fn(),
    getWeeklyOutlook: vi.fn(),
    getPreferences: vi.fn(),
    getPlayers: vi.fn(),
  };
});

const client = await import('../../src/api/client');

const guard = {
  id: 1,
  player_id: 1,
  roster_id: 10,
  name: 'Roster Guard',
  team: 'LAL',
  position: 'PG',
  points_per_game: 22,
  rebounds_per_game: 4,
  assists_per_game: 8,
  steals_per_game: 1.5,
  blocks_per_game: 0.1,
  field_goal_percentage: 46,
  free_throw_percentage: 85,
  three_pointers_made: 2.5,
  turnovers_per_game: 2.8,
  injury_status: null,
  headshot_url: null,
} as unknown as RosterPlayer;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.getMyRoster).mockResolvedValue([guard]);
  vi.mocked(client.getWeeklyOutlook).mockReturnValue(new Promise(() => {}));
  vi.mocked(client.getPreferences).mockResolvedValue({} as Awaited<ReturnType<typeof client.getPreferences>>);
});

describe('FantasyPage', () => {
  it('shows skeleton lines and a plain status while Claude reads the roster', async () => {
    // arrange
    vi.mocked(client.getTeamAnalysis).mockReturnValue(new Promise(() => {}));

    // act
    const { container } = render(
      <MemoryRouter>
        <FantasyPage isLoggedIn />
      </MemoryRouter>
    );

    // assert
    expect(await screen.findByRole('status', { name: /loading roster analysis/i })).toBeInTheDocument();
    expect(screen.getByText(/claude is reading your roster/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /claude's read on your roster/i })).toBeInTheDocument();
    expect(container.querySelector('.loading')).toBeNull();
  });

  it('rates categories in plain text and lists the findings', async () => {
    // arrange
    vi.mocked(client.getTeamAnalysis).mockResolvedValue({
      categories: { PTS: 'strong', REB: 'weak' },
      strengths: ['Scoring is 12% above the benchmark'],
      weaknesses: ['Rebounding trails the benchmark'],
      suggestions: ['Target a big'],
    } as Awaited<ReturnType<typeof client.getTeamAnalysis>>);

    // act
    render(
      <MemoryRouter>
        <FantasyPage isLoggedIn />
      </MemoryRouter>
    );

    // assert
    expect(await screen.findByText('Scoring is 12% above the benchmark')).toBeInTheDocument();
    expect(screen.getByText('strong')).toHaveClass('text-success');
    expect(screen.getByText('weak')).toHaveClass('text-error');
  });
});
