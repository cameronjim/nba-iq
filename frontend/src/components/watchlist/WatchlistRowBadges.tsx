import { formatStat, toStatNumber } from '../../utils/stats';
import { availabilityBadge } from '../../utils/predictions';
import type { DeviationStat, UpsideDriver, WatchlistPlayer } from '../../types';

// short labels matching the Projections category line.
const DRIVER_LABELS: Record<DeviationStat, string> = {
  minutes: 'MIN',
  pts: 'PTS',
  reb: 'REB',
  ast: 'AST',
  stl: 'STL',
  blk: 'BLK',
  fg3m: '3PM',
};

// rendered as an arrow rather than a signed number because the direction is the point.
export const DeltaPair = ({ player }: { player: WatchlistPlayer }): JSX.Element | null => {
  const usualMin = toStatNumber(player.minutes.usual);
  const projMin = toStatNumber(player.minutes.projected);
  const ptsDelta = toStatNumber(player.points.delta);

  if (usualMin === null && ptsDelta === null) return null;

  return (
    <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs tabular-nums">
      {usualMin !== null && projMin !== null && (
        <span
          className="font-semibold whitespace-nowrap"
          title={`Usually ${usualMin.toFixed(1)} min, tonight ${projMin.toFixed(1)}.`}
        >
          {usualMin.toFixed(0)} <span className="text-faint">→</span> {projMin.toFixed(0)} min
        </span>
      )}
      {ptsDelta !== null && (
        <span
          className="text-muted whitespace-nowrap"
          title={`Projected ${formatStat(player.points.projected)} points, usually ${formatStat(player.points.usual)}.`}
        >
          {ptsDelta > 0 ? '+' : ''}
          {ptsDelta.toFixed(1)} pts vs usual
        </span>
      )}
    </span>
  );
};

// keeps a row honest when the two headline deltas are flat but the score is positive.
export const Drivers = ({ drivers }: { drivers: UpsideDriver[] }): JSX.Element | null => {
  const parts = drivers
    .map((driver) => {
      const delta = toStatNumber(driver.delta);
      if (delta === null || delta <= 0) return null;
      return `${DRIVER_LABELS[driver.stat] ?? driver.stat} +${delta.toFixed(1)}`;
    })
    .filter((part): part is string => part !== null);
  if (parts.length === 0) return null;
  return (
    <span
      className="text-[11px] text-muted tabular-nums whitespace-nowrap"
      title="The categories furthest above his usual, biggest first"
    >
      up vs usual: {parts.join(' · ')}
    </span>
  );
};

export const AvailabilityBadge = ({
  value,
}: {
  value: WatchlistPlayer['prob_active'];
}): JSX.Element => {
  const badge = availabilityBadge(value);
  return (
    <span className={`badge badge-sm tabular-nums ${badge.className}`} title={badge.hint}>
      {badge.percentText ?? badge.label}
    </span>
  );
};

export const PositionChip = ({ position }: { position: string | null }): JSX.Element => {
  if (position === null) {
    return (
      <span
        className="badge badge-ghost badge-sm text-muted shrink-0"
        title="No position on record, so position filters skip him"
      >
        pos ?
      </span>
    );
  }
  return (
    <span
      className="badge badge-outline badge-sm shrink-0 font-semibold"
      title={`Listed at ${position.split('/').join(' and ')}`}
    >
      {position}
    </span>
  );
};

// hidden for a one-day window, where "1 game tonight" is not news.
export const GamesCount = ({
  count,
  phrase,
  days,
}: {
  count: number;
  phrase: string;
  days: number;
}): JSX.Element | null => {
  if (days <= 1) return null;
  return (
    <span
      className="badge badge-outline badge-sm tabular-nums whitespace-nowrap"
      title={`${count} game${count === 1 ? '' : 's'} projected in this window. The score adds them up, so more games ranks higher.`}
    >
      {count} game{count === 1 ? '' : 's'} {phrase}
    </span>
  );
};
