import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CompareModal } from '../../../src/components/player/CompareModal';
import type { Player } from '../../../src/types';

function player(overrides: Partial<Player>): Player {
  return {
    id: 1,
    name: 'Test Player',
    team: 'LAL',
    position: 'PG',
    points_per_game: 20,
    rebounds_per_game: 5,
    assists_per_game: 5,
    steals_per_game: 1,
    blocks_per_game: 1,
    field_goal_percentage: 45,
    three_point_percentage: 36,
    free_throw_percentage: 80,
    three_pointers_made: 2,
    turnovers_per_game: 2,
    minutes_per_game: 30,
    games_played: 50,
    injury_status: null,
    injury_detail: null,
    ...overrides,
  };
}

describe('CompareModal', () => {
  it('closes from the labelled close button', async () => {
    // arrange
    const onClose = vi.fn();
    render(
      <CompareModal
        players={[player({ id: 1 }), player({ id: 2, name: 'Other Player' })]}
        onClose={onClose}
      />
    );

    // act
    await userEvent.setup().click(screen.getByRole('button', { name: 'Close' }));

    // assert
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('marks the best value in each row with screen-reader text, not a glyph', () => {
    // arrange
    const players = [
      player({ id: 1, name: 'Scorer', points_per_game: 30 }),
      player({ id: 2, name: 'Passer', points_per_game: 10 }),
    ];

    // act
    const { container } = render(<CompareModal players={players} onClose={() => {}} />);

    // assert
    expect(container.querySelectorAll('.sr-only').length).toBeGreaterThan(0);
    expect(container.textContent).not.toMatch(/[▲★✕]/);
  });
});
