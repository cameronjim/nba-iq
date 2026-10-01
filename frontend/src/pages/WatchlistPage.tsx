import { useState } from 'react';
import { Link } from 'react-router-dom';
import { getWatchlist } from '../api/client';
import { useCachedResource } from '../hooks/useCachedResource';
import { formatRange, todayInEastern } from '../utils/dates';
import { WatchlistRow } from '../components/watchlist/WatchlistRow';
import { WatchlistSkeleton } from '../components/watchlist/WatchlistSkeleton';
import { RankingNote, WatchlistLegend } from '../components/watchlist/WatchlistLegend';
import {
  DEFAULT_WINDOW_DAYS,
  POSITION_LABELS,
  POSITION_PRIMARY,
  POSITION_SECONDARY,
  PositionPicker,
  WindowPicker,
} from '../components/watchlist/WatchlistFilters';
import type { WatchlistPositionFilter, WatchlistResponse } from '../types';

const NO_CANDIDATES = 'Nobody is projected above their own usual tonight';
const NO_CANDIDATES_WINDOW = 'Nobody is projected above their own usual in this window';
const NO_RUN_NOTICE = 'No prediction run yet. Check back after the next model run.';

export const WatchlistPage = (): JSX.Element => {
  const [date, setDate] = useState(todayInEastern);
  const [days, setDays] = useState(DEFAULT_WINDOW_DAYS);
  const [position, setPosition] = useState<WatchlistPositionFilter | null>(null);
  // client-side only, unlike `position`: the watchlist never returns more than a
  // couple dozen rows, so filtering by team in the browser is effectively free.
  const [teamFilter, setTeamFilter] = useState('');

  const { data, loading, error, reload } = useCachedResource<WatchlistResponse>(
    `watchlist:${date}:${days}:${position ?? 'any'}`,
    () => getWatchlist(date, days, position),
    { errorMessage: 'Failed to load the watchlist' }
  );

  const baseline = data?.baseline ?? null;
  // the resolved window is the SERVER's, so while a new window is in flight the rows
  // still holding the previous payload are never captioned with the new one.
  const from = data?.window.from ?? date;
  const to = data?.window.to ?? date;
  const shownDays = data?.window.days ?? days;
  const positionOptions = data?.position_options ?? POSITION_PRIMARY.concat(POSITION_SECONDARY);
  const unplaced = data?.position_coverage.unknown ?? 0;

  const players = data?.players ?? [];
  const teamAbbrs = [...new Set(players.map((p) => p.team_abbr).filter((t): t is string => t !== null))].sort();
  const visiblePlayers = teamFilter ? players.filter((p) => p.team_abbr === teamFilter) : players;

  return (
    <div className="max-w-[900px] mx-auto px-4 py-6 pb-20">
      <header className="flex flex-wrap items-end justify-between gap-3 mb-3">
        <div>
          <h1 className="font-display text-3xl font-semibold uppercase tracking-wide leading-tight">
            Watchlist
          </h1>
          <p className="text-sm text-muted mt-0.5">
            Players projected to do more than they usually do.
          </p>
        </div>

        <label className="form-control">
          <span className="sr-only">Window start date</span>
          <input
            type="date"
            className="input input-bordered input-sm"
            value={date}
            aria-label="Window start date"
            onChange={(e) => setDate(e.target.value || todayInEastern())}
          />
        </label>
      </header>

      <section className="flex flex-col gap-2 mb-5" aria-label="Window and position">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <WindowPicker days={days} onChange={setDays} />
          <span
            className="text-xs text-muted tabular-nums"
            data-testid="window-range"
          >
            {formatRange(from, to)}
            {shownDays > 1 && <span className="text-faint"> · {shownDays} days</span>}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {/* client-side only: it filters the rows already on screen rather than
              adding a team parameter to the request. */}
          <select
            value={teamFilter}
            onChange={(e) => setTeamFilter(e.target.value)}
            className="select select-bordered select-sm w-[160px]"
            aria-label="Filter by team"
          >
            <option value="">All Teams</option>
            {teamAbbrs.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <PositionPicker value={position} options={positionOptions} onChange={setPosition} />
        </div>
      </section>

      {loading && !data ? (
        <WatchlistSkeleton />
      ) : !data ? (
        <div className="card border border-base-300">
          <div className="card-body flex flex-col items-center py-12 gap-4">
            <p className="text-error text-sm">{error || 'Failed to load the watchlist'}</p>
            <button onClick={() => void reload()} className="btn btn-primary btn-sm">
              Try Again
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {!data.run && (
            <div className="alert py-2.5 px-3">
              <span className="text-sm">{NO_RUN_NOTICE}</span>
            </div>
          )}

          {players.length === 0 && data.position !== null ? (
            /* a position filter that emptied the list is its own state: the model
               has plenty to say, just not about this slot. */
            <div className="card border border-base-300">
              <div className="card-body items-center text-center py-12 gap-1">
                <p className="text-sm font-semibold">
                  No {POSITION_LABELS[data.position].toLowerCase()} clear the bar{' '}
                  {shownDays > 1 ? 'in this window' : 'tonight'}
                </p>
                <p className="text-xs text-muted max-w-md">
                  Try a longer window, a wider slot, or{' '}
                  <button
                    type="button"
                    className="link link-primary"
                    onClick={() => setPosition(null)}
                  >
                    every position
                  </button>
                  .
                  {unplaced > 0 && (
                    <>
                      {' '}
                      {unplaced} projected player{unplaced === 1 ? '' : 's'}{' '}
                      {unplaced === 1 ? 'has' : 'have'} no position on record.
                    </>
                  )}
                </p>
              </div>
            </div>
          ) : players.length === 0 ? (
            <div className="card border border-base-300">
              <div className="card-body items-center text-center py-12 gap-1">
                <p className="text-sm font-semibold">
                  {shownDays > 1 ? NO_CANDIDATES_WINDOW : NO_CANDIDATES}
                </p>
                <p className="text-xs text-muted max-w-md">
                  {data.run
                    ? 'That is a normal answer on a quiet stretch.'
                    : 'Check back after the next model run.'}{' '}
                  <Link to="/projections" className="link link-primary">
                    The projections
                  </Link>{' '}
                  still rank who is best tonight.
                </p>
              </div>
            </div>
          ) : visiblePlayers.length === 0 ? (
            /* the team filter is client-side only, so an empty result here is this
               page's own filter rather than the model's answer. */
            <div className="card border border-base-300">
              <div className="card-body items-center text-center py-8 gap-1">
                <p className="text-sm font-semibold">No {teamFilter} players in this window</p>
                <p className="text-xs text-muted">
                  <button
                    type="button"
                    className="link link-primary"
                    onClick={() => setTeamFilter('')}
                  >
                    Clear the team filter
                  </button>{' '}
                  to see every team.
                </p>
              </div>
            </div>
          ) : (
            <>
              <RankingNote days={shownDays} />
              {data.position !== null && (
                <p className="text-[11px] text-muted" data-testid="position-note">
                  Showing {POSITION_LABELS[data.position].toLowerCase()} only.
                  {unplaced > 0 &&
                    ` ${unplaced} projected player${unplaced === 1 ? '' : 's'} ${unplaced === 1 ? 'has' : 'have'} no position on record.`}
                </p>
              )}
              <ul className="flex flex-col border-t border-base-300">
                {visiblePlayers.map((player, index) => (
                  <WatchlistRow
                    key={player.nba_player_id}
                    player={player}
                    rank={index + 1}
                    days={shownDays}
                  />
                ))}
              </ul>
            </>
          )}

          <WatchlistLegend days={shownDays} />

          {/* the label comes from the server, so this page never names a baseline
              the numbers were not actually computed against. */}
          <footer className="text-[11px] text-muted pt-1 flex flex-col gap-1">
            {baseline?.label && <span>&ldquo;Usual&rdquo; means {baseline.label}.</span>}
            {shownDays > 1 && (
              <span>
                Over a window, minutes and points are per-game averages; impact and score are
                totals.
              </span>
            )}
          </footer>
        </div>
      )}
    </div>
  );
};
