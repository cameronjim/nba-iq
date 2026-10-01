import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PlayerUpcomingGames } from '../../../src/components/player/PlayerUpcomingGames';
import type {
  PlayerPredictionsResponse,
  PredictionStatLine,
  UpcomingGamePrediction,
} from '../../../src/types';

const RUN = {
  id: 1,
  model_version: 'bt20260115',
  feature_version: 'v3',
  predicted_at: '2026-08-17T22:08:18.285Z',
  forecast_cutoff_at: '2026-01-15T00:00:00.000Z',
  horizon: 'gameday (T-6h)',
};

function line(over: Partial<PredictionStatLine> = {}): PredictionStatLine {
  return { expected: null, p10: null, p50: null, p90: null, unconditional: null, ...over };
}

function game(over: Partial<UpcomingGamePrediction> = {}): UpcomingGamePrediction {
  return {
    nba_game_id: '0022500586',
    game_date: '2026-01-15',
    opponent_abbr: 'CHA',
    is_home: true,
    game_status: 'Final',
    prob_active: 0.9146,
    prob_active_model: 0.9146,
    stats: {
      minutes: line({ expected: 36.33, p10: 28.48, p50: 36.17, p90: 43.5, unconditional: 33.23 }),
      pts: line({ expected: 32.34, p10: 25.75, p50: 31.74, p90: 39.94, unconditional: 29.58 }),
      ast: line({ expected: 9.1, unconditional: 8.33 }),
    },
    ...over,
  };
}

function payload(over: Partial<PlayerPredictionsResponse> = {}): PlayerPredictionsResponse {
  return {
    player_id: 373,
    nba_player_id: '1629029',
    run: RUN,
    stats: ['minutes', 'pts', 'ast'],
    games: [game()],
    ...over,
  };
}

describe('PlayerUpcomingGames', () => {
  it('renders nothing while the request has not resolved', () => {
    const { container } = render(<PlayerUpcomingGames data={null} />);

    expect(container).toBeEmptyDOMElement();
  });

  it('renders a row per game with date, opponent, availability and every stat column', async () => {
    render(
      <PlayerUpcomingGames
        data={payload({
          games: [
            game(),
            game({
              nba_game_id: '0022500601',
              game_date: '2026-01-17',
              opponent_abbr: 'POR',
              is_home: false,
              prob_active: 0.86,
            }),
          ],
        })}
      />
    );

    expect(screen.getByRole('heading', { name: /Upcoming games/i })).toBeInTheDocument();
    const table = within(screen.getByTestId('upcoming-games-table'));
    for (const header of ['Date', 'Opp', 'Availability', 'MIN', 'PTS', 'AST']) {
      expect(table.getByRole('columnheader', { name: header })).toBeInTheDocument();
    }

    expect(screen.getAllByTestId('upcoming-game-row')).toHaveLength(2);
    expect(screen.getByText('Jan 15')).toBeInTheDocument();
    expect(screen.getByText('vs CHA')).toBeInTheDocument();
    expect(screen.getByText('@ POR')).toBeInTheDocument();
  });

  it('shows the median, the P10-P90 band and the schedule-level number for a stat', () => {
    render(<PlayerUpcomingGames data={payload()} />);

    expect(screen.getByText('36.2')).toBeInTheDocument();
    expect(screen.getByText('28.5-43.5')).toBeInTheDocument();
    expect(screen.getByText('33.2 sched')).toBeInTheDocument();
    expect(screen.getByText('31.7')).toBeInTheDocument();
    expect(screen.getByText('25.8-39.9')).toBeInTheDocument();
  });

  it('serves a quantile-less stat as its expected value with no band', () => {
    render(<PlayerUpcomingGames data={payload()} />);

    expect(screen.getByText('9.1')).toBeInTheDocument();
    expect(screen.getByText('8.3 sched')).toBeInTheDocument();
  });

  it('renders a column for a stat the app has never heard of', () => {
    render(
      <PlayerUpcomingGames
        data={payload({
          stats: ['pts', 'fga'],
          games: [game({ stats: { pts: line({ expected: 30 }), fga: line({ expected: 21.4 }) } })],
        })}
      />
    );

    const table = within(screen.getByTestId('upcoming-games-table'));
    expect(table.getByRole('columnheader', { name: 'FGA' })).toBeInTheDocument();
    expect(screen.getByText('21.4')).toBeInTheDocument();
  });

  describe('availability badges', () => {
    const cases: Array<[number, string]> = [
      [0.04, 'OUT-ish'],
      [0.3, 'Doubtful'],
      [0.6, 'Questionable'],
      [0.91, 'Likely'],
    ];

    for (const [prob, label] of cases) {
      it(`labels ${prob} as "${label}"`, () => {
        render(<PlayerUpcomingGames data={payload({ games: [game({ prob_active: prob })] })} />);

        expect(screen.getByText(label)).toBeInTheDocument();
      });
    }

    it('keeps a written-off player’s projections fully visible', () => {
      render(<PlayerUpcomingGames data={payload({ games: [game({ prob_active: 0.02 })] })} />);

      expect(screen.getByText('OUT-ish')).toBeInTheDocument();
      expect(screen.getByText('2%')).toBeInTheDocument();
      expect(screen.getByText('36.2')).toBeInTheDocument();
      expect(screen.getByText('31.7')).toBeInTheDocument();
    });

    it('says in the copy that the badge is a model probability', () => {
      render(<PlayerUpcomingGames data={payload()} />);

      expect(screen.getByText(/not an official injury designation/i)).toBeInTheDocument();
      expect(screen.getByText('Likely').parentElement).toHaveAttribute(
        'data-tip',
        expect.stringMatching(/Model estimate/i)
      );
    });

    it('shows an explicit no-estimate badge rather than implying zero', () => {
      render(<PlayerUpcomingGames data={payload({ games: [game({ prob_active: null })] })} />);

      expect(screen.getByText('No estimate')).toBeInTheDocument();
      expect(screen.queryByText('0%')).not.toBeInTheDocument();
    });
  });

  it('narrows the table to one stat through the picker and back again', async () => {
    render(<PlayerUpcomingGames data={payload()} />);
    const user = userEvent.setup();
    const tablist = within(screen.getByRole('tablist', { name: 'Prediction stat' }));

    for (const label of ['All', 'MIN', 'PTS', 'AST']) {
      expect(tablist.getByRole('tab', { name: label })).toBeInTheDocument();
    }
    expect(tablist.getByRole('tab', { name: 'All' })).toHaveAttribute('aria-selected', 'true');

    await user.click(tablist.getByRole('tab', { name: 'PTS' }));

    const table = within(screen.getByTestId('upcoming-games-table'));
    expect(table.getByRole('columnheader', { name: 'PTS' })).toBeInTheDocument();
    expect(table.queryByRole('columnheader', { name: 'MIN' })).not.toBeInTheDocument();

    await user.click(tablist.getByRole('tab', { name: 'All' }));

    expect(
      within(screen.getByTestId('upcoming-games-table')).getByRole('columnheader', { name: 'MIN' })
    ).toBeInTheDocument();
  });

  it('hides the picker when the run carries a single stat', () => {
    render(
      <PlayerUpcomingGames
        data={payload({ stats: ['pts'], games: [game({ stats: { pts: line({ expected: 30 }) } })] })}
      />
    );

    expect(screen.queryByRole('tablist', { name: 'Prediction stat' })).not.toBeInTheDocument();
  });

  it('shows the run provenance under the table', () => {
    render(<PlayerUpcomingGames data={payload()} />);

    const footer = screen.getByText(/^published /i);
    expect(footer).toHaveTextContent(/published \w{3} \d{1,2}, \d{1,2}:\d{2}\s[AP]M \S+/);
    expect(footer).toHaveTextContent(/data through /i);
    expect(screen.queryByText(/bt20260115/)).not.toBeInTheDocument();
  });

  it('says no run has been published when there is none', () => {
    render(<PlayerUpcomingGames data={payload({ run: null, stats: [], games: [] })} />);

    expect(screen.getByText('No prediction run published yet')).toBeInTheDocument();
    expect(screen.queryByTestId('upcoming-games-table')).not.toBeInTheDocument();
  });

  it('distinguishes "the run has nothing for him" from "there is no run"', () => {
    render(<PlayerUpcomingGames data={payload({ stats: [], games: [] })} />);

    expect(
      screen.getByText('No upcoming games for this player in the current run')
    ).toBeInTheDocument();
    expect(screen.queryByText('No prediction run published yet')).not.toBeInTheDocument();
  });

  it('placeholders the opponent when the schedule row could not be matched', () => {
    render(
      <PlayerUpcomingGames
        data={payload({ games: [game({ opponent_abbr: null, is_home: null })] })}
      />
    );

    const row = within(screen.getAllByTestId('upcoming-game-row')[0]);
    expect(row.getByText('-')).toBeInTheDocument();
  });
});
