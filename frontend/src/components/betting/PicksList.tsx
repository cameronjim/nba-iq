import { IconRefresh } from '../icons';
import { SkeletonLines } from '../Skeleton';
import { PickRow } from './PickRow';
import { formatAmerican } from '../../utils/formatOdds';
import { matchupWords, straightBetText } from '../../utils/bettingCopy';
import type { BettingPick, BettingPicksResponse, ParlaySuggestion } from '../../types';

interface PicksListProps {
  picks: BettingPicksResponse | null;
  loading: boolean;
  refreshing: boolean;
  error: string;
  onReload: (refresh?: boolean) => void;
}

const CATEGORY_ORDER: Array<BettingPick['category']> = ['best_value', 'safe', 'hail_mary'];

const formatCacheTime = (iso: string): string =>
  new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });

const ParlayIdea = ({ parlay }: { parlay: ParlaySuggestion }): JSX.Element => (
  <details className="border-t border-base-300 pt-3">
    <summary className="cursor-pointer text-sm font-semibold w-fit">Claude's parlay idea</summary>
    <div className="space-y-2 mt-2">
      <ul className="space-y-1">
        {parlay.legs.map((leg) => (
          <li key={`${leg.game_id}-${leg.market}-${leg.selection}`} className="text-sm">
            {straightBetText(leg)}, {matchupWords(leg.matchup)}.
          </li>
        ))}
      </ul>
      <p className="text-sm">
        All {parlay.legs.length} legs must win. Together they pay {formatAmerican(parlay.combined_american)};
        the price implies they all hit about {Math.round(parlay.combined_implied_prob * 100)}% of the time.
      </p>
      {parlay.rationale && <p className="text-xs text-muted">Claude's reasoning: {parlay.rationale}</p>}
      <p className="text-xs text-warning">{parlay.ev_note}</p>
    </div>
  </details>
);

export const PicksList = ({ picks, loading, refreshing, error, onReload }: PicksListProps): JSX.Element => {
  if (loading) {
    return <SkeletonLines lines={6} label="Loading picks" />;
  }

  if (error) {
    return (
      <p className="text-sm">
        <span className="text-error">Couldn't load Claude's picks.</span>{' '}
        <button onClick={() => onReload()} className="btn btn-ghost btn-xs">Try again</button>
      </p>
    );
  }

  if (!picks || picks.no_games) {
    return <p className="text-sm text-muted">No games with posted odds in the next two days.</p>;
  }

  const ordered = CATEGORY_ORDER.flatMap((category) => picks.picks.filter((p) => p.category === category));

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-faint">
          {picks.cached_at ? `Last updated ${formatCacheTime(picks.cached_at)}` : ''}
        </span>
        <button onClick={() => onReload(true)} disabled={refreshing} className="btn btn-ghost btn-xs gap-1.5">
          <IconRefresh size={12} />
          {refreshing ? 'Re-analyzing' : 'Re-analyze'}
        </button>
      </div>

      {picks.summary && (
        <p className="text-sm">
          <span className="font-semibold">Claude's read:</span> {picks.summary}
        </p>
      )}

      {ordered.length === 0 ? (
        <p className="text-sm text-muted">Claude did not find a pick worth making on this slate.</p>
      ) : (
        <ul>
          {ordered.map((pick) => (
            <PickRow key={`${pick.category}-${pick.game_id}-${pick.market}-${pick.selection}`} pick={pick} />
          ))}
        </ul>
      )}

      {picks.parlay && <ParlayIdea parlay={picks.parlay} />}
    </div>
  );
};
