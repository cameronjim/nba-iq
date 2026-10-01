import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { BettingPage } from '../../src/pages/BettingPage';

vi.mock('../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/client')>();
  return {
    ...actual,
    getBettingOdds: vi.fn(),
    getPropPicks: vi.fn(),
    getBets: vi.fn(),
  };
});

const { getBettingOdds, getPropPicks, getBets } = await import('../../src/api/client');
const oddsMock = vi.mocked(getBettingOdds);
const propsMock = vi.mocked(getPropPicks);
const betsMock = vi.mocked(getBets);

beforeEach(() => {
  vi.clearAllMocks();
  oddsMock.mockResolvedValue({ games: [], fetched_at: '2026-06-09T12:00:00Z' });
  propsMock.mockResolvedValue({ run: null, picks: [] });
  betsMock.mockResolvedValue({
    bets: [],
    summary: { wins: 0, losses: 0, pushes: 0, pending: 0, net: 0 },
  });
});

const renderPage = (isLoggedIn: boolean): ReturnType<typeof render> =>
  render(
    <MemoryRouter>
      <BettingPage isLoggedIn={isLoggedIn} />
    </MemoryRouter>
  );

describe('BettingPage', () => {
  it('shows the responsible-gambling line with a Terms link and no banner', async () => {
    // arrange + act
    renderPage(false);

    // assert
    expect(await screen.findByText(/Picks are informational, not betting advice/)).toBeInTheDocument();
    expect(screen.getByText(/1-800-GAMBLER/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Terms' })).toHaveAttribute('href', '/terms');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows skeletons while games, prop picks, and bets load', () => {
    // arrange
    oddsMock.mockReturnValue(new Promise(() => {}));
    propsMock.mockReturnValue(new Promise(() => {}));
    betsMock.mockReturnValue(new Promise(() => {}));

    // act
    renderPage(true);

    // assert
    expect(screen.getByRole('status', { name: "Loading tonight's games" })).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Loading prop picks' })).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Loading bets' })).toBeInTheDocument();
    expect(document.querySelector('.loading-spinner')).toBeNull();
  });

  it('stacks the three plainly named sections', async () => {
    // arrange + act
    renderPage(true);

    // assert
    const headings = (await screen.findAllByRole('heading', { level: 2 })).map((h) => h.textContent);
    expect(headings.slice(0, 3)).toEqual(["Tonight's games", 'Prop picks', 'My bets']);
    expect(screen.getByText('Probabilities come from the projection model, not Claude.')).toBeInTheDocument();
  });

  it('shows games and prop picks to signed-out visitors, with a sign-in line for bets', async () => {
    // arrange + act
    renderPage(false);

    // assert
    expect(await screen.findByText('Prop picks appear here once prop odds are connected.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Prop picks' })).toBeInTheDocument();
    expect(screen.getByText('Sign in to track your bets.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /New to betting\? Start here/i })).toBeInTheDocument();
    expect(screen.getByText('Ask Claude')).toBeInTheDocument();
    expect(propsMock).toHaveBeenCalled();
    expect(betsMock).not.toHaveBeenCalled();
  });

  it('loads the ledger when logged in', async () => {
    // arrange + act
    renderPage(true);

    // assert
    expect(await screen.findByText("You haven't tracked any bets yet.")).toBeInTheDocument();
    expect(betsMock).toHaveBeenCalled();
  });

  it('shows the odds error state with a retry button', async () => {
    // arrange
    oddsMock.mockRejectedValue(new Error('espn down'));

    // act
    renderPage(false);

    // assert
    expect(await screen.findByText("Couldn't load the odds right now.")).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});
