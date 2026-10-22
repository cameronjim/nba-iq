import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { ModelPropsSection } from '../../../src/components/betting/ModelPropsSection';
import type { PropPick } from '../../../src/types';

vi.mock('../../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../src/api/client')>();
  return { ...actual, getPropPicks: vi.fn() };
});

const { getPropPicks } = await import('../../../src/api/client');
const propsMock = vi.mocked(getPropPicks);

const EMPTY_SENTENCE = 'Prop picks appear here once prop odds are connected.';

const curry: PropPick = {
  player_name: 'Stephen Curry', team: 'GSW', opponent: 'LAL', game_date: '2026-10-21',
  market: 'pts', line: 24.5, side: 'over', bookmaker: 'DraftKings', price: -115,
  model_prob: 0.58, implied_prob_novig: 0.5304, ev: 0.08, prob_active: 0.82, void_rule: 'dnp',
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ModelPropsSection', () => {
  it('shows a skeleton while prop picks load', () => {
    // arrange
    propsMock.mockReturnValue(new Promise(() => {}));

    // act
    render(<ModelPropsSection />);

    // assert
    expect(screen.getByRole('status', { name: 'Loading prop picks' })).toBeInTheDocument();
  });

  it('renders each prop pick as one sentence', async () => {
    // arrange
    propsMock.mockResolvedValue({
      run: { predicted_at: '2026-10-21T15:00:00Z', information_as_of: '2026-10-21T14:00:00Z' },
      picks: [curry],
    });

    // act
    render(<ModelPropsSection />);

    // assert
    expect(await screen.findByText(
      'Stephen Curry over 24.5 points (-115, DraftKings) · the model gives this 58%; the price implies 53% · 82% to play'
    )).toBeInTheDocument();
  });

  it('shows the single sentence when there are no picks', async () => {
    // arrange
    propsMock.mockResolvedValue({ run: null, picks: [] });

    // act
    render(<ModelPropsSection />);

    // assert
    expect(await screen.findByText(EMPTY_SENTENCE)).toBeInTheDocument();
  });

  describe('collapse', () => {
    const run = { predicted_at: '2026-10-21T15:00:00Z', information_as_of: '2026-10-21T14:00:00Z' };
    const many = (n: number): PropPick[] =>
      Array.from({ length: n }, (_, i) => ({ ...curry, player_name: `Player ${i}`, ev: 0.5 - i * 0.01 }));
    const rows = (): number => within(screen.getByRole('list')).getAllByRole('listitem').length;

    it('shows 6, reveals 12 more per click, then offers Show less', async () => {
      // arrange
      propsMock.mockResolvedValue({ run, picks: many(30) });
      render(<ModelPropsSection />);
      await screen.findByRole('button', { name: 'See more (24)' });

      // act + assert
      expect(rows()).toBe(6);
      fireEvent.click(screen.getByRole('button', { name: 'See more (24)' }));
      expect(rows()).toBe(18);
      fireEvent.click(screen.getByRole('button', { name: 'See more (12)' }));
      expect(rows()).toBe(30);
      fireEvent.click(screen.getByRole('button', { name: 'Show less' }));
      expect(rows()).toBe(6);
    });

    it('collapses two lines of the same player and market to the best ev', async () => {
      // arrange
      propsMock.mockResolvedValue({
        run,
        picks: [{ ...curry, line: 24.5, ev: 0.04 }, { ...curry, line: 26.5, ev: 0.09 }],
      });

      // act
      render(<ModelPropsSection />);

      // assert
      expect(await screen.findByText(/over 26.5 points/)).toBeInTheDocument();
      expect(screen.queryByText(/over 24.5 points/)).not.toBeInTheDocument();
      expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });
  });

  it('shows the same sentence, not an error, when the endpoint is missing or fails', async () => {
    // arrange
    propsMock.mockRejectedValue(Object.assign(new Error('Not Found'), { response: { status: 404 } }));

    // act
    render(<ModelPropsSection />);

    // assert
    expect(await screen.findByText(EMPTY_SENTENCE)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /try again/i })).not.toBeInTheDocument();
  });
});
