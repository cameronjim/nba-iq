import { REASON_META, REASON_ORDER, ReasonBadge } from './ReasonBadge';

export const WatchlistLegend = ({ days }: { days: number }): JSX.Element => (
  <section className="border-t border-base-300 pt-3">
    <h2 className="font-display text-lg font-semibold uppercase tracking-wide mb-2">
      What the badges mean
    </h2>
    <ul className="flex flex-col divide-y divide-base-300">
      {REASON_ORDER.map((reason) => (
        <li key={reason} className="flex items-start gap-3 py-1.5">
          <span className="shrink-0 w-28">
            <ReasonBadge reason={reason} />
          </span>
          <span className="text-xs text-muted">{REASON_META[reason].description}</span>
        </li>
      ))}
      <li className="flex items-start gap-3 py-1.5">
        <span className="shrink-0 w-28">
          <span className="badge badge-outline badge-sm font-semibold">PG/SG</span>
        </span>
        <span className="text-xs text-muted">
          Every position he is listed at, so &ldquo;PG/SG&rdquo; shows up under both Guards and PG.
        </span>
      </li>
      {days > 1 && (
        <li className="flex items-start gap-3 py-1.5">
          <span className="shrink-0 w-28">
            <span className="badge badge-outline badge-sm">4 games</span>
          </span>
          <span className="text-xs text-muted">
            Games projected in the window. The score adds them up, so more games ranks higher.
          </span>
        </li>
      )}
    </ul>
    <p className="text-xs text-muted pt-2 border-t border-base-300">
      Badges explain a row; they do not rank it.
    </p>
  </section>
);

// always visible rather than a tooltip: a reader who does not know what the score
// means will not hover to find out.
export const RankingNote = ({ days }: { days: number }): JSX.Element => (
  <p className="text-xs text-muted" data-testid="ranking-note">
    {days > 1 ? (
      <span>
        Ranked by how far above his usual each game projects,{' '}
        <strong className="font-semibold">added up</strong> over the window. So more games can
        outrank a better player with fewer. Open a row to see every game.
      </span>
    ) : (
      <span>
        Each row shows his usual minutes and where tonight projects. The score on the right is that
        gap, weighted by how much it matters tonight.
      </span>
    )}
  </p>
);
