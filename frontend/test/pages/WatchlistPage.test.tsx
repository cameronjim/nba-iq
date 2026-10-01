import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { WatchlistPage } from '../../src/pages/WatchlistPage';
import type { WatchlistPlayer, WatchlistResponse } from '../../src/types';

vi.mock('../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/client')>();
  return { ...actual, getWatchlist: vi.fn() };
});

const { getWatchlist } = await import('../../src/api/client');
const watchlistMock = vi.mocked(getWatchlist);

const breakout: WatchlistPlayer = {
  nba_player_id: '1630559',
  name: 'Breakout Wing',
  name_is_placeholder: false,
  team_abbr: 'OKC',
  position: 'SG/SF',
  opponent_team_abbr: 'LAL',
  preseason: false,
  nba_game_id: '0022500555',
  game_date: '2026-02-04',
  games_count: 1,
  games: [
    {
      game_date: '2026-02-04',
      nba_game_id: '0022500555',
      opponent_team_abbr: 'LAL',
      preseason: false,
      minutes_p50: 31,
      proj_pts: 20,
      impact: 3.4,
      score: 1.42,
    },
  ],
  score: 1.42,
  score_per_game: 1.42,
  upside: 1.6,
  drivers: [
    { stat: 'minutes', delta: 9, scaled: 2.1 },
    { stat: 'pts', delta: 8.4, scaled: 1.9 },
  ],
  relevance: 0.89,
  impact: 3.4,
  impact_percentile: 96.7,
  prob_active: 0.88,
  minutes: { usual: 22, projected: 31, delta: 9 },
  points: { usual: 11.6, projected: 20, delta: 8.4 },
  totals: { pts: 20 },
  baseline_games: 15,
  reasons: ['ROLE_INCREASE', 'TEAMMATE_ABSENCE'],
  evidence: {
    teammate_out: 'Franchise Player',
    teammate_out_minutes: 34.6,
    teammate_out_prob_active: 0.12,
  },
};

const returnee: WatchlistPlayer = {
  nba_player_id: '2544',
  name: 'Returning Vet',
  name_is_placeholder: false,
  team_abbr: 'LAL',
  position: 'C',
  opponent_team_abbr: 'OKC',
  preseason: false,
  nba_game_id: '0022500555',
  game_date: '2026-02-04',
  games_count: 1,
  games: [
    {
      game_date: '2026-02-04',
      nba_game_id: '0022500555',
      opponent_team_abbr: 'OKC',
      preseason: false,
      minutes_p50: 25.5,
      proj_pts: 15.4,
      impact: 0.9,
      score: 0.31,
    },
  ],
  score: 0.31,
  score_per_game: 0.31,
  upside: 0.62,
  drivers: [{ stat: 'reb', delta: 1.4, scaled: 0.8 }],
  relevance: 0.5,
  impact: 0.9,
  impact_percentile: 85,
  prob_active: null,
  minutes: { usual: 24, projected: 25.5, delta: 1.5 },
  points: { usual: 15, projected: 15.4, delta: 0.4 },
  totals: { pts: 15.4 },
  baseline_games: 11,
  reasons: ['RETURNING_FROM_ABSENCE'],
  evidence: { days_since_played: 9, last_played_date: '2026-01-26' },
};

const weekLong: WatchlistPlayer = {
  ...breakout,
  games_count: 4,
  score: 2.4,
  score_per_game: 0.6,
  impact: 11.2,
  games: [
    {
      game_date: '2026-02-04',
      nba_game_id: '0022500555',
      opponent_team_abbr: 'LAL',
      preseason: false,
      minutes_p50: 31,
      proj_pts: 20,
      impact: 3.4,
      score: 0.9,
    },
    {
      game_date: '2026-02-05',
      nba_game_id: '0022500601',
      opponent_team_abbr: 'DEN',
      preseason: false,
      minutes_p50: 29,
      proj_pts: 18.2,
      impact: 2.8,
      score: 0.7,
    },
    {
      game_date: '2026-02-07',
      nba_game_id: '0022500612',
      opponent_team_abbr: 'PHX',
      preseason: false,
      minutes_p50: 30,
      proj_pts: 19,
      impact: 2.9,
      score: 0.8,
    },
    {
      game_date: '2026-02-09',
      nba_game_id: '0022500620',
      opponent_team_abbr: 'SAC',
      preseason: false,
      minutes_p50: 24,
      proj_pts: 12.1,
      impact: 2.1,
      score: 0,
    },
  ],
  totals: { pts: 69.3 },
};

function payload(overrides: Partial<WatchlistResponse> = {}): WatchlistResponse {
  return {
    date: '2026-02-04',
    window: { from: '2026-02-04', to: '2026-02-04', days: 1 },
    run: { model_version: 'v1-decomposed', predicted_at: '2026-02-04T11:00:00Z' },
    pool: {
      key: 'slate',
      label: "Tonight's slate",
      definition: "every player the run projects for this date, across all of the date's games",
      sample_size: 244,
    },
    baseline: {
      window_games: 15,
      min_games: 5,
      notable_min_delta: 4,
      label: 'his own recent form',
      definition: 'per-game averages over his last 15 games played before this date',
    },
    position: null,
    position_options: ['G', 'F', 'C', 'PG', 'SG', 'SF', 'PF'],
    position_coverage: { known: 244, unknown: 0 },
    players: [breakout, returnee],
    ...overrides,
  };
}

function weekPayload(overrides: Partial<WatchlistResponse> = {}): WatchlistResponse {
  return payload({
    window: { from: '2026-02-04', to: '2026-02-10', days: 7 },
    pool: {
      key: 'slate',
      label: "Each night's slate",
      definition:
        "every player the run projects for a date, across all of that date's games; each night in the window is scored against its own slate",
      sample_size: 1712,
    },
    players: [weekLong],
    ...overrides,
  });
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/watchlist']}>
      <WatchlistPage />
    </MemoryRouter>
  );
}

// the date argument is the real today in eastern, so asserting on it would fail tomorrow.
function lastRequestWindow(): unknown[] {
  return (watchlistMock.mock.calls.at(-1) ?? []).slice(1);
}

beforeEach(() => {
  vi.clearAllMocks();
  watchlistMock.mockResolvedValue(payload());
});

describe('WatchlistPage', () => {
  it('renders the ranked candidates with their scores', async () => {
    renderPage();

    expect(await screen.findByText('Breakout Wing')).toBeInTheDocument();
    expect(screen.getByText('Returning Vet')).toBeInTheDocument();
    expect(screen.getByText('1.42')).toBeInTheDocument();
    expect(screen.getByText('0.31')).toBeInTheDocument();
  });

  it('leads each row with the minutes it is claiming will change', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');

    // the arrow sits in its own span, so the row is compared as text.
    const minutes = screen.getByTitle(/Usually 22\.0 min/);
    expect(minutes.textContent?.replace(/\s+/g, ' ')).toBe('22 → 31 min');
    expect(screen.getByText(/\+8\.4 pts vs usual/)).toBeInTheDocument();
  });

  it('marks a preseason game on the row', async () => {
    watchlistMock.mockResolvedValue(payload({ players: [{ ...breakout, preseason: true }, returnee] }));

    renderPage();
    await screen.findByText('Breakout Wing');

    const preseasonRow = screen.getByText('Breakout Wing').closest('li') as HTMLElement;
    const regularRow = screen.getByText('Returning Vet').closest('li') as HTMLElement;
    expect(within(preseasonRow).getByText('Preseason')).toBeInTheDocument();
    expect(within(regularRow).queryByText('Preseason')).not.toBeInTheDocument();
  });

  it('names the team and the opponent', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');

    const row = screen.getByText('Breakout Wing').closest('li') as HTMLElement;
    expect(within(row).getByText(/OKC/)).toBeInTheDocument();
    expect(within(row).getByText(/LAL/)).toBeInTheDocument();
  });

  it('lists the categories pulling the score up', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');

    expect(screen.getByText(/up vs usual: MIN \+9\.0 · PTS \+8\.4/)).toBeInTheDocument();
    expect(screen.getByText(/up vs usual: REB \+1\.4/)).toBeInTheDocument();
  });

  it('keeps the server ranking and numbers the rows from it', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');

    const rows = screen.getAllByRole('listitem').filter((row) => row.querySelector('details'));
    expect(within(rows[0]).getByText('Breakout Wing')).toBeInTheDocument();
    expect(within(rows[0]).getByText('1')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Returning Vet')).toBeInTheDocument();
    expect(within(rows[1]).getByText('2')).toBeInTheDocument();
  });

  it('labels each reason code as a badge', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');

    expect(screen.getAllByText('Role increase').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('Teammate out').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('Just back').length).toBeGreaterThanOrEqual(2);
  });

  it('explains every reason code in the legend, including unused ones', async () => {
    renderPage();
    await screen.findByRole('heading', { name: /What the badges mean/i });

    expect(screen.getByText(/at least 4 more minutes than usual/i)).toBeInTheDocument();
    expect(screen.getByText(/take more shots than usual/i)).toBeInTheDocument();
    expect(screen.getByText(/back after a week or more out/i)).toBeInTheDocument();
    expect(screen.getByText(/above his usual over his last 5 games/i)).toBeInTheDocument();
    expect(screen.getByText(/teammate who usually plays 28\+ minutes is unlikely to play/i)).toBeInTheDocument();
  });

  it('says the badges explain the ranking rather than being it', async () => {
    renderPage();
    await screen.findByRole('heading', { name: /What the badges mean/i });

    expect(screen.getByText(/Badges explain a row; they do not rank it/i)).toBeInTheDocument();
    expect(screen.queryByText(/tripling his minutes scores nothing/i)).toBeNull();
  });

  it('shows the evidence behind the reasons that fired, and the absolute floor', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');

    const evidence = screen.getByTestId('evidence-1630559');
    expect(
      within(evidence).getByText(/Minutes: 31\.0 projected, usually 22\.0 \(\+9\.0\)/)
    ).toBeInTheDocument();
    expect(
      within(evidence).getByText(/Franchise Player usually plays 34\.6 minutes, 12% to play/)
    ).toBeInTheDocument();
    expect(within(evidence).getByText(/97th percentile of the slate/)).toBeInTheDocument();
    expect(within(evidence).queryByText(/days without a game/)).not.toBeInTheDocument();
    expect(
      within(screen.getByTestId('evidence-2544')).getByText(
        /9 days without a game, last played 2026-01-26/
      )
    ).toBeInTheDocument();
  });

  it('opens a candidate row to reveal its evidence', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');
    const user = userEvent.setup();
    const details = screen.getByTestId('evidence-1630559').closest('details');

    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute('open');
    await user.click(screen.getByText('Breakout Wing'));

    expect(details).toHaveAttribute('open');
  });

  it('shows availability with the same wording as a player upcoming-games table', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');

    expect(screen.getByText('88%')).toBeInTheDocument();
    expect(screen.getByText('No estimate')).toBeInTheDocument();
  });

  it('shows the projected total impact alongside the score', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');

    expect(screen.getByText('+3.4')).toBeInTheDocument();
  });

  it('renders a placeholder name as an id rather than as a person', async () => {
    watchlistMock.mockResolvedValue(
      payload({
        players: [
          {
            ...breakout,
            nba_player_id: '1642850',
            name: 'NBA #1642850 (new roster)',
            name_is_placeholder: true,
            team_abbr: null,
            opponent_team_abbr: null,
          },
        ],
      })
    );

    renderPage();

    expect(await screen.findByText('NBA #1642850 (new roster)')).toBeInTheDocument();
    expect(
      screen.getByTitle(/Not on a roster yet, so this is his NBA id/i)
    ).toBeInTheDocument();
  });

  it('explains that the page needs a run, because it compares against a baseline', async () => {
    watchlistMock.mockResolvedValue(payload({ run: null, players: [] }));

    renderPage();

    expect(
      await screen.findByText('No prediction run yet. Check back after the next model run.')
    ).toBeInTheDocument();
  });

  it('drops the notice once a run has scored the date', async () => {
    renderPage();

    expect(await screen.findByText('Breakout Wing')).toBeInTheDocument();
    expect(screen.queryByText(/No prediction run yet/i)).not.toBeInTheDocument();
  });

  it('calls an empty list a normal answer, and points at the projections', async () => {
    watchlistMock.mockResolvedValue(payload({ players: [] }));

    renderPage();

    expect(
      await screen.findByText('Nobody is projected above their own usual tonight')
    ).toBeInTheDocument();
    expect(screen.getByText(/That is a normal answer/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /The projections/i })).toHaveAttribute(
      'href',
      '/projections'
    );
    expect(screen.getByRole('heading', { name: /What the badges mean/i })).toBeInTheDocument();
  });

  it("names the server's baseline without restating how it is computed", async () => {
    renderPage();
    await screen.findByText('Breakout Wing');

    expect(screen.getByText(/his own recent form/i)).toBeInTheDocument();
    expect(screen.queryByText(/last 15 games played before this date/i)).toBeNull();
    expect(screen.queryByText(/every player the run projects for this date/i)).toBeNull();
    expect(screen.queryByText(/244 player-games/)).toBeNull();
  });

  it('refetches for another date when the picker changes', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');
    watchlistMock.mockResolvedValue(payload({ date: '2026-02-06', players: [] }));

    // a date input is set wholesale, not typed character by character.
    fireEvent.change(screen.getByLabelText('Window start date'), {
      target: { value: '2026-02-06' },
    });

    expect(
      await screen.findByText('Nobody is projected above their own usual tonight')
    ).toBeInTheDocument();
    expect(watchlistMock).toHaveBeenLastCalledWith('2026-02-06', 1, null);
  });

  it('shows an error state with a retry button when the request fails', async () => {
    watchlistMock.mockRejectedValue(new Error('watchlist down'));

    renderPage();

    expect(await screen.findByText(/Failed to load the watchlist/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Try Again/i })).toBeInTheDocument();
  });

  it('shows each position next to the name', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');

    const row = screen.getByText('Breakout Wing').closest('li') as HTMLElement;
    expect(within(row).getByText('SG/SF')).toBeInTheDocument();
    const other = screen.getByText('Returning Vet').closest('li') as HTMLElement;
    expect(within(other).getByText('C')).toBeInTheDocument();
  });

  it('says so rather than guessing when a player has no position on record', async () => {
    watchlistMock.mockResolvedValue(
      payload({ players: [{ ...breakout, position: null }], position_coverage: { known: 243, unknown: 1 } })
    );

    renderPage();
    await screen.findByText('Breakout Wing');

    expect(screen.getByText('pos ?')).toBeInTheDocument();
    expect(
      screen.getByTitle(/No position on record, so position filters skip him/i)
    ).toBeInTheDocument();
  });

  it('hides the games count and the breakdown for a one-night window', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');

    expect(screen.queryByText(/1 game tonight/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId('games-1630559')).not.toBeInTheDocument();
    expect(screen.getByTestId('ranking-note').textContent).toMatch(/where tonight projects/i);
  });
});

describe('WatchlistPage window picker', () => {
  it('offers the four windows a manager actually asks for', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');

    for (const label of ['Tonight', '3 days', 'Week', '2 weeks']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: 'Tonight' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('refetches for the chosen window and shows the range the server resolved', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');
    watchlistMock.mockResolvedValue(weekPayload());
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Week' }));

    expect(lastRequestWindow()).toEqual([7, null]);
    expect(await screen.findByText(/Feb 4 to Feb 10/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Week' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('leads the row with the games count, which is the streaming argument', async () => {
    watchlistMock.mockResolvedValue(weekPayload());

    renderPage();
    await screen.findByText('Breakout Wing');

    const row = screen.getByText('Breakout Wing').closest('li') as HTMLElement;
    expect(within(row).getByText('4 games this week')).toBeInTheDocument();
    expect(within(row).getByText('2.40')).toBeInTheDocument();
  });

  it('says the ranking adds the games up rather than averaging them', async () => {
    watchlistMock.mockResolvedValue(weekPayload());

    renderPage();
    await screen.findByText('Breakout Wing');

    const note = screen.getByTestId('ranking-note');
    expect(note.textContent).toMatch(/added up/i);
    expect(note.textContent).toMatch(/more games can outrank a better player with fewer/i);
  });

  it('breaks the window down game by game, including the ones that add nothing', async () => {
    watchlistMock.mockResolvedValue(weekPayload());

    renderPage();
    await screen.findByText('Breakout Wing');

    const breakdown = screen.getByTestId('games-1630559');
    expect(within(breakdown).getByText('Feb 4')).toBeInTheDocument();
    expect(within(breakdown).getByText('Feb 9')).toBeInTheDocument();
    expect(within(breakdown).getByText('DEN')).toBeInTheDocument();
    expect(within(breakdown).getByText('0.00')).toBeInTheDocument();
    expect(within(breakdown).getAllByRole('row')).toHaveLength(5);
  });

  it('drops the single opponent from the summary when there are several', async () => {
    watchlistMock.mockResolvedValue(weekPayload());

    renderPage();
    await screen.findByText('Breakout Wing');

    const summary = screen.getByText('Breakout Wing').closest('summary') as HTMLElement;
    expect(summary.textContent).not.toMatch(/ vs LAL/);
  });

  it('explains the window total in the row evidence', async () => {
    watchlistMock.mockResolvedValue(weekPayload());

    renderPage();
    await screen.findByText('Breakout Wing');

    const evidence = screen.getByTestId('evidence-1630559');
    expect(
      within(evidence).getByText(/4 games projected, 2\.40 total at 0\.60 a game/)
    ).toBeInTheDocument();
    expect(
      within(evidence).getByText(/projected per game, usually 22\.0/)
    ).toBeInTheDocument();
  });

  it('explains the game-count badge in the legend once a window is chosen', async () => {
    watchlistMock.mockResolvedValue(weekPayload());

    renderPage();
    await screen.findByText('Breakout Wing');

    expect(screen.getByText(/score adds them up, so more games ranks higher/i)).toBeInTheDocument();
  });
});

describe('WatchlistPage position filter', () => {
  it('offers roster slots and exact positions, from the server vocabulary', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');

    const filter = screen.getByTestId('position-filter');
    for (const label of ['All', 'PG', 'SG', 'SF', 'PF', 'C', 'Guards', 'Forwards']) {
      expect(within(filter).getByRole('button', { name: label })).toBeInTheDocument();
    }
    expect(within(filter).getByRole('button', { name: 'All' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
  });

  it('never offers a filter the server did not publish', async () => {
    watchlistMock.mockResolvedValue(payload({ position_options: ['G', 'F', 'C'] }));

    renderPage();
    await screen.findByText('Breakout Wing');

    const filter = screen.getByTestId('position-filter');
    expect(within(filter).getByRole('button', { name: 'Guards' })).toBeInTheDocument();
    expect(within(filter).queryByRole('button', { name: 'PG' })).not.toBeInTheDocument();
  });

  it('refetches for the chosen position and says the filter is on', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');
    watchlistMock.mockResolvedValue(
      weekPayload({ position: 'G', position_coverage: { known: 1700, unknown: 12 } })
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Guards' }));

    expect(lastRequestWindow()).toEqual([1, 'G']);
    expect(await screen.findByTestId('position-note')).toHaveTextContent(/guards only/i);
    expect(screen.getByTestId('position-note')).toHaveTextContent(
      /12 projected players have no position on record/i
    );
  });

  it('gives an empty position filter its own empty state', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');
    watchlistMock.mockResolvedValue(
      weekPayload({ position: 'C', players: [], position_coverage: { known: 1700, unknown: 3 } })
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'C', exact: true }));

    expect(await screen.findByText(/No centers clear the bar in this window/i)).toBeInTheDocument();
    expect(screen.getByText(/Try a longer window, a wider slot/i)).toBeInTheDocument();
    expect(
      screen.getByText(/3 projected players have no position on record/i)
    ).toBeInTheDocument();
    expect(
      screen.queryByText('Nobody is projected above their own usual in this window')
    ).not.toBeInTheDocument();
  });

  it('clears the filter from its own empty state', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');
    const user = userEvent.setup();
    watchlistMock.mockResolvedValue(payload({ position: 'C', players: [] }));
    await user.click(screen.getByRole('button', { name: 'C', exact: true }));
    await screen.findByText(/No centers clear the bar/i);
    watchlistMock.mockResolvedValue(payload());

    await user.click(screen.getByRole('button', { name: 'every position' }));

    expect(await screen.findByText('Breakout Wing')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'All', exact: true })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
  });

  it('explains that combo players answer every one of their positions', async () => {
    renderPage();
    await screen.findByRole('heading', { name: /What the badges mean/i });

    expect(screen.getByText(/shows up under both Guards and PG/i)).toBeInTheDocument();
  });
});

describe('WatchlistPage team filter', () => {
  it('offers only the teams on the current page, alphabetically', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');

    const select = screen.getByRole('combobox', { name: 'Filter by team' });
    const optionLabels = within(select)
      .getAllByRole('option')
      .map((option) => option.textContent);
    expect(optionLabels).toEqual(['All Teams', 'LAL', 'OKC']);
  });

  it('filters the visible rows client-side without refetching', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');
    const callsBefore = watchlistMock.mock.calls.length;
    const user = userEvent.setup();

    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by team' }), 'LAL');

    expect(screen.queryByText('Breakout Wing')).not.toBeInTheDocument();
    expect(screen.getByText('Returning Vet')).toBeInTheDocument();
    expect(watchlistMock.mock.calls.length).toBe(callsBefore);
  });

  it('gives a stale team filter its own empty state, with an escape hatch', async () => {
    renderPage();
    await screen.findByText('Breakout Wing');
    const user = userEvent.setup();
    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by team' }), 'OKC');
    expect(screen.getByText('Breakout Wing')).toBeInTheDocument();

    watchlistMock.mockResolvedValue(payload({ players: [returnee] }));
    await user.click(screen.getByRole('button', { name: 'Week' }));
    await screen.findByText(/No OKC players in this window/i);

    await user.click(screen.getByRole('button', { name: 'Clear the team filter' }));

    expect(await screen.findByText('Returning Vet')).toBeInTheDocument();
  });
});
