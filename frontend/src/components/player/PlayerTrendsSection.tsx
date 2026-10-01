import { useState } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { AnalyticsGameLog, AnalyticsTrends } from '../../types';
import { useChartColors } from '../../hooks/useChartColors';
import { formatStat } from '../../utils/stats';
import { chartNumber, deltaDisplay, formatGameDate, statLabel } from '../../utils/analytics';

interface PlayerTrendsSectionProps {
  trends: AnalyticsTrends;
}

// turnovers are the one category where a drop is an improvement, so the delta
// coloring is flipped for it.
const LOWER_IS_BETTER = new Set<string>(['tov']);

const TREND_TABS = ['pts', 'reb', 'ast', 'stl', 'blk', 'fg3m', 'tov', 'minutes'] as const;
type TrendTab = (typeof TREND_TABS)[number];

// the rolling payload shortens `minutes` to `min_`.
const rollingKey = (stat: TrendTab, window: 5 | 10): string =>
  `${stat === 'minutes' ? 'min' : stat}_r${window}`;

interface TrendPoint {
  date: string;
  value: number;
  r5: number | null;
  r10: number | null;
}

// rolling values are null on the leading games where the window is not full yet,
// which recharts renders as a gap rather than a drop to zero.
function buildTrendPoints(trends: AnalyticsTrends, stat: TrendTab): TrendPoint[] {
  const rollingByDate = new Map(trends.rolling.map((r) => [r.game_date, r]));
  const k5 = rollingKey(stat, 5);
  const k10 = rollingKey(stat, 10);
  return trends.games.map((game) => {
    const rolling = rollingByDate.get(game.game_date);
    return {
      date: formatGameDate(game.game_date),
      value: chartNumber(game[stat as keyof AnalyticsGameLog] as number),
      r5: rolling && rolling[k5] !== undefined ? chartNumber(rolling[k5] as number) : null,
      r10: rolling && rolling[k10] !== undefined ? chartNumber(rolling[k10] as number) : null,
    };
  });
}

export const PlayerTrendsSection = ({ trends }: PlayerTrendsSectionProps): JSX.Element | null => {
  const colors = useChartColors();
  const [stat, setStat] = useState<TrendTab>('pts');
  const points = buildTrendPoints(trends, stat);
  const hasCharts = points.length > 0;
  const hasForm = trends.last10_vs_season.length > 0;

  if (!hasCharts && !hasForm) return null;

  const axisTick = { fill: colors.content, fontSize: 10, opacity: 0.6 };
  const tooltipStyle = {
    background: colors.surface,
    border: `1px solid ${colors.grid}`,
    borderRadius: '2px',
    color: colors.content,
    fontSize: '0.75rem',
  };

  return (
    <section className="border-t border-base-300 pt-4">
      <div className="flex flex-col gap-4">
        <div>
          <h2 className="text-lg font-semibold">Trends</h2>
          <p className="text-xs text-muted mt-0.5">
            {hasCharts
              ? `Last ${points.length} games, oldest first, with trailing averages.`
              : 'Recent form against this player’s season baseline.'}
          </p>
        </div>

        {hasCharts && (
          <div>
            <div
              role="tablist"
              aria-label="Trend stat"
              className="flex flex-wrap gap-1 overflow-x-auto no-scrollbar mb-2"
            >
              {TREND_TABS.map((tab) => (
                <button
                  key={tab}
                  role="tab"
                  aria-selected={tab === stat}
                  onClick={() => setStat(tab)}
                  className={`btn btn-xs ${tab === stat ? 'btn-primary' : 'btn-ghost'}`}
                >
                  {statLabel(tab)}
                </button>
              ))}
            </div>

            <div className="h-52 w-full" data-testid="trend-chart">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: -22 }}>
                  <CartesianGrid stroke={colors.grid} strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="date" tick={axisTick} stroke={colors.grid} minTickGap={16} />
                  <YAxis tick={axisTick} stroke={colors.grid} allowDecimals={false} />
                  <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: colors.content }} />
                  <Legend wrapperStyle={{ fontSize: '0.7rem', color: colors.content }} />
                  <Line
                    type="monotone"
                    dataKey="value"
                    name={statLabel(stat)}
                    stroke={colors.content}
                    strokeOpacity={0.35}
                    strokeWidth={1}
                    dot={false}
                    isAnimationActive={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="r5"
                    name="5-game avg"
                    stroke={colors.primary}
                    strokeWidth={1.5}
                    dot={false}
                    connectNulls={false}
                    isAnimationActive={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="r10"
                    name="10-game avg"
                    stroke={colors.accent}
                    strokeWidth={1.5}
                    strokeDasharray="4 3"
                    dot={false}
                    connectNulls={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}

        {hasForm && (
          <div>
            <h3 className="text-sm font-semibold mb-1">Last 10 vs season</h3>
            <div className="overflow-x-auto border border-base-300">
              <table className="table table-xs w-full">
                <thead>
                  <tr>
                    <th>Stat</th>
                    <th className="text-right">Last 10</th>
                    <th className="text-right">Season</th>
                    <th className="text-right">Change</th>
                  </tr>
                </thead>
                <tbody>
                  {trends.last10_vs_season.map((row) => {
                    const display = deltaDisplay(row.delta, row.z, LOWER_IS_BETTER.has(row.stat));
                    return (
                      <tr key={row.stat} className={display.notable ? 'bg-base-200' : undefined}>
                        <td className="font-medium whitespace-nowrap">{statLabel(row.stat)}</td>
                        <td className="text-right tabular-nums">{formatStat(row.last10)}</td>
                        <td className="text-right tabular-nums">{formatStat(row.season)}</td>
                        <td className={`text-right tabular-nums whitespace-nowrap ${display.className}`}>
                          {display.text}
                          {row.z === null && (
                            <span className="ml-1 text-muted text-[10px]">small sample</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="text-[10px] text-faint mt-1">
              Highlighted rows moved more than one standard deviation from this player&apos;s own
              season baseline.
            </p>
          </div>
        )}
      </div>
    </section>
  );
};
