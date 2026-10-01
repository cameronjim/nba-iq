import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { PlayerPage } from '../../src/pages/PlayerPage';
import type { PlayerAnalytics, PlayerPredictionsResponse } from '../../src/types';

vi.mock('../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/client')>();
  return {
    ...actual,
    getPlayerAnalytics: vi.fn(),
    getPlayerPredictions: vi.fn(),
  };
});

const { getPlayerAnalytics, getPlayerPredictions } = await import('../../src/api/client');
const analyticsMock = vi.mocked(getPlayerAnalytics);
const predictionsMock = vi.mocked(getPlayerPredictions);

function predictionsPayload(
  overrides: Partial<PlayerPredictionsResponse> = {}
): PlayerPredictionsResponse {
  return { player_id: 1, nba_player_id: '2544', run: null, stats: [], games: [], ...overrides };
}

function fullPayload(overrides: Partial<PlayerAnalytics> = {}): PlayerAnalytics {
  return {
    player: {
      id: 1,
      nba_id: '2544',
      name: 'Test Allstar',
      team: 'LAL',
      position: 'PG',
      headshot_url: null,
    },
    as_of: { logs: '2026-02-04T12:00:00Z', distributions: '2026-02-04T13:00:00Z' },
    pool: {
      key: 'rotation',
      label: 'Rotation players',
      definition: 'GP >= 15 and MPG >= 12 this season',
      sample_size: 312,
    },
    percentiles: [
      { stat: 'pts', value: 28.5, percentile: 94 },
      { stat: 'tov', value: 3.1, percentile: 22 },
      { stat: 'fg_impact', value: 1.8, percentile: 81 },
      { stat: 'ft_impact', value: 0.6, percentile: 64 },
    ],
    distributions: [
      {
        stat: 'pts',
        mean: 14.2,
        stddev: 5.6,
        player_value: 28.5,
        buckets: [
          { lo: 0, hi: 10, count: 90 },
          { lo: 10, hi: 20, count: 150 },
          { lo: 20, hi: 30, count: 60 },
          { lo: 30, hi: 40, count: 12 },
        ],
      },
      {
        stat: 'reb',
        mean: 5.1,
        stddev: 2.4,
        player_value: 7.1,
        buckets: [
          { lo: 0, hi: 5, count: 140 },
          { lo: 5, hi: 10, count: 150 },
        ],
      },
    ],
    trends: {
      games: [
        {
          game_date: '2026-02-01',
          opponent_team_abbr: 'BOS',
          is_home: true,
          minutes: 35,
          pts: 31,
          reb: 8,
          ast: 9,
          stl: 2,
          blk: 1,
          tov: 3,
          fgm: 11,
          fga: 21,
          fg3m: 3,
          fg3a: 7,
          ftm: 6,
          fta: 7,
        },
        {
          game_date: '2026-02-03',
          opponent_team_abbr: 'GSW',
          is_home: false,
          minutes: 33,
          pts: 24,
          reb: 6,
          ast: 7,
          stl: 1,
          blk: 0,
          tov: 4,
          fgm: 9,
          fga: 19,
          fg3m: 2,
          fg3a: 6,
          ftm: 4,
          fta: 4,
        },
      ],
      rolling: [
        { game_date: '2026-02-01', min_r5: 34.2, pts_r5: 27.5, pts_r10: 26.9, reb_r5: 7.0, ast_r5: 8.1 },
        { game_date: '2026-02-03', min_r5: 34.0, pts_r5: 28.1, pts_r10: 27.2, reb_r5: 7.2, ast_r5: 8.3 },
      ],
      last10_vs_season: [
        { stat: 'pts', last10: 31.2, season: 28.5, delta: 2.7, z: 1.6 },
        { stat: 'blk', last10: 0.9, season: 0.6, delta: 0.3, z: null },
      ],
    },
    prediction: null,
    ...overrides,
  };
}

function renderPage(playerId = '1') {
  return render(
    <MemoryRouter initialEntries={[`/player/${playerId}`]}>
      <Routes>
        <Route path="/player/:id" element={<PlayerPage />} />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  analyticsMock.mockResolvedValue(fullPayload());
  predictionsMock.mockResolvedValue(predictionsPayload());
});

describe('PlayerPage', () => {
  it('renders the header, percentiles, distribution, trends and recent games from a full payload', async () => {
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Test Allstar' })).toBeInTheDocument();
    expect(screen.getByText(/LAL.*PG/)).toBeInTheDocument();
    expect(analyticsMock).toHaveBeenCalledWith(1);

    expect(screen.getByRole('heading', { name: /Category Percentiles/i })).toBeInTheDocument();
    expect(screen.getByLabelText('PTS percentile')).toHaveValue(94);
    expect(screen.getByText('FG Impact')).toBeInTheDocument();
    expect(screen.getByText('FT Impact')).toBeInTheDocument();

    expect(screen.getByRole('heading', { name: /Distribution/i })).toBeInTheDocument();
    expect(screen.getByTestId('distribution-chart')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Trends/i })).toBeInTheDocument();
    expect(screen.getByTestId('trend-chart')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Recent Games/i })).toBeInTheDocument();
    expect(screen.getByText('BOS')).toBeInTheDocument();
    expect(screen.getByText('GSW')).toBeInTheDocument();
  });

  it('shows the pool label, definition and sample size next to the percentile bars', async () => {
    renderPage();

    const poolLine = await screen.findByText(/^vs rotation players/i);
    expect(poolLine).toHaveTextContent('GP >= 15 and MPG >= 12 this season');
    expect(poolLine).toHaveTextContent('n=312');
  });

  it('marks a last-10 swing beyond one standard deviation and flags a null z as a small sample', async () => {
    renderPage();

    expect(await screen.findByText('+2.7')).toBeInTheDocument();
    expect(screen.getByText(/small sample/i)).toBeInTheDocument();
  });

  it('switches the histogram when another distribution stat is picked', async () => {
    renderPage();
    await screen.findByRole('heading', { name: /Distribution/i });
    const user = userEvent.setup();
    // both the distribution and trends sections have a REB tab, so scope the query.
    const tablist = within(screen.getByRole('tablist', { name: 'Distribution stat' }));

    await user.click(tablist.getByRole('tab', { name: 'REB' }));

    expect(tablist.getByRole('tab', { name: 'REB' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('distribution-chart')).toBeInTheDocument();
  });

  it('switches the trend chart when another trend stat is picked', async () => {
    renderPage();
    await screen.findByRole('heading', { name: /Trends/i });
    const user = userEvent.setup();
    const tablist = within(screen.getByRole('tablist', { name: 'Trend stat' }));

    for (const label of ['PTS', 'REB', 'AST', 'STL', 'BLK', '3PM', 'TOV', 'MIN']) {
      expect(tablist.getByRole('tab', { name: label })).toBeInTheDocument();
    }
    expect(tablist.getByRole('tab', { name: 'PTS' })).toHaveAttribute('aria-selected', 'true');

    await user.click(tablist.getByRole('tab', { name: 'BLK' }));

    expect(tablist.getByRole('tab', { name: 'BLK' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('trend-chart')).toBeInTheDocument();
  });

  it('degrades to percentiles only when the player has no game logs', async () => {
    analyticsMock.mockResolvedValue(
      fullPayload({
        as_of: { logs: null, distributions: '2026-02-04T13:00:00Z' },
        trends: { games: [], rolling: [], last10_vs_season: [] },
      })
    );

    renderPage();

    expect(await screen.findByRole('heading', { name: /Category Percentiles/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Distribution/i })).toBeInTheDocument();
    expect(screen.getByText('No game logs yet')).toBeInTheDocument();
    expect(screen.queryByTestId('trend-chart')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /Recent Games/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Game logs as of no game logs yet/i)).toBeInTheDocument();
  });

  it('shows skeletons instead of a spinner while the analytics load', () => {
    analyticsMock.mockReturnValue(new Promise(() => {}));

    const { container } = renderPage();

    expect(screen.getByRole('status', { name: 'Loading player' })).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Loading player stats' })).toBeInTheDocument();
    expect(container.querySelector('.loading-spinner')).toBeNull();
  });

  it('omits the prediction card while the api returns a null prediction', async () => {
    renderPage();
    await screen.findByRole('heading', { name: 'Test Allstar' });

    expect(screen.queryByRole('heading', { name: /Projection/i })).not.toBeInTheDocument();
  });

  it('renders the prediction card once the api returns one', async () => {
    analyticsMock.mockResolvedValue(
      fullPayload({
        prediction: {
          summary: 'Usage should hold with Doncic out.',
          projected: { pts: 30.1, reb: 7.4 },
          confidence: 'medium',
          as_of: '2026-02-04T14:00:00Z',
        },
      })
    );

    renderPage();

    expect(await screen.findByRole('heading', { name: 'Model projection' })).toBeInTheDocument();
    expect(screen.getByText(/Usage should hold/i)).toBeInTheDocument();
    expect(screen.getByText('30.1')).toBeInTheDocument();
    expect(screen.getByText(/medium confidence/i)).toBeInTheDocument();
  });

  it('renders quantile bands, play probability and the schedule-adjusted line', async () => {
    analyticsMock.mockResolvedValue(
      fullPayload({
        prediction: {
          summary: '82% to play, 34.5 min (26.0-41.0), 24.5 pts if he plays.',
          projected: {
            minutes: { p10: 26, p50: 34.5, p90: 41 },
            pts: { p10: 14, p50: 24.5, p90: 37 },
            ast: 8,
            reb: null,
          },
          prob_active: 0.82,
          conditional: true,
          unconditional_pts: 20.5,
          game_date: '2026-03-02',
          model_version: '2026-02-28',
          as_of: '2026-03-01T13:30:00Z',
        },
      })
    );

    renderPage();

    expect(await screen.findByRole('heading', { name: /Projection/i })).toBeInTheDocument();
    expect(screen.getByText('34.5')).toBeInTheDocument();
    expect(screen.getByText('26.0-41.0')).toBeInTheDocument();
    expect(screen.getByText('24.5')).toBeInTheDocument();
    expect(screen.getByText('14.0-37.0')).toBeInTheDocument();
    expect(screen.getByText('82% to play')).toBeInTheDocument();
    expect(screen.getByText(/Points, counting the chance he sits/i)).toBeInTheDocument();
    expect(screen.getByText('20.5')).toBeInTheDocument();
    expect(screen.getByText(/stat lines assume he plays/i)).toBeInTheDocument();
  });

  it('asks for the upcoming games without a date filter', async () => {
    renderPage();
    await screen.findByRole('heading', { name: 'Test Allstar' });

    expect(predictionsMock).toHaveBeenCalledWith(1);
  });

  it('renders the upcoming-games section under the projection card', async () => {
    predictionsMock.mockResolvedValue(
      predictionsPayload({
        run: {
          id: 1,
          model_version: 'bt20260115',
          feature_version: 'v3',
          predicted_at: '2026-08-17T22:08:18.285Z',
          forecast_cutoff_at: '2026-01-15T00:00:00.000Z',
          horizon: 'gameday (T-6h)',
        },
        stats: ['minutes', 'pts'],
        games: [
          {
            nba_game_id: '0022500586',
            game_date: '2026-01-15',
            opponent_abbr: 'CHA',
            is_home: true,
            game_status: 'Final',
            prob_active: 0.91,
            prob_active_model: 0.91,
            stats: {
              minutes: { expected: 36.3, p10: 28.5, p50: 36.2, p90: 43.5, unconditional: 33.2 },
              pts: { expected: 32.3, p10: 25.8, p50: 31.7, p90: 39.9, unconditional: 29.6 },
            },
          },
          {
            nba_game_id: '0022500601',
            game_date: '2026-01-17',
            opponent_abbr: 'POR',
            is_home: false,
            game_status: 'Final',
            prob_active: 0.12,
            prob_active_model: 0.12,
            stats: {
              minutes: { expected: 30, p10: 22, p50: 30, p90: 38, unconditional: 3.6 },
              pts: { expected: 27, p10: 19, p50: 27, p90: 35, unconditional: 3.2 },
            },
          },
        ],
      })
    );
    analyticsMock.mockResolvedValue(
      fullPayload({
        prediction: { summary: 'Full go tonight.', projected: { pts: 32.3 }, game_date: '2026-01-15' },
      })
    );

    renderPage();

    expect(await screen.findByTestId('upcoming-games-section')).toBeInTheDocument();
    expect(screen.getAllByTestId('upcoming-game-row')).toHaveLength(2);
    expect(screen.getByText('Likely')).toBeInTheDocument();
    expect(screen.getByText('OUT-ish')).toBeInTheDocument();

    expect(screen.queryByRole('link', { name: /games in this run/i })).toBeNull();
  });

  it('shows the no-run empty state rather than hiding the section', async () => {
    renderPage();

    expect(await screen.findByText('No prediction run published yet')).toBeInTheDocument();
  });

  it('leaves the page intact when the predictions call fails', async () => {
    predictionsMock.mockRejectedValue(new Error('predictions down'));

    renderPage();

    expect(await screen.findByRole('heading', { name: 'Test Allstar' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Category Percentiles/i })).toBeInTheDocument();
    expect(screen.queryByTestId('upcoming-games-section')).not.toBeInTheDocument();
  });

  it('shows an error state with a retry button when the analytics call fails', async () => {
    analyticsMock.mockRejectedValue(new Error('analytics down'));

    renderPage();

    expect(await screen.findByText(/Failed to load player analytics/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Try Again/i })).toBeInTheDocument();
  });

  it('rejects a non-numeric player id without calling the api', async () => {
    renderPage('not-a-player');

    expect(await screen.findByText(/Unknown player/i)).toBeInTheDocument();
    expect(analyticsMock).not.toHaveBeenCalled();
  });
});
