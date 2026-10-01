import { formatStat, toStatNumber, STAT_PLACEHOLDER } from '../../utils/stats';
import type { SlatePlayer } from '../../types';

function availabilityClass(probability: number): string {
  if (probability >= 0.85) return 'badge-success';
  if (probability >= 0.6) return 'badge-warning';
  return 'badge-error';
}

export const AvailabilityBadge = ({
  value,
}: {
  value: SlatePlayer['prob_active'];
}): JSX.Element => {
  const probability = toStatNumber(value);
  if (probability === null) {
    return (
      <span className="badge badge-ghost badge-sm tabular-nums" title="Availability not modelled">
        {STAT_PLACEHOLDER}
      </span>
    );
  }
  return (
    <span
      className={`badge badge-sm tabular-nums ${availabilityClass(probability)}`}
      title="Modelled chance this player appears"
    >
      {Math.round(probability * 100)}%
    </span>
  );
};

export const ImpactBadge = ({ player }: { player: SlatePlayer }): JSX.Element => {
  const impact = toStatNumber(player.impact);
  if (impact === null) {
    return (
      <span
        className="badge badge-ghost badge-sm tabular-nums"
        title="No impact score for this player"
      >
        {STAT_PLACEHOLDER}
      </span>
    );
  }
  const tone = player.slate_spotlight
    ? 'badge-primary'
    : player.spotlight
      ? 'badge-primary badge-outline'
      : 'badge-ghost';
  return (
    <span
      className={`badge badge-sm tabular-nums font-semibold ${tone}`}
      title="Projected impact tonight. 0 is an average night."
    >
      {impact > 0 ? '+' : ''}
      {impact.toFixed(1)}
    </span>
  );
};

const CATEGORY_LABELS: ReadonlyArray<[keyof SlatePlayer['projected'], string]> = [
  ['reb', 'REB'],
  ['ast', 'AST'],
  ['stl', 'STL'],
  ['blk', 'BLK'],
  ['fg3m', '3PM'],
  ['tov', 'TOV'],
];

export const CategoryLine = ({ player }: { player: SlatePlayer }): JSX.Element | null => {
  const parts = CATEGORY_LABELS.filter(
    ([key]) => toStatNumber(player.projected?.[key]) !== null
  ).map(([key, label]) => `${formatStat(player.projected[key])} ${label}`);
  if (parts.length === 0) return null;
  return (
    <span className="text-[11px] opacity-50 tabular-nums">{parts.join(' · ')}</span>
  );
};
