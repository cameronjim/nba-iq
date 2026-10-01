import { useEffect, useState } from 'react';
import { getDataStatus, type DataStatus } from '../api/client';

// the scraper runs every 6 hours, so there is no need to poll fast.
const REFRESH_INTERVAL = 5 * 60_000;

function relativeTime(iso: string | null): string {
  if (!iso) return 'never';
  const diffMs = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diffMs / 60_000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

function fullTimestamp(iso: string | null): string {
  if (!iso) return 'No data yet';
  return new Date(iso).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export const StatusBadge = () => {
  const [status, setStatus] = useState<DataStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = (): void => {
      getDataStatus()
        .then((s) => { if (!cancelled) setStatus(s); })
        .catch(() => { /* silent: badge is non-essential */ });
    };
    load();
    const id = setInterval(load, REFRESH_INTERVAL);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const mostRecentISO =
    status &&
    [status.players_updated_at, status.teams_updated_at, status.games_updated_at]
      .filter((s): s is string => !!s)
      .sort()
      .reverse()[0];

  const age = relativeTime(mostRecentISO ?? null);

  return (
    <div className="dropdown dropdown-bottom">
      <button
        tabIndex={0}
        className="text-xs text-muted hover:text-base-content"
        aria-label={`Data updated ${age}`}
        title={`Data updated ${age}`}
      >
        {status ? `Updated ${age}` : 'Data status'}
      </button>
      <div tabIndex={0} className="dropdown-content z-50 mt-1 w-60 border border-base-300 bg-base-200 p-3 rounded-box">
        <p className="mb-2 font-display text-sm font-semibold uppercase tracking-wide">Last updated</p>
        <Row label="Players" iso={status?.players_updated_at ?? null} />
        <Row label="Teams" iso={status?.teams_updated_at ?? null} />
        <Row label="Games" iso={status?.games_updated_at ?? null} />
        <p className="mt-2 border-t border-base-300 pt-2 text-xs text-faint">
          Data refreshes every 6 hours
        </p>
      </div>
    </div>
  );
};

const Row = ({ label, iso }: { label: string; iso: string | null }) => (
  <div className="flex items-baseline justify-between py-1">
    <span className="text-xs">{label}</span>
    <span className="text-xs text-muted" title={fullTimestamp(iso)}>
      {relativeTime(iso)}
    </span>
  </div>
);
