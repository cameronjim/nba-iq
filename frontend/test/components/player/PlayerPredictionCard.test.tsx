import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PlayerPredictionCard } from '../../../src/components/player/PlayerPredictionCard';
import type { PlayerPrediction } from '../../../src/types';

function prediction(overrides: Partial<PlayerPrediction> = {}): PlayerPrediction {
  return {
    as_of: '2026-03-01T13:30:00.000Z',
    model_version: 'v1',
    game_date: '2026-03-02',
    prob_active: 0.9,
    conditional: true,
    projected: { minutes: { p10: 28, p50: 34, p90: 38 } },
    ...overrides,
  };
}

describe('PlayerPredictionCard', () => {
  it('compares his next game to his usual when the server sent it', () => {
    render(
      <PlayerPredictionCard
        prediction={prediction({
          vs_usual: {
            minutes: { usual: 30, projected: 34.5, delta: 4.5 },
            points: { usual: 22, projected: 19.8, delta: -2.2 },
          },
        })}
      />
    );

    expect(screen.getByTestId('prediction-vs-usual')).toHaveTextContent(
      'vs usual: MIN +4.5, PTS -2.2'
    );
  });

  it('leaves the line off for an older server or a player without a usual', () => {
    render(<PlayerPredictionCard prediction={prediction()} />);

    expect(screen.queryByTestId('prediction-vs-usual')).not.toBeInTheDocument();
  });

  it('shows only the half that has a comparison', () => {
    render(
      <PlayerPredictionCard
        prediction={prediction({
          vs_usual: {
            minutes: { usual: 30, projected: 31, delta: 1 },
            points: { usual: 22, projected: null, delta: null },
          },
        })}
      />
    );

    expect(screen.getByTestId('prediction-vs-usual')).toHaveTextContent('vs usual: MIN +1.0');
    expect(screen.getByTestId('prediction-vs-usual')).not.toHaveTextContent('PTS');
  });
});
