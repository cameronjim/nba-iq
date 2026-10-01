import { useState } from 'react';
import { IconSearch } from '../components/icons';
import { SkeletonLines, SkeletonTable } from '../components/Skeleton';
import { SeasonPlayerTable } from '../components/player/SeasonPlayerTable';
import { PlayerCareerModal } from '../components/player/PlayerCareerModal';
import { getHistoryPlayers, getHistorySeasons, type HistoryPlayersResponse } from '../api/client';
import { useCachedResource } from '../hooks/useCachedResource';
import { CACHE_KEYS, historyPlayersKey } from '../api/resourceCache';
import type { PlayerSeasonRow } from '../types';

// must clear the largest season outright or the tail is unreachable: modern seasons
// run past 500 rows, and the api ceiling for this route is 1000.
const SEASON_ROW_LIMIT = 1000;

export const HistoryPage = (): JSX.Element => {
  const [selectedSeason, setSelectedSeason] = useState('');
  const [search, setSearch] = useState('');
  const [selectedPlayer, setSelectedPlayer] = useState<PlayerSeasonRow | null>(null);

  const {
    data: seasons,
    loading: loadingSeasons,
    error: seasonsError,
    reload: reloadSeasons,
  } = useCachedResource<string[]>(CACHE_KEYS.historySeasons, getHistorySeasons, {
    errorMessage: 'Failed to load historical seasons',
  });

  const seasonList = seasons ?? [];
  // the api returns seasons newest first, so entry 0 is the default.
  const activeSeason = selectedSeason || seasonList[0] || '';

  const {
    data: seasonData,
    loading: loadingRows,
    error: rowsError,
    reload: reloadRows,
  } = useCachedResource<HistoryPlayersResponse>(
    historyPlayersKey(activeSeason),
    () => getHistoryPlayers({ season: activeSeason, limit: SEASON_ROW_LIMIT }),
    { enabled: !!activeSeason, errorMessage: 'Failed to load season stats' }
  );

  const rows = seasonData?.players ?? [];
  const term = search.trim().toLowerCase();
  const filtered = term
    ? rows.filter((row) => (row.player_name ?? '').toLowerCase().includes(term))
    : rows;
  const total = seasonData?.total ?? 0;
  const truncated = total > rows.length;

  const renderBody = (): JSX.Element => {
    if (loadingRows) {
      return <SkeletonTable rows={12} cols={10} label="Loading season stats" />;
    }

    if (rowsError) {
      return (
        <div className="flex flex-col items-center py-12 gap-4 border border-base-300">
          <p className="text-error text-sm">{rowsError}</p>
          <button onClick={() => void reloadRows()} className="btn btn-primary btn-sm">
            Try Again
          </button>
        </div>
      );
    }

    return <SeasonPlayerTable rows={filtered} onSelect={setSelectedPlayer} />;
  };

  return (
    <div className="pb-20">
      <div className="max-w-[1400px] mx-auto px-4 py-6">
        <h1 className="font-display text-3xl font-semibold uppercase tracking-wide mb-1">
          Season History
        </h1>
        <p className="text-sm text-muted mb-5">
          Per-game averages for every player, season by season. Click a player for his other
          seasons.
        </p>

        {loadingSeasons ? (
          <SkeletonLines lines={2} label="Loading seasons" />
        ) : seasonsError ? (
          <div className="flex flex-col items-center py-12 gap-4 border border-base-300">
            <p className="text-error text-sm">{seasonsError}</p>
            <button onClick={() => void reloadSeasons()} className="btn btn-primary btn-sm">
              Try Again
            </button>
          </div>
        ) : seasonList.length === 0 ? (
          <div className="flex flex-col items-center text-center py-12 gap-2 border border-base-300">
            <p className="font-semibold">No historical data available yet</p>
            <p className="text-sm text-muted max-w-md">
              Season-by-season stats show up here once historical seasons have been
              imported. Current-season stats are on the Stats tab in the meantime.
            </p>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-3 mb-5">
              <select
                value={activeSeason}
                onChange={(e) => setSelectedSeason(e.target.value)}
                className="select select-bordered select-sm w-[140px]"
                aria-label="Season"
              >
                {seasonList.map((season) => (
                  <option key={season} value={season}>{season}</option>
                ))}
              </select>

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
                  showing the top {rows.length} of {total}
                </span>
              )}
            </div>

            {renderBody()}
          </>
        )}
      </div>

      {selectedPlayer && (
        <PlayerCareerModal
          playerName={selectedPlayer.player_name}
          nbaPlayerId={selectedPlayer.nba_player_id}
          onClose={() => setSelectedPlayer(null)}
        />
      )}
    </div>
  );
};
