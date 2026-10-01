import { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import {
  getAuthToken, getCurrentUser, getAdminStats, getAdminUsers, getAdminViews,
  type AdminStats, type AdminUser, type AdminPageView,
} from '../api/client';
import { SkeletonBlock, SkeletonTable } from '../components/Skeleton';
import { STAT_PLACEHOLDER } from '../utils/stats';

type AdminState =
  | { status: 'loading' }
  | { status: 'forbidden' }
  | { status: 'error' }
  | { status: 'ready'; stats: AdminStats; users: AdminUser[]; views: AdminPageView[] };

function formatDate(iso: string | null): string {
  if (!iso) return STAT_PLACEHOLDER;
  return new Date(iso).toLocaleString([], {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

export const AdminPage = (): JSX.Element => {
  const [state, setState] = useState<AdminState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    // check the flag first so non-admins get a clean message instead of three 403s.
    getCurrentUser()
      .then((user) => {
        if (!user.is_admin) {
          if (!cancelled) setState({ status: 'forbidden' });
          return;
        }
        return Promise.all([getAdminStats(), getAdminUsers(), getAdminViews()]).then(
          ([stats, users, views]) => {
            if (!cancelled) setState({ status: 'ready', stats, users, views });
          }
        );
      })
      .catch(() => {
        if (!cancelled) setState({ status: 'error' });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // the auth guard must come after every hook: an early return above one crashes
  // with react error #300 when the token disappears while the page is mounted.
  if (!getAuthToken()) {
    return <Navigate to="/login" replace state={{ from: '/admin' }} />;
  }

  if (state.status === 'loading') {
    return (
      <div className="mx-auto max-w-6xl space-y-8 px-4 py-8" role="status" aria-label="Loading developer tools">
        <SkeletonBlock className="h-8 w-56" />
        <SkeletonBlock className="h-20 w-full" />
        <SkeletonTable rows={6} cols={6} label="Loading users" />
      </div>
    );
  }

  if (state.status === 'forbidden') {
    return (
      <div className="max-w-md mx-auto py-24 px-4 text-center">
        <h1 className="mb-2 font-display text-3xl font-semibold uppercase tracking-wide">Admin access required</h1>
        <p className="text-muted">Your account doesn't have access to the developer tools.</p>
      </div>
    );
  }

  if (state.status === 'error') {
    return (
      <div className="max-w-md mx-auto py-24 px-4">
        <p className="border border-error px-4 py-3 text-error" role="alert">
          Failed to load developer tools. Try refreshing.
        </p>
      </div>
    );
  }

  const { stats, users, views } = state;

  return (
    <div className="max-w-6xl mx-auto px-4 py-8 space-y-8">
      <h1 className="font-display text-3xl font-semibold uppercase tracking-wide">Developer Tools</h1>

      <div className="stats stats-vertical sm:stats-horizontal w-full border border-base-300">
        <div className="stat">
          <div className="stat-title">Total users</div>
          <div className="stat-value">{stats.totals.total_users}</div>
          <div className="stat-desc">+{stats.totals.new_users_7d} in the last 7 days</div>
        </div>
        <div className="stat">
          <div className="stat-title">Views (24h)</div>
          <div className="stat-value">{stats.totals.views_24h}</div>
          <div className="stat-desc">{stats.totals.views_7d} in the last 7 days</div>
        </div>
        <div className="stat">
          <div className="stat-title">Active users (24h)</div>
          <div className="stat-value">{stats.totals.active_users_24h}</div>
          <div className="stat-desc">signed-in visitors</div>
        </div>
      </div>

      <section>
        <h2 className="mb-3 font-display text-2xl font-semibold uppercase tracking-wide">Users ({users.length})</h2>
        {users.length === 0 ? (
          <p className="text-muted">No users yet.</p>
        ) : (
          <div className="overflow-x-auto border border-base-300 rounded-box">
            <table className="table table-sm">
              <thead>
                <tr>
                  <th>Username</th>
                  <th>Email</th>
                  <th>Sign-in</th>
                  <th>Roster</th>
                  <th>Joined</th>
                  <th>Last seen</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.id}>
                    <td className="font-medium">
                      {user.username}
                      {user.is_admin && <span className="badge badge-outline badge-sm ml-2">admin</span>}
                    </td>
                    <td>{user.email ?? STAT_PLACEHOLDER}</td>
                    <td>
                      <div className="flex gap-1">
                        {user.has_password && <span className="badge badge-ghost badge-sm">password</span>}
                        {user.has_google && <span className="badge badge-ghost badge-sm">google</span>}
                      </div>
                    </td>
                    <td>{user.roster_count}</td>
                    <td className="whitespace-nowrap">{formatDate(user.created_at)}</td>
                    <td className="whitespace-nowrap">{formatDate(user.last_seen)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="grid lg:grid-cols-2 gap-8">
        <section>
          <h2 className="mb-3 font-display text-2xl font-semibold uppercase tracking-wide">Top pages (7 days)</h2>
          {stats.top_paths.length === 0 ? (
            <p className="text-muted">No page views recorded yet.</p>
          ) : (
            <div className="overflow-x-auto border border-base-300 rounded-box">
              <table className="table table-sm">
                <thead>
                  <tr>
                    <th>Path</th>
                    <th className="text-right">Views</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.top_paths.map((row) => (
                    <tr key={row.path}>
                      <td className="font-mono">{row.path}</td>
                      <td className="text-right">{row.views}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section>
          <h2 className="mb-3 font-display text-2xl font-semibold uppercase tracking-wide">Recent activity</h2>
          {views.length === 0 ? (
            <p className="text-muted">No activity yet.</p>
          ) : (
            <div className="overflow-x-auto border border-base-300 rounded-box max-h-96 overflow-y-auto">
              <table className="table table-sm">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Who</th>
                    <th>Path</th>
                  </tr>
                </thead>
                <tbody>
                  {views.map((view) => (
                    <tr key={view.id}>
                      <td className="whitespace-nowrap">{formatDate(view.created_at)}</td>
                      <td>{view.username ?? <span className="text-faint">anonymous</span>}</td>
                      <td className="font-mono">{view.path}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
};
