import { statLabel } from '../../utils/analytics';
import { availabilityBadge, formatPredictionDate, opponentLabel, statCellDisplay } from '../../utils/predictions';
import type { UpcomingGamePrediction } from '../../types';

interface UpcomingGameRowProps {
  game: UpcomingGamePrediction;
  columns: string[];
}

export const UpcomingGameRow = ({ game, columns }: UpcomingGameRowProps): JSX.Element => {
  const date = formatPredictionDate(game.game_date);
  const badge = availabilityBadge(game.prob_active);

  return (
    <tr data-testid="upcoming-game-row">
      <td className="whitespace-nowrap">
        <span className="font-medium">{date.label}</span>
        {date.weekday && <span className="ml-1 text-[10px] text-muted">{date.weekday}</span>}
      </td>
      <td className="whitespace-nowrap font-medium">
        {opponentLabel(game.opponent_abbr, game.is_home)}
      </td>
      <td className="whitespace-nowrap">
        <span className="tooltip tooltip-right" data-tip={badge.hint}>
          <span className={`badge badge-sm ${badge.className}`}>{badge.label}</span>
        </span>
        {badge.percentText && (
          <span className="ml-1.5 text-[10px] tabular-nums text-muted">{badge.percentText}</span>
        )}
      </td>
      {columns.map((stat) => {
        const cell = statCellDisplay(statLabel(stat), game.stats[stat]);
        return (
          <td key={stat} className="text-right whitespace-nowrap">
            {/* the band and the schedule-level twin sit under the number rather than
                in the tooltip, so nothing load-bearing is hover-only. */}
            <span className="tooltip tooltip-left" data-tip={cell.hint}>
              <span className="font-semibold tabular-nums">{cell.primary}</span>
              {cell.band && (
                <span className="block text-[10px] tabular-nums text-muted">{cell.band}</span>
              )}
              {cell.unconditional && (
                <span className="block text-[10px] tabular-nums text-faint">
                  {cell.unconditional} sched
                </span>
              )}
            </span>
          </td>
        );
      })}
    </tr>
  );
};
