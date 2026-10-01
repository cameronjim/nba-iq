import type { AnalyticsPool, StatPercentile } from '../../types';
import { formatStat } from '../../utils/stats';
import { clampPercentile, ordinal, percentileTier, statHint, statLabel } from '../../utils/analytics';

interface PercentilePanelProps {
  percentiles: StatPercentile[];
  pool: AnalyticsPool;
}

// tailwind only emits classes it can see as literals, so the map is spelled out.
const TIER_CLASS = {
  success: 'progress-success',
  primary: 'progress-primary',
  error: 'progress-error',
} as const;

// turnover percentiles arrive already inverted, so every bar reads further-right-is-better.
export const PercentilePanel = ({ percentiles, pool }: PercentilePanelProps): JSX.Element => (
  <section className="border-t border-base-300 pt-4">
    <div className="flex flex-col gap-3">
      <div>
        <h2 className="text-lg font-semibold">Category Percentiles</h2>
        <p className="text-xs text-muted mt-0.5">
          vs {pool.label} · {pool.definition} · n={formatStat(pool.sample_size, 0)}
        </p>
      </div>

      {percentiles.length === 0 ? (
        <p className="text-sm text-muted py-4">No percentile data for this player yet.</p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {percentiles.map((row) => {
            const pct = clampPercentile(row.percentile);
            const hint = statHint(row.stat);
            const label = statLabel(row.stat);
            return (
              <li
                key={row.stat}
                className="grid grid-cols-[72px_1fr_auto] items-center gap-2 sm:gap-3"
              >
                <span className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider">
                  <span className="truncate text-muted">{label}</span>
                  {hint && (
                    <span
                      className="tooltip tooltip-right cursor-help text-muted"
                      data-tip={hint}
                      role="img"
                      aria-label={`${label} explanation`}
                    >
                      ?
                    </span>
                  )}
                </span>

                <span className="flex items-center gap-2 min-w-0">
                  <progress
                    className={`progress ${TIER_CLASS[percentileTier(pct)]} w-full h-2.5`}
                    value={pct}
                    max={100}
                    aria-label={`${label} percentile`}
                  />
                  <span className="text-xs tabular-nums text-muted w-9 shrink-0 text-right">
                    {ordinal(pct)}
                  </span>
                </span>

                <span className="text-sm font-semibold tabular-nums w-12 text-right">
                  {formatStat(row.value)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  </section>
);
