import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { Navbar } from '../../src/components/Navbar';
import { Toast } from '../../src/components/Toast';

vi.mock('../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/client')>();
  return {
    ...actual,
    getCurrentUser: vi.fn().mockResolvedValue({ is_admin: false }),
  };
});

describe('Navbar', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders text-only tab links and marks the current route', () => {
    // arrange + act
    render(
      <MemoryRouter initialEntries={['/stats']}>
        <Navbar isLoggedIn={false} onLogout={() => {}} />
      </MemoryRouter>
    );

    // assert
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    expect(nav.querySelector('svg')).toBeNull();
    expect(screen.getByRole('link', { name: 'Stats' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'Home' })).not.toHaveAttribute('aria-current');
  });

  it('shows Sign In when logged out and Account when logged in', () => {
    // arrange + act
    const { rerender } = render(
      <MemoryRouter>
        <Navbar isLoggedIn={false} onLogout={() => {}} />
      </MemoryRouter>
    );

    // assert
    expect(screen.getByRole('button', { name: 'Sign In' })).toBeInTheDocument();
    rerender(
      <MemoryRouter>
        <Navbar isLoggedIn onLogout={() => {}} />
      </MemoryRouter>
    );
    expect(screen.getByRole('button', { name: 'Account' })).toBeInTheDocument();
  });

  it('picks a theme from the theme menu and marks the current one', async () => {
    // arrange
    localStorage.removeItem('theme');
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <Navbar isLoggedIn={false} onLogout={() => {}} />
      </MemoryRouter>
    );

    // act
    await user.click(screen.getByRole('button', { name: 'Theme' }));
    await user.click(screen.getByRole('button', { name: 'Light' }));

    // assert
    expect(document.documentElement.getAttribute('data-theme')).toBe('paper');
    expect(screen.getByRole('button', { name: 'Light' })).toHaveAttribute('aria-current', 'true');
    expect(screen.getByRole('button', { name: 'Dark' })).not.toHaveAttribute('aria-current');
  });

  it('lists tabs in the agreed order without a data-status badge', () => {
    // arrange + act
    render(
      <MemoryRouter>
        <Navbar isLoggedIn={false} onLogout={() => {}} />
      </MemoryRouter>
    );

    // assert
    const labels = screen.getAllByRole('link').map((l) => l.textContent).filter((l) => l !== 'NBA IQ');
    expect(labels).toEqual([
      'Home', 'Stats', 'Projections', 'Watchlist', 'Betting', 'My Team', 'Improve Team', 'History', '2K Ratings',
    ]);
    expect(screen.queryByText(/Updated .* ago|Data status/)).not.toBeInTheDocument();
  });
});

describe('Toast', () => {
  it('labels an error in text and dismisses on click', async () => {
    // arrange
    const onDismiss = vi.fn();
    const user = userEvent.setup();
    render(<Toast message="Could not add player" variant="error" onDismiss={onDismiss} duration={60_000} />);

    // act
    await user.click(screen.getByRole('button', { name: 'Dismiss' }));

    // assert
    expect(screen.getByText('Error')).toBeInTheDocument();
    expect(onDismiss).toHaveBeenCalled();
  });

  it('shows a success message without a generic label', () => {
    // arrange + act
    render(<Toast message="Added a player" onDismiss={() => {}} duration={60_000} />);

    // assert
    expect(screen.getByRole('status')).toHaveTextContent('Added a player');
    expect(screen.queryByText('Saved')).not.toBeInTheDocument();
  });
});
