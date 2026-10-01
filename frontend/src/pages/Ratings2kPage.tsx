import { useState } from 'react';
import { IconSearch } from '../components/icons';
import { SkeletonTable } from '../components/Skeleton';
import type { Rating2kSummary, Rating2kTeamType } from '../types';
import { Rating2kTable } from '../components/ratings2k/Rating2kTable';
import { Rating2kModal } from '../components/ratings2k/Rating2kModal';
import { Ratings2kAttribution } from '../components/ratings2k/Ratings2kAttribution';
import { getRatings2kPlayers, type Ratings2kPlayersResponse } from '../api/client';
import { useCachedResource } from '../hooks/useCachedResource';
import { ratings2kPlayersKey } from '../api/resourceCache';

// must clear the largest roster outright (classic is ~767) or the lowest-rated cards
// are unreachable by search. the api ceiling for this route is 1000.
const ROSTER_ROW_LIMIT = 1000;

const TEAM_TYPES: Array<{ value: Rating2kTeamType; label: string }> = [
  { value: 'curr', label: 'Current' },
  { value: 'class', label: 'Classic' },
  { value: 'allt', label: 'All-Time' },
];

export const Ratings2kPage = (): JSX.Element => {
  const [teamType, setTeamType] = useState<Rating2kTeamType>('curr');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Rating2kSummary | null>(null);

  const { data, loading, error, reload } = useCachedResource<Ratings2kPlayersResponse>(
    ratings2kPlayersKey(teamType),
    () => getRatings2kPlayers({ teamType, limit: ROSTER_ROW_LIMIT, sort: 'overall' }),
    { errorMessage: 'Failed to load 2K ratings' }
  );

  const rows = data?.players ?? [];
  const term = search.trim().toLowerCase();
  const filtered = term
    ? rows.filter((row) => (row.name ?? '').toLowerCase().includes(term))
    : rows;
  const total = data?.total ?? 0;
  const truncated = total > rows.length;

  const renderBody = (): JSX.Element => {
    if (loading) {
      return <SkeletonTable rows={12} cols={5} label="Loading 2K ratings" />;
    }

    if (error) {
      return (
        <div className="flex flex-col items-center py-12 gap-4 border border-base-300">
          <p className="text-error text-sm">{error}</p>
          <button onClick={() => void reload()} className="btn btn-primary btn-sm">
            Try Again
          </button>
        </div>
      );
    }

    if (rows.length === 0) {
      return (
        <div className="flex flex-col items-center text-center py-12 gap-2 border border-base-300">
          <p className="font-semibold">No 2K ratings available yet</p>
          <p className="text-sm text-muted max-w-md">
            Player ratings and attributes show up here once the 2K roster has been
            imported. Real NBA stats are on the Stats tab in the meantime.
          </p>
        </div>
      );
    }

    return <Rating2kTable rows={filtered} onSelect={setSelected} />;
  };

  return (
    <div className="pb-20">
      <div className="max-w-[1400px] mx-auto px-4 py-6">
        <h1 className="font-display text-3xl font-semibold uppercase tracking-wide mb-1">
          2K Ratings
        </h1>
        <p className="text-sm text-muted mb-5">
          Overall ratings and full attribute breakdowns. Click a player for all 35 attributes.
        </p>

        <div className="flex flex-wrap items-center gap-3 mb-5">
          <div role="group" aria-label="Team type" className="join">
            {TEAM_TYPES.map((option) => (
              <button
                key={option.value}
                onClick={() => setTeamType(option.value)}
                aria-pressed={teamType === option.value}
                className={`btn btn-sm join-item ${teamType === option.value ? 'btn-primary' : 'btn-ghost border border-base-300'}`}
              >
                {option.label}
              </button>
            ))}
          </div>

          <label className="input input-bordered input-sm flex items-center gap-2 flex-1 min-w-[200px] max-w-[360px]">
            <IconSearch size={14} className="text-muted" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search players..."
              aria-label="Search players"
              className="grow"
            />
          </label>

          {truncated && (
            <span className="text-xs text-faint">
              showing the top {rows.length} of {total} by rating
            </span>
          )}
        </div>

        {renderBody()}

        <div className="mt-6">
          <Ratings2kAttribution />
        </div>
      </div>

      {selected && (
        <Rating2kModal
          slug={selected.slug}
          summary={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
};
