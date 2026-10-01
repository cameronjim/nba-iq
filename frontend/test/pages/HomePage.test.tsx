import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { HomePage } from '../../src/pages/HomePage';
import { clearCachedResources } from '../../src/api/resourceCache';
import type { SlateGame, SlatePlayer, SlateResponse } from '../../src/types';

vi.mock('../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/client')>();
  return {
    ...actual,
    getPlayers: vi.fn(),
    getTeams: vi.fn(),
    getGames: vi.fn(),
    getLiveGames: vi.fn(),
    getSlate: vi.fn(),
    getTeamAnalysis: vi.fn(),
    getWaiverSuggestions: vi.fn(),
    chatWithAI: vi.fn(),
  };
});

const client = await import('../../src/api/client');
const playersMock = vi.mocked(client.getPlayers);
const teamsMock = vi.mocked(client.getTeams);
const gamesMock = vi.mocked(client.getGames);
const liveGamesMock = vi.mocked(client.getLiveGames);
const slateMock = vi.mocked(client.getSlate);

function slatePlayer(overrides: Partial<SlatePlayer>): SlatePlayer {
  return {
    nba_player_id: '1',
    name: 'Player',
    name_is_placeholder: false,
    team_abbr: 'OKC',
    prob_active: 0.95,
    proj_pts: 20,
    proj_pts_cond: 21.4,
    proj_min_p50: 30,
    projected: { reb: 5.2, ast: 4.1, stl: 1, blk: 0.5, tov: 2, fg3m: 2 },
    usual_min: 30,
    usual_pts: 20,
    min_vs_usual: 0,
    pts_vs_usual: 0,
    baseline_games: 15,
    impact: 1,
    edge: 0,
    vs_usual: null,
    reasons: [],
    evidence: {},
    spotlight: false,
    slate_spotlight: false,
    ...overrides,
  };
}

function slateGame(players: SlatePlayer[]): SlateGame {
  return {
    nba_game_id: '0022600001',
    game_status: 'Scheduled',
    home_team_id: '1',
    home_team_abbr: 'OKC',
    away_team_id: '2',
    away_team_abbr: 'DEN',
    preseason: false,
    top_impact: null,
    top_edge: null,
    players,
  };
}

function slateResponse(games: SlateGame[]): SlateResponse {
  return {
    date: '2026-10-01',
    sort: 'impact',
    run: null,
    covered: games.length > 0,
    pool: { key: 'rotation', label: 'Rotation players', definition: '', sample_size: 0 },
    baseline: {
      window_games: 15,
      min_games: 5,
      notable_min_delta: 4,
      label: 'his own recent form',
      definition: '',
    },
    games,
  };
}

function renderHome(isLoggedIn: boolean) {
  return render(
    <MemoryRouter>
      <HomePage isLoggedIn={isLoggedIn} />
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  clearCachedResources();
  playersMock.mockResolvedValue([]);
  teamsMock.mockResolvedValue([]);
  gamesMock.mockResolvedValue([]);
  liveGamesMock.mockResolvedValue([]);
  slateMock.mockResolvedValue(slateResponse([]));
});

describe('HomePage', () => {
  it('lists every section as a link in the index', () => {
    // arrange
    const sections: Array<[string, string]> = [
      ['Stats', '/stats'],
      ['Projections', '/projections'],
      ['History', '/history'],
      ['2K Ratings', '/ratings'],
      ['My Team', '/fantasy'],
      ['Improve Team', '/improve'],
      ['Betting', '/betting'],
    ];

    // act
    renderHome(true);

    // assert
    for (const [title, to] of sections) {
      expect(screen.getByRole('link', { name: title })).toHaveAttribute('href', to);
    }
  });

  it('shows a skeleton while the slate loads', () => {
    // arrange
    slateMock.mockReturnValue(new Promise(() => {}));

    // act
    renderHome(true);

    // assert
    expect(screen.getByRole('status', { name: 'Loading projections' })).toBeInTheDocument();
  });

  it('renders the top projected players with their opponent', async () => {
    // arrange
    slateMock.mockResolvedValue(
      slateResponse([
        slateGame([
          slatePlayer({ nba_player_id: '1', name: 'Bench Guy', impact: 0.4 }),
          slatePlayer({ nba_player_id: '2', name: 'Shai Gilgeous-Alexander', impact: 8.2, proj_pts_cond: 31.5 }),
          slatePlayer({ nba_player_id: '3', name: 'Nikola Jokic', team_abbr: 'DEN', impact: 7.1 }),
        ]),
      ])
    );

    // act
    renderHome(true);

    // assert
    const [table] = await screen.findAllByRole('table');
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText('Shai Gilgeous-Alexander')).toBeInTheDocument();
    expect(within(rows[0]).getByText('vs DEN')).toBeInTheDocument();
    expect(within(rows[0]).getByText('31.5')).toBeInTheDocument();
    expect(within(rows[0]).getByText('+8.2')).toBeInTheDocument();
    expect(within(rows[1]).getByText('@ OKC')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Full slate' })).toHaveAttribute('href', '/projections');
  });

  it('shows a one-line empty state when there is no slate', async () => {
    // arrange
    slateMock.mockResolvedValue(slateResponse([]));

    // act
    renderHome(true);

    // assert
    expect(await screen.findByText(/No projections for/)).toBeInTheDocument();
  });

  it('shows an error line when the slate fails to load', async () => {
    // arrange
    slateMock.mockRejectedValue(new Error('boom'));

    // act
    renderHome(true);

    // assert
    expect(await screen.findByText(/Failed to load the slate/)).toBeInTheDocument();
  });

  it('does not show a scoring leaders table', async () => {
    // arrange
    renderHome(true);

    // act
    await screen.findByText(/No projections for/);

    // assert
    expect(screen.queryByRole('heading', { name: /scoring leaders/i })).not.toBeInTheDocument();
  });

  it('never calls an AI endpoint', async () => {
    // arrange
    renderHome(true);

    // act
    await screen.findByText(/No projections for/);

    // assert
    expect(client.getTeamAnalysis).not.toHaveBeenCalled();
    expect(client.getWaiverSuggestions).not.toHaveBeenCalled();
    expect(client.chatWithAI).not.toHaveBeenCalled();
  });

  it('shows the sign-in prompt as a plain line when logged out', () => {
    // arrange + act
    renderHome(false);

    // assert
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/login');
    expect(screen.getByText(/to track your fantasy roster/)).toBeInTheDocument();
  });

  it('hides the sign-in prompt when logged in', () => {
    // arrange + act
    renderHome(true);

    // assert
    expect(screen.queryByText(/to track your fantasy roster/)).not.toBeInTheDocument();
  });

  it('prefetches players and teams on mount', () => {
    // arrange + act
    renderHome(true);

    // assert
    expect(playersMock).toHaveBeenCalled();
    expect(teamsMock).toHaveBeenCalled();
  });
});
