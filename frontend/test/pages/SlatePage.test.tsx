import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { SlatePage } from '../../src/pages/SlatePage';
import type { SlatePlayer, SlateResponse, SlateRun } from '../../src/types';

const RUN: SlateRun = {
  model_version: 'v1-decomposed',
  predicted_at: '2026-02-04T11:00:00Z',
  information_as_of: '2026-02-04T10:45:00Z',
  covers_from: '2026-02-04',
  covers_to: '2026-02-10',
};

vi.mock('../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/client')>();
  return { ...actual, getSlate: vi.fn() };
});

const { getSlate } = await import('../../src/api/client');
const slateMock = vi.mocked(getSlate);

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

function payload(overrides: Partial<SlateResponse> = {}): SlateResponse {
  return {
    date: '2026-02-04',
    sort: 'impact',
    run: RUN,
    covered: true,
    pool: {
      key: 'slate',
      label: "Tonight's slate",
      definition: "every player the run projects for this date, across all of the date's games",
      sample_size: 2,
    },
    baseline: {
      window_games: 15,
      min_games: 5,
      notable_min_delta: 4,
      label: 'his own recent form',
      definition: 'per-game averages over his last 15 games played before this date',
    },
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
        players: [
          slatePlayer(),
          slatePlayer({
            nba_player_id: '2544',
            name: 'LeBron James',
            team_abbr: 'LAL',
            prob_active: 0.42,
            proj_pts: 18.6,
            proj_pts_cond: 26,
            proj_min_p50: 30.5,
            projected: { reb: 7.2, ast: 8.4, stl: 0.9, blk: 0.5, tov: 3.4, fg3m: 1.6 },
            usual_min: 24.3,
            usual_pts: 24.1,
            min_vs_usual: 6.2,
            pts_vs_usual: -5.5,
            baseline_games: 15,
            impact: 2.1,
            edge: 1.3,
            vs_usual: {
              minutes: { usual: 24.3, projected: 30.5, delta: 6.2 },
              points: { usual: 24.1, projected: 26, delta: 1.9 },
              categories: [
                { stat: 'ast', usual: 6.1, projected: 8.4, delta: 2.3 },
                { stat: 'reb', usual: 5, projected: 7.2, delta: 2.2 },
              ],
            },
            reasons: ['ROLE_INCREASE', 'TEAMMATE_ABSENCE'],
            evidence: {
              teammate_out: 'Anthony Davis',
              teammate_out_minutes: 35.1,
              teammate_out_prob_active: 0.1,
            },
            spotlight: true,
            slate_spotlight: false,
          }),
        ],
      },
    ],
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/projections']}>
      <SlatePage />
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  slateMock.mockResolvedValue(payload());
});

describe('SlatePage', () => {
  it('shows a game-card skeleton, not a spinner, while the slate loads', () => {
    // arrange
    slateMock.mockReturnValue(new Promise(() => {}));

    // act
    const { container } = renderPage();

    // assert
    expect(screen.getByRole('status', { name: /loading projections/i })).toBeInTheDocument();
    expect(container.querySelector('.loading')).toBeNull();
    expect(screen.queryByText('Stephen Curry')).not.toBeInTheDocument();
  });

  it('renders each game as a card with its projected players', async () => {
    renderPage();

    expect(await screen.findByRole('heading', { name: /GSW.*@.*LAL/ })).toBeInTheDocument();
    expect(screen.getByText('Stephen Curry')).toBeInTheDocument();
    expect(screen.getByText('LeBron James')).toBeInTheDocument();
    expect(screen.getByText('Scheduled')).toBeInTheDocument();
  });

  it('shows the projected line and availability percentage for each player', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');

    expect(screen.getByText('28.7')).toBeInTheDocument();
    expect(screen.getByText(/33\.1 min/)).toBeInTheDocument();
    expect(screen.getByText('99%')).toBeInTheDocument();
    expect(screen.getByText('42%')).toBeInTheDocument();
  });

  it('headlines the points he scores if he plays, beside his minutes', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');

    const headline = screen.getByTestId('slate-headline-2544');
    expect(headline.textContent?.replace(/\s+/g, ' ')).toBe('26.0 pts if he plays · 30.5 min');
  });

  it('prices the chance he sits into a muted line under the headline', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');

    expect(screen.getByTestId('slate-schedule-2544')).toHaveTextContent(
      '42% to play, 18.6 over the schedule'
    );
    expect(screen.getByTestId('slate-schedule-201939')).toHaveTextContent(
      '99% to play, 28.4 over the schedule'
    );
  });

  it('drops the muted line when availability is not modelled', async () => {
    slateMock.mockResolvedValue(
      payload({
        games: [{ ...payload().games[0], players: [slatePlayer({ prob_active: null })] }],
      })
    );

    renderPage();
    await screen.findByText('Stephen Curry');

    expect(screen.queryByTestId('slate-schedule-201939')).not.toBeInTheDocument();
  });

  it('badges a preseason game and explains why its minutes run high', async () => {
    slateMock.mockResolvedValue(
      payload({ games: [{ ...payload().games[0], preseason: true }] })
    );

    renderPage();
    const heading = await screen.findByRole('heading', { name: /GSW.*@.*LAL/ });

    const card = heading.closest('section') as HTMLElement;
    expect(within(card).getByText('Preseason')).toBeInTheDocument();
    expect(screen.getByTestId('slate-preseason-note')).toHaveTextContent(
      'trained on regular-season games'
    );
  });

  it('shows no preseason badge or note on a regular-season slate', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');

    expect(screen.queryByText('Preseason')).not.toBeInTheDocument();
    expect(screen.queryByTestId('slate-preseason-note')).not.toBeInTheDocument();
  });

  it('dates the run by when it was published and its injury cutoff, never by artifact id', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');

    const subtitle = screen.getByTestId('slate-subtitle');
    expect(subtitle).toHaveTextContent(/published Feb \d{1,2}, \d{1,2}:\d{2}\s[AP]M \S+/);
    expect(subtitle).toHaveTextContent(/injuries as of \d{1,2}:\d{2}\s[AP]M \S+/);
    expect(screen.queryByText(/v1-decomposed/)).not.toBeInTheDocument();
  });

  it('drops the injuries clause when the run recorded no information cutoff', async () => {
    slateMock.mockResolvedValue(
      payload({ run: { ...RUN, information_as_of: null } })
    );

    renderPage();
    await screen.findByText('Stephen Curry');

    expect(screen.getByTestId('slate-subtitle')).not.toHaveTextContent(/injuries as of/);
  });

  it('titles the page as today only while the picker is on today', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent("Today's Projections");

    fireEvent.change(screen.getByLabelText('Game date'), { target: { value: '2099-10-20' } });

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/^Projections$/);
  });

  it('says when the date is past the latest run and shows the games as schedule only', async () => {
    slateMock.mockResolvedValue(
      payload({
        date: '2026-10-20',
        covered: false,
        run: {
          ...RUN,
          covers_from: '2026-10-01',
          covers_to: '2026-10-07',
        },
        games: [{ ...payload().games[0], players: [] }],
      })
    );

    renderPage();

    expect(await screen.findByTestId('slate-coverage-notice')).toHaveTextContent(
      "No projections for Tue, Oct 20 yet. The latest run covers Oct 1 to Oct 7; each day's run looks seven days ahead and publishes around 9 AM PT."
    );
    expect(screen.getByRole('heading', { name: /GSW.*@.*LAL/ })).toBeInTheDocument();
    expect(screen.getByText('projections not published yet')).toBeInTheDocument();
    expect(screen.queryByText(/No projected players for this game yet/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/No prediction run yet/i)).not.toBeInTheDocument();
  });

  it('shows no coverage notice when the run covers the date', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');

    expect(screen.queryByTestId('slate-coverage-notice')).not.toBeInTheDocument();
    expect(screen.queryByText('projections not published yet')).not.toBeInTheDocument();
  });

  it('shows the per-category projections so the line is more than points', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');

    expect(screen.getByText(/4\.6 REB · 6\.1 AST · 1\.2 STL · 0\.3 BLK · 4\.4 3PM · 2\.8 TOV/))
      .toBeInTheDocument();
  });

  it('shows each player total projected impact, signed', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');

    expect(screen.getByText('+6.2')).toBeInTheDocument();
    expect(screen.getByText('+2.1')).toBeInTheDocument();
  });

  it('marks the slate standouts and explains every badge in the legend', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');

    expect(screen.getByLabelText('Top projected impact on the slate')).toBeInTheDocument();
    const legend = screen.getByTestId('slate-legend');
    expect(legend).toHaveTextContent(/impact/i);
    expect(legend).toHaveTextContent(/0 = average night/i);
    expect(legend).toHaveTextContent(/chance he plays/i);
    expect(legend).toHaveTextContent(/slate standout/i);
  });

  it('renders a placeholder when availability was not modelled', async () => {
    slateMock.mockResolvedValue(
      payload({
        games: [
          {
            ...payload().games[0],
            players: [slatePlayer({ prob_active: null })],
          },
        ],
      })
    );

    renderPage();

    expect(await screen.findByText('Stephen Curry')).toBeInTheDocument();
    expect(screen.getByTitle('Availability not modelled')).toHaveTextContent('-');
  });

  it('renders a placeholder when the run projected no impact for a player', async () => {
    slateMock.mockResolvedValue(
      payload({
        games: [
          {
            ...payload().games[0],
            top_impact: null,
            players: [slatePlayer({ impact: null, spotlight: false, slate_spotlight: false })],
          },
        ],
      })
    );

    renderPage();

    expect(await screen.findByText('Stephen Curry')).toBeInTheDocument();
    expect(
      screen.getByTitle('No impact score for this player')
    ).toHaveTextContent('-');
  });

  it('labels a player with no roster row by id instead of showing a blank name', async () => {
    slateMock.mockResolvedValue(
      payload({
        games: [
          {
            ...payload().games[0],
            players: [
              slatePlayer({
                nba_player_id: '1642850',
                name: 'NBA #1642850 (new roster)',
                name_is_placeholder: true,
                team_abbr: null,
              }),
            ],
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

  it('says what the impact number means without explaining how it is computed', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');

    expect(screen.getByText(/ordered by projected impact/i)).toBeInTheDocument();
    expect(screen.getByText(/0 is an average night/i)).toBeInTheDocument();
    expect(screen.queryByText(/z-score/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/every player the run projects for this date/i)).toBeNull();
  });

  it('explains that no model run has completed yet, while still listing the games', async () => {
    slateMock.mockResolvedValue(
      payload({
        run: null,
        covered: false,
        games: [{ ...payload().games[0], players: [] }],
      })
    );

    renderPage();

    expect(await screen.findByText(/No prediction run yet/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /GSW.*@.*LAL/ })).toBeInTheDocument();
    expect(screen.getByText(/No projected players for this game yet/i)).toBeInTheDocument();
  });

  it('shows an empty state for a day with no games', async () => {
    slateMock.mockResolvedValue(payload({ games: [] }));

    renderPage();

    expect(await screen.findByText('No games scheduled')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /the watchlist/i })).toHaveAttribute(
      'href',
      '/watchlist'
    );
  });

  it('refetches for another date when the picker changes', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');
    slateMock.mockResolvedValue(payload({ date: '2026-02-06', games: [] }));

    // a date input is set wholesale, not typed character by character.
    fireEvent.change(screen.getByLabelText('Game date'), { target: { value: '2026-02-06' } });

    expect(await screen.findByText('No games scheduled')).toBeInTheDocument();
    expect(slateMock).toHaveBeenLastCalledWith('2026-02-06', 'impact');
  });

  it('shows an error state with a retry button when the request fails', async () => {
    slateMock.mockRejectedValue(new Error('slate down'));

    renderPage();

    expect(await screen.findByText(/Failed to load the slate/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Try Again/i })).toBeInTheDocument();
  });

  it('badges the reasons a row departs from his usual, with a compact vs-usual line', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');

    const row = screen.getByText('LeBron James').closest('li') as HTMLElement;
    expect(within(row).getByText('Role increase')).toBeInTheDocument();
    expect(within(row).getByText('Teammate out')).toBeInTheDocument();
    expect(within(row).getByText('MIN +6.2 · AST +2.3 · REB +2.2')).toBeInTheDocument();
  });

  it('keeps a row quiet when no reason fired and his minutes barely moved', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');

    expect(screen.queryByTestId('vs-usual-201939')).not.toBeInTheDocument();
    expect(screen.queryByTestId('reasons-201939')).not.toBeInTheDocument();
  });

  it('shows the vs-usual line for a two-minute swing even without a reason', async () => {
    slateMock.mockResolvedValue(
      payload({
        games: [
          {
            ...payload().games[0],
            players: [
              slatePlayer({
                vs_usual: {
                  minutes: { usual: 34, projected: 31.9, delta: -2.1 },
                  points: { usual: 26.9, projected: 25, delta: -1.9 },
                  categories: [{ stat: 'fg3m', usual: 4.8, projected: 4.1, delta: -0.7 }],
                },
              }),
            ],
          },
        ],
      })
    );

    renderPage();
    await screen.findByText('Stephen Curry');

    expect(screen.getByTestId('vs-usual-201939')).toHaveTextContent('MIN -2.1 · 3PM -0.7');
    expect(screen.queryByTestId('reasons-201939')).not.toBeInTheDocument();
  });

  it('opens a row to the evidence behind its reasons', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');

    fireEvent.click(screen.getByTestId('vs-usual-2544').querySelector('summary') as HTMLElement);

    const details = screen.getByTestId('vs-usual-2544');
    expect(details).toHaveAttribute('open');
    expect(details).toHaveTextContent('Minutes: 30.5 projected, usually 24.3 (+6.2)');
    expect(details).toHaveTextContent('AST: 8.4 projected, usually 6.1 (+2.3)');
    expect(details).toHaveTextContent('Usage freed: Anthony Davis usually plays 35.1 minutes, 10% to play');
  });

  it('shows no vs-usual line for a player with too little history to have a usual', async () => {
    slateMock.mockResolvedValue(
      payload({
        games: [
          {
            ...payload().games[0],
            players: [
              slatePlayer({
                usual_min: null,
                usual_pts: null,
                min_vs_usual: null,
                pts_vs_usual: null,
                baseline_games: 0,
                edge: null,
                vs_usual: null,
              }),
            ],
          },
        ],
      })
    );

    renderPage();

    expect(await screen.findByText('Stephen Curry')).toBeInTheDocument();
    expect(screen.queryByText(/vs usual:/)).not.toBeInTheDocument();
  });

  it('refetches ranked by edge when the sort toggle flips, and says what the order means', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');
    expect(screen.getByRole('button', { name: 'Impact' })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByRole('button', { name: 'Edge vs usual' }));

    expect(screen.getByRole('button', { name: 'Edge vs usual' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
    expect(slateMock).toHaveBeenLastCalledWith(expect.any(String), 'edge');
    expect(await screen.findByTestId('slate-order-note')).toHaveTextContent(/own usual/);
  });

  it('orders the players as the server ranked them', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');

    const rows = screen.getAllByRole('listitem');
    expect(within(rows[0]).getByText('Stephen Curry')).toBeInTheDocument();
    expect(within(rows[1]).getByText('LeBron James')).toBeInTheDocument();
  });

  it('shows the current injury designation in the source wording', async () => {
    slateMock.mockResolvedValue(
      payload({
        games: [
          {
            ...payload().games[0],
            players: [
              slatePlayer({
                injury_status: 'questionable',
                injury_status_raw: 'Game Time Decision',
                injury_detail: 'Knee',
                injury_as_of: '2026-02-04T20:00:00Z',
                injury_changed_after_run: false,
              }),
            ],
          },
        ],
      })
    );

    renderPage();
    await screen.findByText('Stephen Curry');

    const chip = screen.getByTestId('injury-chip');
    expect(chip).toHaveTextContent('Game Time Decision');
    expect(chip).not.toHaveTextContent('· new');
  });

  it('marks a designation that moved after the projection was published', async () => {
    slateMock.mockResolvedValue(
      payload({
        games: [
          {
            ...payload().games[0],
            players: [
              slatePlayer({
                injury_status: 'out',
                injury_status_raw: 'Out',
                injury_detail: 'Ankle',
                injury_as_of: '2026-02-04T20:00:00Z',
                injury_changed_after_run: true,
              }),
            ],
          },
        ],
      })
    );

    renderPage();
    await screen.findByText('Stephen Curry');

    const chip = screen.getByTestId('injury-chip');
    expect(chip).toHaveTextContent('Out');
    expect(chip).toHaveTextContent('· new');
    expect(chip).toHaveAttribute('title', expect.stringMatching(/after this projection/i));
  });

  it('shows a clearance chip when a priced-in designation came off the report', async () => {
    slateMock.mockResolvedValue(
      payload({
        games: [
          {
            ...payload().games[0],
            players: [
              slatePlayer({
                injury_status: null,
                injury_status_raw: null,
                injury_detail: null,
                injury_as_of: null,
                injury_changed_after_run: true,
              }),
            ],
          },
        ],
      })
    );

    renderPage();
    await screen.findByText('Stephen Curry');

    const chip = screen.getByTestId('injury-chip');
    expect(chip).toHaveTextContent('Cleared');
    expect(chip).toHaveTextContent('· new');
  });

  it('shows no injury chip for a healthy player or an older server', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');

    expect(screen.queryByTestId('injury-chip')).not.toBeInTheDocument();
  });

  it('explains the injury chip in the legend', async () => {
    renderPage();
    await screen.findByText('Stephen Curry');

    const legend = screen.getByTestId('slate-legend');
    expect(legend).toHaveTextContent(/injury report now/i);
    expect(legend).toHaveTextContent(/changed after this projection/i);
  });
});
