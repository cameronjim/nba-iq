import { useEffect, useState } from 'react';
import { NavLink, useNavigate, useLocation } from 'react-router-dom';
import { StatusBadge } from './StatusBadge';
import { ThemePicker } from './ThemePicker';
import { getCurrentUser } from '../api/client';

interface NavbarProps {
  isLoggedIn: boolean;
  onLogout: () => void;
}

const tabs = [
  { to: '/', label: 'Home' },
  { to: '/stats', label: 'Stats' },
  { to: '/projections', label: 'Projections' },
  { to: '/watchlist', label: 'Watchlist' },
  { to: '/history', label: 'History' },
  { to: '/ratings', label: '2K Ratings' },
  { to: '/fantasy', label: 'My Team' },
  { to: '/improve', label: 'Improve Team' },
  { to: '/betting', label: 'Betting' },
];

export function Navbar({ isLoggedIn, onLogout }: NavbarProps): JSX.Element {
  const navigate = useNavigate();
  const location = useLocation();
  const [isAdmin, setIsAdmin] = useState(false);

  // the flag only gates the nav link; the admin api re-checks server-side.
  useEffect(() => {
    if (!isLoggedIn) {
      setIsAdmin(false);
      return;
    }
    let cancelled = false;
    getCurrentUser()
      .then((user) => {
        if (!cancelled) setIsAdmin(user.is_admin);
      })
      .catch(() => {
        if (!cancelled) setIsAdmin(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isLoggedIn]);

  const goToSignIn = (): void => {
    navigate('/login', { state: { from: location.pathname } });
  };

  const goAndBlur = (path: string): void => {
    (document.activeElement as HTMLElement)?.blur();
    navigate(path);
  };

  // without this, signing out from a protected route would leave the user on a
  // now-broken page.
  const handleSignOut = (): void => {
    (document.activeElement as HTMLElement)?.blur();
    onLogout();
    navigate('/');
  };

  return (
    <header className="navbar sticky top-0 z-50 flex-wrap gap-x-4 border-b border-base-300 bg-base-200 px-5 py-0 min-h-0">
      <div className="flex items-center gap-3 py-3">
        <NavLink to="/" className="font-display text-3xl font-semibold uppercase leading-none tracking-wide">
          NBA <span className="text-accent">IQ</span>
        </NavLink>
        <StatusBadge />
      </div>

      <nav
        aria-label="Primary"
        className="order-3 -mx-4 flex w-[calc(100%+2rem)] gap-6 overflow-x-auto px-4 no-scrollbar md:order-none md:mx-0 md:w-auto md:flex-1 md:px-0 md:pl-2"
      >
        {tabs.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.to === '/'}
            className={({ isActive }) =>
              `whitespace-nowrap border-b-2 py-4 text-base ${
                isActive
                  ? 'border-accent font-semibold text-base-content'
                  : 'border-transparent text-muted hover:text-base-content'
              }`
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>

      <div className="ml-auto flex items-center gap-2 py-2">
        <ThemePicker />

        {isLoggedIn ? (
          <div className="dropdown dropdown-end">
            <button tabIndex={0} className="btn btn-ghost">
              Account
            </button>
            <ul tabIndex={0} className="dropdown-content menu z-50 mt-1 w-52 border border-base-300 bg-base-200 p-2 rounded-box">
              <li>
                <button onClick={() => goAndBlur('/profile')}>My Profile</button>
              </li>
              <li>
                <button onClick={() => goAndBlur('/preferences')}>Team Preferences</button>
              </li>
              {isAdmin && (
                <li className="mt-1 border-t border-base-300 pt-1">
                  <button onClick={() => goAndBlur('/admin')}>Developer Tools</button>
                </li>
              )}
              <li className="mt-1 border-t border-base-300 pt-1">
                <button onClick={() => goAndBlur('/about')}>About</button>
              </li>
              <li>
                <button onClick={handleSignOut} className="text-error">
                  Sign Out
                </button>
              </li>
            </ul>
          </div>
        ) : (
          <button onClick={goToSignIn} className="btn btn-primary">
            Sign In
          </button>
        )}
      </div>
    </header>
  );
}
