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
    getDataStatus: vi.fn().mockResolvedValue({
      players_updated_at: null,
      teams_updated_at: null,
      games_updated_at: null,
    }),
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

  it('lists the themes and marks the current one', () => {
    // arrange + act
    render(
      <MemoryRouter>
        <Navbar isLoggedIn={false} onLogout={() => {}} />
      </MemoryRouter>
    );

    // assert
    expect(screen.getByRole('button', { name: 'Theme' })).toBeInTheDocument();
    expect(document.querySelectorAll('[aria-current="true"]')).toHaveLength(1);
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
