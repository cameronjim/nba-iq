import { useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { AnalyticsPool, StatDistribution, StatPercentile } from '../../types';
import { useChartColors } from '../../hooks/useChartColors';
import { formatStat } from '../../utils/stats';
import { bucketLabel, chartNumber, ordinal, clampPercentile, statLabel } from '../../utils/analytics';

interface StatDistributionSectionProps {
  distributions: StatDistribution[];
  percentiles: StatPercentile[];
  pool: AnalyticsPool;
}

// deliberately no fitted bell curve: these distributions are skewed, and an overlaid
// normal would imply a symmetry the data does not have.
export const StatDistributionSection = ({
  distributions,
  percentiles,
  pool,
}: StatDistributionSectionProps): JSX.Element | null => {
  const [selected, setSelected] = useState<string>(distributions[0]?.stat ?? '');
  const colors = useChartColors();

  if (distributions.length === 0) return null;

  const active = distributions.find((d) => d.stat === selected) ?? distributions[0];
  const percentile = percentiles.find((p) => p.stat === active.stat);
  const playerValue = chartNumber(active.player_value);

  const data = active.buckets.map((bucket) => ({
    label: bucketLabel(bucket.lo, bucket.hi),
    count: chartNumber(bucket.count),
    lo: chartNumber(bucket.lo),
    hi: chartNumber(bucket.hi),
  }));

  // a categorical axis needs a category to anchor the marker to, so find the bucket
  // the player lands in (the last one for an out-of-range high).
  const playerBucket =
    data.find((b) => playerValue >= b.lo && playerValue < b.hi) ??
    (playerValue >= (data[data.length - 1]?.hi ?? 0) ? data[data.length - 1] : data[0]);

  return (
    <section className="border-t border-base-300 pt-4">
      <div className="flex flex-col gap-3">
        <div>
          <h2 className="text-lg font-semibold">Distribution</h2>
          <p className="text-xs text-muted mt-0.5">
            How {pool.label} are spread across each category, counted from real games.
          </p>
        </div>

        <div
          role="tablist"
          aria-label="Distribution stat"
          className="flex flex-wrap gap-1 overflow-x-auto no-scrollbar"
        >
          {distributions.map((dist) => (
            <button
              key={dist.stat}
              role="tab"
              aria-selected={dist.stat === active.stat}
              onClick={() => setSelected(dist.stat)}
              className={`btn btn-xs ${dist.stat === active.stat ? 'btn-primary' : 'btn-ghost'}`}
            >
              {statLabel(dist.stat)}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs">
          <span>
            <span className="text-muted">This player </span>
            <span className="font-semibold tabular-nums">{formatStat(active.player_value)}</span>
          </span>
          {percentile && (
            <span>
              <span className="text-muted">Percentile </span>
              <span className="font-semibold tabular-nums">
                {ordinal(clampPercentile(percentile.percentile))}
              </span>
            </span>
          )}
          <span className="text-muted tabular-nums">
            pool mean {formatStat(active.mean)} · sd {formatStat(active.stddev)}
          </span>
        </div>

        <div className="h-56 w-full" data-testid="distribution-chart">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 16, right: 8, bottom: 4, left: -20 }}>
              <CartesianGrid stroke={colors.grid} strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="label"
                tick={{ fill: colors.content, fontSize: 10, opacity: 0.6 }}
                stroke={colors.grid}
                interval="preserveStartEnd"
              />
              <YAxis
                tick={{ fill: colors.content, fontSize: 10, opacity: 0.6 }}
                stroke={colors.grid}
                allowDecimals={false}
              />
              <Tooltip
                cursor={{ fill: colors.grid, opacity: 0.3 }}
                contentStyle={{
                  background: colors.surface,
                  border: `1px solid ${colors.grid}`,
                  borderRadius: '2px',
                  color: colors.content,
                  fontSize: '0.75rem',
                }}
                labelStyle={{ color: colors.content }}
                formatter={(value) => [`${chartNumber(String(value))} players`, statLabel(active.stat)]}
              />
              <Bar dataKey="count" fill={colors.content} fillOpacity={0.35} isAnimationActive={false} />
              {playerBucket && (
                <ReferenceLine
                  x={playerBucket.label}
                  stroke={colors.primary}
                  strokeWidth={1.5}
                  label={{
                    value: percentile
                      ? `${formatStat(active.player_value)} · ${ordinal(clampPercentile(percentile.percentile))}`
                      : formatStat(active.player_value),
                    position: 'top',
                    fill: colors.primary,
                    fontSize: 10,
                  }}
                />
              )}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </section>
  );
};
