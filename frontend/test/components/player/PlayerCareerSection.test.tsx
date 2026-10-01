import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { PlayerCareerSection } from '../../../src/components/player/PlayerCareerSection';
import type { PlayerSeasonRow } from '../../../src/types';

vi.mock('../../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../src/api/client')>();
  return {
    ...actual,
    getPlayerCareer: vi.fn(),
  };
});

const { getPlayerCareer } = await import('../../../src/api/client');
const careerMock = vi.mocked(getPlayerCareer);

const SEASONS: PlayerSeasonRow[] = [
  {
    nba_player_id: '977', player_name: 'Kobe Bryant', season: '1996-97', team: 'LAL',
    games_played: 71, minutes_per_game: 15.5, points_per_game: 7.6, rebounds_per_game: 1.9,
    assists_per_game: 1.3, steals_per_game: 0.7, blocks_per_game: 0.3, turnovers_per_game: 1.6,
    field_goal_percentage: 41.7, three_point_percentage: 37.5, free_throw_percentage: 81.9,
    three_pointers_made: 0.7,
  },
  {
    nba_player_id: '977', player_name: 'Kobe Bryant', season: '2005-06', team: 'LAL',
    games_played: 80, minutes_per_game: 41.0, points_per_game: 35.4, rebounds_per_game: 5.3,
    assists_per_game: 4.5, steals_per_game: 1.8, blocks_per_game: 0.4, turnovers_per_game: 3.1,
    field_goal_percentage: 45.0, three_point_percentage: 34.7, free_throw_percentage: 85.0,
    three_pointers_made: 2.3,
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  careerMock.mockResolvedValue({
    nba_player_id: '977',
    player_name: 'Kobe Bryant',
    seasons: SEASONS,
  });
});

describe('PlayerCareerSection', () => {
  it('renders one row per season, oldest first as the api returns them', async () => {
    render(<PlayerCareerSection nbaPlayerId="977" />);

    expect(await screen.findByText('Career by Season')).toBeInTheDocument();
    expect(screen.getByText('1996-97')).toBeInTheDocument();
    expect(screen.getByText('2005-06')).toBeInTheDocument();
    expect(screen.getByText('35.4')).toBeInTheDocument();
    expect(careerMock).toHaveBeenCalledWith('977');
  });

  it('shows a table-shaped skeleton while the career loads', () => {
    careerMock.mockReturnValue(new Promise(() => {}));

    const { container } = render(<PlayerCareerSection nbaPlayerId="977" />);

    expect(screen.getByRole('status', { name: 'Loading career history' })).toBeInTheDocument();
    expect(container.querySelector('.loading-spinner')).toBeNull();
  });

  it('renders nothing and skips the request without an nba id', () => {
    const { container } = render(<PlayerCareerSection nbaPlayerId={null} />);

    expect(container).toBeEmptyDOMElement();
    expect(careerMock).not.toHaveBeenCalled();
  });

  it('renders nothing when the player has no ingested history', async () => {
    careerMock.mockResolvedValue({ nba_player_id: '1', player_name: 'Nobody', seasons: [] });

    const { container } = render(<PlayerCareerSection nbaPlayerId="1" />);

    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('renders nothing when the career lookup fails', async () => {
    careerMock.mockRejectedValue(new Error('404'));

    const { container } = render(<PlayerCareerSection nbaPlayerId="1" />);

    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('coerces string numerics and shows dashes for missing stats', async () => {
    careerMock.mockResolvedValue({
      nba_player_id: '893',
      player_name: 'Michael Jordan',
      seasons: [
        {
          nba_player_id: '893', player_name: 'Michael Jordan', season: '1984-85', team: null,
          games_played: '82', minutes_per_game: '38.30', points_per_game: '28.20',
          rebounds_per_game: '6.50', assists_per_game: '5.90', steals_per_game: null,
          blocks_per_game: null, turnovers_per_game: null, field_goal_percentage: '51.50',
          three_point_percentage: null, free_throw_percentage: '84.50', three_pointers_made: null,
        },
      ],
    });

    const { container } = render(<PlayerCareerSection nbaPlayerId="893" />);

    expect(await screen.findByText('28.2')).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/NaN|null|undefined/);
  });
});
