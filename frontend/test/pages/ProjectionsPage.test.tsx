import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { ProjectionsPage, WatchlistRedirect } from '../../src/pages/ProjectionsPage';
import type { SlatePlayer, SlateResponse, WatchlistPlayer, WatchlistResponse } from '../../src/types';

vi.mock('../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/client')>();
  return { ...actual, getSlate: vi.fn(), getWatchlist: vi.fn() };
});

const { getSlate, getWatchlist } = await import('../../src/api/client');
const slateMock = vi.mocked(getSlate);
const watchlistMock = vi.mocked(getWatchlist);

function slatePlayer(overrides: Partial<SlatePlayer> = {}): SlatePlayer {
  return {
    nba_player_id: '201939',
    name: 'Stephen Curry',
    name_is_placeholder: false,
    team_abbr: 'GSW',
    prob_active: 0.99,
    proj_pts: 28.4,
    proj_pts_cond: 28.7,
    proj_min_p50: 33.1,
    projected: { reb: 4.6, ast: 6.1, stl: 1.2, blk: 0.3, tov: 2.8, fg3m: 4.4 },
    usual_min: 32.4,
    usual_pts: 26.9,
    min_vs_usual: 0.7,
    pts_vs_usual: 1.5,
    baseline_games: 15,
    impact: 6.2,
    edge: 0.1,
    vs_usual: {
      minutes: { usual: 32.4, projected: 33.1, delta: 0.7 },
      points: { usual: 26.9, projected: 28.4, delta: 1.5 },
      categories: [],
    },
    reasons: [],
    evidence: {},
    spotlight: true,
    slate_spotlight: true,
    ...overrides,
  };
}

const LEBRON = slatePlayer({
  nba_player_id: '2544',
  name: 'LeBron James',
  team_abbr: 'LAL',
  prob_active: 0.42,
  proj_pts: 18.6,
  proj_pts_cond: 26,
  proj_min_p50: 30.5,
  usual_min: 24.3,
  impact: 2.1,
  vs_usual: {
    minutes: { usual: 24.3, projected: 30.5, delta: 6.2 },
    points: { usual: 24.1, projected: 26, delta: 1.9 },
    categories: [{ stat: 'ast', usual: 6.1, projected: 8.4, delta: 2.3 }],
  },
  reasons: ['ROLE_INCREASE', 'TEAMMATE_ABSENCE'],
  evidence: { teammate_out: 'Anthony Davis', teammate_out_minutes: 35.1, teammate_out_prob_active: 0.05 },
  injury_status: 'questionable',
  injury_status_raw: 'GTD',
});

function slate(overrides: Partial<SlateResponse> = {}): SlateResponse {
  return {
    date: '2026-02-04',
    sort: 'impact',
    run: {
      model_version: 'v1-decomposed',
      predicted_at: '2026-02-04T11:00:00Z',
      information_as_of: '2026-02-04T10:45:00Z',
      covers_from: '2026-02-04',
      covers_to: '2026-02-10',
    },
    covered: true,
    pool: { key: 'slate', label: "Tonight's slate", definition: '', sample_size: 2 },
    baseline: { window_games: 15, min_games: 5, notable_min_delta: 4, label: 'his own recent form', definition: '' },
    games: [
      {
        nba_game_id: '0022500555',
        game_status: 'Scheduled',
        home_team_id: '1610612747',
        home_team_abbr: 'LAL',
        away_team_id: '1610612744',
        away_team_abbr: 'GSW',
        preseason: false,
        top_impact: 6.2,
        top_edge: 1.3,
        players: [slatePlayer(), LEBRON],
      },
    ],
    ...overrides,
  };
}

const GUARD: WatchlistPlayer = {
  nba_player_id: '1629630',
  name: 'Windowed Guard',
  name_is_placeholder: false,
  team_abbr: 'MEM',
  position: 'PG/SG',
  game_date: '2026-02-04',
  nba_game_id: '0022500555',
  opponent_team_abbr: 'UTA',
  preseason: false,
  games_count: 2,
  games: [
    { game_date: '2026-02-04', nba_game_id: '0022500555', opponent_team_abbr: 'UTA', preseason: false, minutes_p50: 27, proj_pts: 14.1, impact: 1, score: 0.2 },
    { game_date: '2026-02-06', nba_game_id: '0022500601', opponent_team_abbr: 'GSW', preseason: false, minutes_p50: 28, proj_pts: 14.5, impact: 1, score: 0.2 },
  ],
  score: 0.4,
  score_per_game: 0.2,
  upside: 0.3,
  drivers: [],
  relevance: 0.5,
  impact: 2,
  impact_percentile: 80,
  prob_active: 0.7,
  minutes: { usual: 23, projected: 27.5, delta: 4.5 },
  points: { usual: 15, projected: 20.4, delta: 5.4 },
  totals: {},
  baseline_games: 15,
  reasons: ['ROLE_INCREASE'],
  evidence: {},
};

const FORWARD: WatchlistPlayer = {
  ...GUARD,
  nba_player_id: '1641705',
  name: 'Rested Forward',
  team_abbr: 'BOS',
  position: 'SF',
  reasons: [],
  score: 0.3,
};

function week(overrides: Partial<WatchlistResponse> = {}): WatchlistResponse {
  return {
    date: '2026-02-04',
    window: { from: '2026-02-04', to: '2026-02-10', days: 7 },
    run: { model_version: 'v1-decomposed', predicted_at: '2026-02-04T11:00:00Z' },
    pool: { key: 'slate', label: "Each night's slate", definition: '', sample_size: 1000 },
    baseline: { window_games: 15, min_games: 5, notable_min_delta: 4, label: 'his own recent form', definition: '' },
    position: null,
    position_options: ['G', 'F', 'C', 'PG', 'SG', 'SF', 'PF'],
    position_coverage: { known: 1000, unknown: 0 },
    players: [GUARD, FORWARD],
    ...overrides,
  };
}

const LocationProbe = (): JSX.Element => {
  const location = useLocation();
  return <span data-testid="location">{location.pathname + location.search}</span>;
};

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/projections" element={<ProjectionsPage />} />
        <Route path="/watchlist" element={<WatchlistRedirect />} />
      </Routes>
      <LocationProbe />
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  slateMock.mockResolvedValue(slate());
  watchlistMock.mockResolvedValue(week());
});

describe('ProjectionsPage, tonight', () => {
  it('shows row-shaped skeletons, not a spinner, while loading', () => {
    // arrange
    slateMock.mockReturnValue(new Promise(() => {}));

    // act
    const { container } = renderAt('/projections');

    // assert
    expect(screen.getByRole('status', { name: 'Loading projections' })).toBeInTheDocument();
    expect(container.querySelector('.loading')).toBeNull();
  });

  it('groups the players by game, each on one plain line', async () => {
    // act
    renderAt('/projections');

    // assert
    expect(await screen.findByRole('heading', { name: /GSW.*@.*LAL/ })).toBeInTheDocument();
    expect(screen.getByTestId('line-201939')).toHaveTextContent(
      'Stephen Curry · GSW @ LAL · 29 pts, 33 min if he plays · 99% to play'
    );
    expect(screen.getByTestId('line-2544')).toHaveTextContent(
      'LeBron James · LAL vs GSW · 26 pts, 31 min if he plays · 42% to play'
    );
  });

  it('explains a departure from his usual in one sentence, and stays quiet otherwise', async () => {
    // act
    renderAt('/projections');
    await screen.findByText('Stephen Curry');

    // assert
    expect(screen.getByTestId('note-2544')).toHaveTextContent('Up from his usual 24 min: Anthony Davis is out.');
    expect(screen.queryByTestId('note-201939')).not.toBeInTheDocument();
  });

  it('shows no impact numbers, reason badges, sort controls or legend', async () => {
    // act
    renderAt('/projections');
    await screen.findByText('Stephen Curry');

    // assert
    expect(screen.queryByText('+6.2')).not.toBeInTheDocument();
    expect(screen.queryByText('Role increase')).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Sort players by' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('slate-legend')).not.toBeInTheDocument();
    expect(slateMock).toHaveBeenCalledWith(expect.any(String));
  });

  it('reads the injury chip as a word', async () => {
    // act
    renderAt('/projections');
    await screen.findByText('Stephen Curry');

    // assert
    expect(screen.getByTestId('injury-chip')).toHaveTextContent('Questionable');
  });

  it('opens Details to the category line, the sits sentence and the evidence', async () => {
    // arrange
    renderAt('/projections');
    await screen.findByText('LeBron James');
    const details = screen.getByTestId('details-2544');

    // act
    fireEvent.click(within(details).getByText('Details'));

    // assert
    expect(details).toHaveAttribute('open');
    expect(details).toHaveTextContent('If he plays:');
    expect(details).toHaveTextContent('3PM');
    expect(details).toHaveTextContent('18.6 points averaged over the chance he sits.');
    expect(details).toHaveTextContent('Minutes: 30.5 projected, usually 24.3 (+6.2)');
  });

  it('tags a preseason game in plain text', async () => {
    // arrange
    const base = slate();
    slateMock.mockResolvedValue(slate({ games: [{ ...base.games[0], preseason: true }] }));

    // act
    renderAt('/projections');
    await screen.findByText('Stephen Curry');

    // assert
    expect(screen.getAllByText('Preseason').length).toBeGreaterThan(0);
    expect(
      screen.getAllByTitle(
        'Preseason minutes use a tier prior from four seasons of preseason box scores; stars play about 22 minutes.',
      ).length,
    ).toBeGreaterThan(0);
  });

  it('closes with the publish and injury times, and no model id', async () => {
    // act
    renderAt('/projections');
    await screen.findByText('Stephen Curry');

    // assert
    const footer = screen.getByTestId('projections-footer');
    expect(footer).toHaveTextContent(/^Published Feb 4.*\. Injuries as of Feb 4.*\.$/);
    expect(footer).not.toHaveTextContent('v1-decomposed');
  });

  it('says when the date is past the latest run and lists the games as schedule only', async () => {
    // arrange
    const base = slate();
    slateMock.mockResolvedValue(
      slate({
        date: '2026-10-20',
        covered: false,
        run: base.run && { ...base.run, covers_from: '2026-10-01', covers_to: '2026-10-07' },
        games: base.games.map((g) => ({ ...g, players: [] })),
      })
    );

    // act
    renderAt('/projections');

    // assert
    expect(await screen.findByTestId('coverage-notice')).toHaveTextContent(
      'No projections for Tue, Oct 20 yet. The latest run covers Oct 1 to Oct 7'
    );
    expect(screen.getByTestId('schedule-only')).toHaveTextContent('Projections not published yet.');
  });

  it('shows an empty state for a day with no games that points at the next 7 days', async () => {
    // arrange
    slateMock.mockResolvedValue(slate({ games: [] }));
    const user = userEvent.setup();
    renderAt('/projections');
    await screen.findByText('No games scheduled');

    // act
    await user.click(screen.getByRole('button', { name: 'the next 7 days' }));

    // assert
    expect(await screen.findByText('Windowed Guard')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('/projections?scope=week');
  });

  it('says no run has completed while still listing the games', async () => {
    // arrange
    slateMock.mockResolvedValue(slate({ run: null, covered: false }));

    // act
    renderAt('/projections');

    // assert
    expect(await screen.findByText(/No prediction run yet/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /GSW.*@.*LAL/ })).toBeInTheDocument();
  });

  it('shows an error with a retry', async () => {
    // arrange
    slateMock.mockRejectedValue(new Error('boom'));

    // act
    renderAt('/projections');

    // assert
    expect(await screen.findByText('Failed to load the slate')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try Again' })).toBeInTheDocument();
  });

  it('refetches for another date from the picker', async () => {
    // arrange
    renderAt('/projections');
    await screen.findByText('Stephen Curry');

    // act
    fireEvent.change(screen.getByLabelText('Game date'), { target: { value: '2026-02-05' } });

    // assert
    expect(slateMock).toHaveBeenLastCalledWith('2026-02-05');
  });
});

describe('ProjectionsPage, next 7 days', () => {
  it('lists the players flat, in the server order, with per-game numbers', async () => {
    // act
    renderAt('/projections?scope=week');

    // assert
    const list = await screen.findByTestId('week-list');
    const rows = within(list).getAllByTestId(/^row-/);
    expect(rows[0]).toHaveTextContent('Windowed Guard');
    expect(rows[1]).toHaveTextContent('Rested Forward');
    expect(screen.getByTestId('line-1629630')).toHaveTextContent(
      'Windowed Guard · MEM, 2 games · 20 pts, 28 min a game if he plays · 70% to play'
    );
    expect(watchlistMock).toHaveBeenCalledWith(expect.any(String), 7, null);
    expect(slateMock).not.toHaveBeenCalled();
  });

  it('keeps the date picker under Tonight only', async () => {
    // act
    renderAt('/projections?scope=week');
    await screen.findByText('Windowed Guard');

    // assert
    expect(screen.queryByLabelText('Game date')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next 7 days' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('opens Details to the game-by-game breakdown', async () => {
    // arrange
    renderAt('/projections?scope=week');
    await screen.findByText('Windowed Guard');

    // act
    fireEvent.click(within(screen.getByTestId('details-1629630')).getByText('Details'));

    // assert
    expect(within(screen.getByTestId('games-1629630')).getAllByRole('row')).toHaveLength(3);
  });

  it('refetches for a position and filters teams in the browser', async () => {
    // arrange
    const user = userEvent.setup();
    renderAt('/projections?scope=week');
    await screen.findByText('Windowed Guard');

    // act
    await user.selectOptions(screen.getByLabelText('Filter by team'), 'BOS');

    // assert
    expect(screen.queryByText('Windowed Guard')).not.toBeInTheDocument();
    expect(screen.getByText('Rested Forward')).toBeInTheDocument();
    expect(watchlistMock).toHaveBeenCalledTimes(1);

    // act
    await user.selectOptions(screen.getByLabelText('Filter by position'), 'G');

    // assert
    expect(watchlistMock).toHaveBeenLastCalledWith(expect.any(String), 7, 'G');
  });

  it('gives an empty window its own plain sentence', async () => {
    // arrange
    watchlistMock.mockResolvedValue(week({ players: [] }));

    // act
    renderAt('/projections?scope=week');

    // assert
    expect(
      await screen.findByText('Nobody is projected above their own usual in this window')
    ).toBeInTheDocument();
  });

  it('closes with the publish time only, since the week has no injury cutoff', async () => {
    // act
    renderAt('/projections?scope=week');
    await screen.findByText('Windowed Guard');

    // assert
    const footer = screen.getByTestId('projections-footer');
    expect(footer).toHaveTextContent(/^Published Feb 4.*\.$/);
    expect(footer).not.toHaveTextContent('Injuries');
  });
});

describe('the old watchlist route', () => {
  it('redirects to the next 7 days', async () => {
    // act
    renderAt('/watchlist');

    // assert
    expect(await screen.findByText('Windowed Guard')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('/projections?scope=week');
  });
});
