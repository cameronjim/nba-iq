import { Link, useParams } from 'react-router-dom';
import { IconChevronLeft } from '../components/icons';
import { SkeletonBlock, SkeletonLines, SkeletonTable } from '../components/Skeleton';
import { PercentilePanel } from '../components/player/PercentilePanel';
import { StatDistributionSection } from '../components/player/StatDistributionSection';
import { PlayerTrendsSection } from '../components/player/PlayerTrendsSection';
import { RecentGamesTable } from '../components/player/RecentGamesTable';
import { PlayerPredictionCard } from '../components/player/PlayerPredictionCard';
import { PlayerUpcomingGames } from '../components/player/PlayerUpcomingGames';
import { PlayerCareerSection } from '../components/player/PlayerCareerSection';
import { InjuryBadge } from '../components/player/InjuryBadge';
import { getPlayerAnalytics, getPlayerPredictions } from '../api/client';
import { useCachedResource } from '../hooks/useCachedResource';
import { playerAnalyticsKey, playerPredictionsKey } from '../api/resourceCache';
import { formatTimestamp } from '../utils/analytics';
import type { PlayerAnalytics, PlayerPredictionsResponse } from '../types';

const BackLink = (): JSX.Element => (
  <Link to="/stats" className="btn btn-ghost btn-xs gap-1 -ml-2 mb-2">
    <IconChevronLeft size={14} />
    Back to stats
  </Link>
);

export const PlayerPage = (): JSX.Element => {
  const { id } = useParams<{ id: string }>();
  const playerId = Number(id);
  const valid = Number.isInteger(playerId) && playerId > 0;

  const { data, error, reload } = useCachedResource<PlayerAnalytics>(
    playerAnalyticsKey(playerId),
    () => getPlayerAnalytics(playerId),
    { enabled: valid, errorMessage: 'Failed to load player analytics' }
  );

  // a separate request on purpose, so a failure here renders nothing rather than
  // taking the page down. `from` is deliberately not passed: with no date filter the
  // section stays non-empty while the only published run is a backtest.
  const { data: predictions, loading: loadingPredictions } = useCachedResource<PlayerPredictionsResponse>(
    playerPredictionsKey(playerId),
    () => getPlayerPredictions(playerId),
    { enabled: valid, errorMessage: 'Failed to load upcoming predictions' }
  );

  if (!valid) {
    return (
      <div className="max-w-[900px] mx-auto px-4 py-6">
        <BackLink />
        <div className="flex flex-col items-center text-center py-12 gap-2 border border-base-300">
          <p className="font-semibold">Unknown player</p>
          <p className="text-sm text-muted">That link doesn&apos;t point at a player we have.</p>
        </div>
      </div>
    );
  }

  // a cached copy keeps rendering through a failed background refresh.
  if (!data) {
    if (error) {
      return (
        <div className="max-w-[900px] mx-auto px-4 py-6">
          <BackLink />
          <div className="flex flex-col items-center py-12 gap-4 border border-base-300">
            <p className="text-error text-sm">{error}</p>
            <button onClick={() => void reload()} className="btn btn-primary btn-sm">
              Try Again
            </button>
          </div>
        </div>
      );
    }
    return (
      <div className="max-w-[900px] mx-auto px-4 py-6 flex flex-col gap-5">
        <BackLink />
        <div className="flex items-start gap-4">
          <SkeletonBlock className="h-16 w-16" />
          <div className="flex-1">
            <SkeletonLines lines={3} label="Loading player" />
          </div>
        </div>
        <SkeletonTable rows={5} cols={7} label="Loading player stats" />
      </div>
    );
  }

  const { player, as_of: asOf, pool, percentiles, distributions, trends, prediction } = data;
  const logsAt = formatTimestamp(asOf.logs);
  const distributionsAt = formatTimestamp(asOf.distributions);
  const noTrends = trends.games.length === 0 && trends.last10_vs_season.length === 0;

  return (
    <div className="pb-20">
      <div className="max-w-[900px] mx-auto px-4 py-6">
        <BackLink />

        <header className="flex items-start gap-3 sm:gap-4 mb-5">
          {player.headshot_url && (
            <div className="avatar shrink-0">
              <div className="w-14 sm:w-16 rounded-box border border-base-300">
                <img
                  src={player.headshot_url}
                  alt={player.name}
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = 'none';
                  }}
                />
              </div>
            </div>
          )}
          <div className="min-w-0">
            <h1 className="font-display text-3xl font-semibold uppercase tracking-wide leading-tight">
              {player.name}
            </h1>
            <p className="text-sm text-muted">
              {[player.team, player.position].filter(Boolean).join(' · ')}
            </p>
            {player.injury_status && (
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 mt-2">
                <InjuryBadge status={player.injury_status} detail={player.injury_detail} showDetail />
              </div>
            )}
          </div>
        </header>

        <div className="flex flex-col gap-5">
          {prediction && <PlayerPredictionCard prediction={prediction} />}

          {loadingPredictions && !predictions ? (
            <SkeletonTable rows={4} cols={6} label="Loading upcoming games" />
          ) : (
            <PlayerUpcomingGames data={predictions} />
          )}

          <PercentilePanel percentiles={percentiles} pool={pool} />

          <StatDistributionSection
            distributions={distributions}
            percentiles={percentiles}
            pool={pool}
          />

          {noTrends ? (
            <div className="border-t border-base-300 pt-4 flex flex-col items-center text-center gap-1">
              <p className="text-sm font-semibold">No game logs yet</p>
              <p className="text-xs text-muted max-w-md">
                Per-game trends and recent box scores appear here once this player&apos;s game
                logs have been ingested. The percentiles above are unaffected.
              </p>
            </div>
          ) : (
            <>
              <PlayerTrendsSection trends={trends} />
              <RecentGamesTable games={trends.games} />
            </>
          )}

          <PlayerCareerSection nbaPlayerId={player.nba_id} framed />
        </div>

        <footer className="text-[11px] text-faint mt-6 pt-3 border-t border-base-300 flex flex-wrap gap-x-4 gap-y-1">
          <span>Game logs as of {logsAt ?? 'no game logs yet'}</span>
          <span>Distributions as of {distributionsAt ?? 'unknown'}</span>
        </footer>
      </div>
    </div>
  );
};
