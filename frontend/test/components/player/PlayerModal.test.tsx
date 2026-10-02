import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { PlayerModal } from '../../../src/components/player/PlayerModal';
import type { Player, Rating2kDetail, Rating2kSummary } from '../../../src/types';

// the header's full analytics link is a react-router Link, so the modal needs a router.
const renderModal = (player: Player | null, onClose: () => void = () => {}) =>
  render(
    <MemoryRouter>
      <PlayerModal player={player} onClose={onClose} />
    </MemoryRouter>
  );

vi.mock('../../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../src/api/client')>();
  return {
    ...actual,
    getRatings2kByName: vi.fn(),
    getRatings2kPlayer: vi.fn(),
  };
});

const { getRatings2kByName, getRatings2kPlayer } = await import('../../../src/api/client');
const byNameMock = vi.mocked(getRatings2kByName);
const detailMock = vi.mocked(getRatings2kPlayer);

const RATING_2K: Rating2kSummary = {
  slug: 'test-player',
  name: 'Test Player',
  team: 'Los Angeles Lakers',
  team_type: 'curr',
  overall: 92,
  positions: ['PG'],
  game_version: 'NBA 2K25',
  player_image: null,
};

const RATING_2K_DETAIL: Rating2kDetail = {
  player: {
    ...RATING_2K,
    archetype: 'Two-Way Slashing Playmaker',
    build: 'Playmaker',
    height: '6\'3"',
    weight: '200 lbs',
    wingspan: '6\'8"',
  },
  attributes: [{ attribute_name: 'threePointShot', value: 84 }],
  badges: [],
  rating_history: [{ game_version: 'NBA 2K25', overall: 92, delta: 0 }],
};

beforeEach(() => {
  vi.clearAllMocks();
  byNameMock.mockResolvedValue(null);
  detailMock.mockResolvedValue(RATING_2K_DETAIL);
});

const samplePlayer: Player = {
  id: 1,
  name: 'Test Player',
  team: 'LAL',
  position: 'PG',
  points_per_game: 27.3,
  rebounds_per_game: 7.2,
  assists_per_game: 5.1,
  steals_per_game: 1.0,
  blocks_per_game: 0.7,
  field_goal_percentage: 48.5,
  three_point_percentage: 36.2,
  free_throw_percentage: 80.1,
  three_pointers_made: 2.6,
  turnovers_per_game: 2.9,
  minutes_per_game: 34.5,
  games_played: 50,
  injury_status: null,
  injury_detail: null,
};

describe('PlayerModal', () => {
  it('renders nothing when no player is selected', () => {
    const { container } = renderModal(null);

    expect(container.firstChild).toBeNull();
  });

  it('renders the player name, team, position, and formatted stats', () => {
    renderModal(samplePlayer);

    expect(screen.getByRole('heading', { name: 'Test Player' })).toBeInTheDocument();
    expect(screen.getByText(/LAL.*PG/)).toBeInTheDocument();
    expect(screen.getByText('27.3')).toBeInTheDocument();
    expect(screen.getByText('48.5%')).toBeInTheDocument();
  });

  it('shows an injury alert when injury_status is set', () => {
    const injured: Player = { ...samplePlayer, injury_status: 'Day_To_Day', injury_detail: 'ankle' };

    renderModal(injured);

    expect(screen.getByTestId('injury-badge')).toHaveTextContent('GTD');
    expect(screen.getByTestId('injury-detail')).toHaveTextContent('ankle');
  });

  it('calls onClose when the close button is clicked', async () => {
    const onClose = vi.fn();
    renderModal(samplePlayer, onClose);
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Close' }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('shows a clickable 2K badge when the name resolves to a rated player', async () => {
    byNameMock.mockResolvedValue(RATING_2K);
    renderModal(samplePlayer);
    const user = userEvent.setup();

    const badge = await screen.findByRole('button', { name: /2K 92/ });
    await user.click(badge);

    expect(await screen.findByText('Three Point Shot')).toBeInTheDocument();
    expect(screen.getByText(/nba2kapi\.com/)).toBeInTheDocument();
    expect(byNameMock).toHaveBeenCalledWith('Test Player');
  });

  it('renders nothing extra when the name has no 2K match', async () => {
    renderModal(samplePlayer);

    await waitFor(() => {
      expect(byNameMock).toHaveBeenCalled();
    });
    expect(screen.queryByRole('button', { name: /2K/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/nba2kapi\.com/)).not.toBeInTheDocument();
  });

  it('renders nothing extra when the 2K lookup fails', async () => {
    byNameMock.mockRejectedValue(new Error('ratings down'));

    renderModal(samplePlayer);

    await waitFor(() => {
      expect(byNameMock).toHaveBeenCalled();
    });
    expect(screen.queryByRole('button', { name: /2K/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/Failed/i)).not.toBeInTheDocument();
  });
});
