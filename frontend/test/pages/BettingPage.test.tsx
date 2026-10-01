import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { BettingPage } from '../../src/pages/BettingPage';

vi.mock('../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/client')>();
  return {
    ...actual,
    getBettingOdds: vi.fn(),
    getBettingPicks: vi.fn(),
    getBets: vi.fn(),
    getPreferences: vi.fn(),
  };
});

const { getBettingOdds, getBettingPicks, getBets, getPreferences } = await import('../../src/api/client');
const oddsMock = vi.mocked(getBettingOdds);
const picksMock = vi.mocked(getBettingPicks);
const betsMock = vi.mocked(getBets);
const prefsMock = vi.mocked(getPreferences);

beforeEach(() => {
  vi.clearAllMocks();
  oddsMock.mockResolvedValue({ games: [], fetched_at: '2026-06-09T12:00:00Z' });
  picksMock.mockResolvedValue({ picks: [], parlay: null, summary: '', no_games: true });
  betsMock.mockResolvedValue({
    bets: [],
    summary: { wins: 0, losses: 0, pushes: 0, pending: 0, net: 0 },
  });
  prefsMock.mockResolvedValue({});
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

  it('shows skeleton tables while odds and bets load', () => {
    // arrange
    oddsMock.mockReturnValue(new Promise(() => {}));
    betsMock.mockReturnValue(new Promise(() => {}));
    picksMock.mockReturnValue(new Promise(() => {}));

    // act
    renderPage(true);

    // assert
    expect(screen.getByRole('status', { name: 'Loading upcoming games and odds' })).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Loading bets' })).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Loading picks' })).toBeInTheDocument();
    expect(document.querySelector('.loading-spinner')).toBeNull();
  });

  it('shows the sign-in prompt, odds board, chat, and glossary when logged out', async () => {
    renderPage(false);

    expect(await screen.findByText(/Sign in to see Claude's betting picks/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Upcoming Games & Odds/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /New to betting\? Start here/i })).toBeInTheDocument();
    expect(screen.getByText('Ask Claude')).toBeInTheDocument();
    expect(picksMock).not.toHaveBeenCalled();
    expect(betsMock).not.toHaveBeenCalled();
  });

  it('loads picks, ledger, and prefs when logged in', async () => {
    renderPage(true);

    expect(await screen.findByText(/No bettable games right now/i)).toBeInTheDocument();
    expect(picksMock).toHaveBeenCalled();
    expect(betsMock).toHaveBeenCalled();
    expect(screen.getByText('Betting Preferences')).toBeInTheDocument();
  });

  it('shows the odds error state with a retry button', async () => {
    oddsMock.mockRejectedValue(new Error('espn down'));

    renderPage(false);

    expect(await screen.findByText(/Failed to load odds/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Try Again/i })).toBeInTheDocument();
  });
});
