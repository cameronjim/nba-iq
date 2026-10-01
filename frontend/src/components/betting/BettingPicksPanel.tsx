import { IconRefresh } from '../icons';
import { SkeletonBlock, SkeletonLines } from '../Skeleton';
import { formatAmerican, formatPercent, formatSignedPercent } from '../../utils/formatOdds';
import type { BettingPick, BettingPicksResponse } from '../../types';

interface BettingPicksPanelProps {
  picks: BettingPicksResponse | null;
  loading: boolean;
  refreshing: boolean;
  error: string;
  onReload: (refresh?: boolean) => void;
}

const CATEGORY_META = {
  best_value: { label: 'Best Value' },
  safe: { label: 'Safe' },
  hail_mary: { label: 'Hail Mary' },
} as const;

const PickItem = ({ pick }: { pick: BettingPick }) => {
  const edgePositive = pick.edge >= 0;

  return (
    <li className="py-3 border-t border-base-300 first:border-t-0 space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <div className="min-w-0 font-semibold text-sm">
          {pick.selection_label}
          <span className="text-muted font-normal ml-1.5 tabular">{formatAmerican(pick.american_odds)}</span>
        </div>
        <span
          className="text-xs text-muted whitespace-nowrap shrink-0"
          title="How confident Claude is in this pick"
        >
          {pick.confidence} confidence
        </span>
      </div>
      <div className="text-xs text-muted">{pick.matchup} · {pick.tipoff}</div>

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs tabular">
        <span title="What the sportsbook's price implies this outcome's chance is">
          <span className="text-muted">Book says</span>{' '}
          <span className="font-medium">{formatPercent(pick.implied_prob)}</span>
        </span>
        <span title="Claude's estimate of the true win probability">
          <span className="text-muted">Claude says</span>{' '}
          <span className="font-medium">{formatPercent(pick.estimated_win_prob)}</span>
        </span>
        <span title="Claude's estimate minus the no-vig implied probability when both sides are priced. Positive means potential value.">
          <span className="text-muted">Edge</span>{' '}
          <span className={`font-semibold ${edgePositive ? 'text-success' : 'text-error'}`}>
            {formatSignedPercent(pick.edge)}
          </span>{' '}
          <span className="text-faint" title="A language-model estimate, not a trained model output">
            Claude estimate
          </span>
        </span>
      </div>

      <p className="text-sm text-muted leading-relaxed">
        <span className="font-medium text-base-content">Claude's reasoning:</span> {pick.rationale}
      </p>
    </li>
  );
};

const PicksLoading = () => (
  <div role="status" aria-label="Loading picks" className="space-y-6">
    {[0, 1, 2].map((i) => (
      <div key={i} className="space-y-3">
        <SkeletonBlock className="h-5 w-28" />
        <SkeletonLines lines={3} label="Loading pick" />
      </div>
    ))}
  </div>
);

export const BettingPicksPanel = ({ picks, loading, refreshing, error, onReload }: BettingPicksPanelProps) => {
  if (loading) {
    return <PicksLoading />;
  }

  if (error) {
    return (
      <div className="border border-base-300 py-8 flex flex-col items-center gap-3">
        <p className="text-error text-sm">{error}</p>
        <button onClick={() => onReload()} className="btn btn-primary btn-sm">Try Again</button>
      </div>
    );
  }

  if (!picks || picks.no_games) {
    return (
      <div className="border border-base-300 py-8 text-center space-y-1">
        <p className="font-semibold text-sm">No bettable games right now</p>
        <p className="text-xs text-muted">
          Sportsbooks haven't posted lines for upcoming games yet. Check back closer to game day.
        </p>
      </div>
    );
  }

  const formatCacheTime = (iso: string): string =>
    new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          {picks.cached_at && (
            <span className="text-xs text-faint">Last updated {formatCacheTime(picks.cached_at)}</span>
          )}
        </div>
        <button onClick={() => onReload(true)} disabled={refreshing} className="btn btn-ghost btn-xs gap-1.5">
          <IconRefresh size={12} />
          {refreshing ? 'Re-analyzing' : 'Re-analyze'}
        </button>
      </div>

      {picks.summary && (
        <p className="text-sm leading-relaxed">
          <span className="font-semibold">Claude's read:</span> {picks.summary}
        </p>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-x-8 gap-y-6">
        {(Object.keys(CATEGORY_META) as Array<keyof typeof CATEGORY_META>).map((category) => {
          const meta = CATEGORY_META[category];
          const categoryPicks = picks.picks.filter((p) => p.category === category);
          return (
            <section key={category}>
              <h2 className="font-display text-xl font-semibold uppercase tracking-wide border-b border-base-300 pb-1">
                {meta.label}
              </h2>
              {categoryPicks.length === 0 ? (
                <p className="text-xs text-muted py-4">No {meta.label.toLowerCase()} picks on this slate.</p>
              ) : (
                <ul>
                  {categoryPicks.map((pick) => (
                    <PickItem key={`${pick.game_id}-${pick.market}-${pick.selection}`} pick={pick} />
                  ))}
                </ul>
              )}
            </section>
          );
        })}
      </div>

      {picks.parlay && (
        <section className="border-t border-base-300 pt-4 space-y-3">
          <div className="flex flex-wrap items-baseline gap-x-3">
            <h2 className="font-display text-xl font-semibold uppercase tracking-wide">Suggested Parlay</h2>
            <span className="font-semibold tabular">{formatAmerican(picks.parlay.combined_american)}</span>
            <span className="text-xs text-muted tabular" title="Combined implied probability of all legs hitting">
              {formatPercent(picks.parlay.combined_implied_prob)} to hit
            </span>
          </div>
          <ul className="space-y-1.5">
            {picks.parlay.legs.map((leg) => (
              <li key={`${leg.game_id}-${leg.market}-${leg.selection}`} className="text-sm flex items-baseline gap-2">
                <span className="font-medium">{leg.selection_label}</span>
                <span className="text-xs text-muted tabular">{leg.matchup} · {formatAmerican(leg.american_odds)}</span>
              </li>
            ))}
          </ul>
          <p className="text-sm text-muted leading-relaxed">
            <span className="font-medium text-base-content">Claude's reasoning:</span> {picks.parlay.rationale}
          </p>
          <p className="text-xs text-warning leading-relaxed">{picks.parlay.ev_note}</p>
        </section>
      )}
    </div>
  );
};
