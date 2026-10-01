import { formatStat, toStatNumber } from '../../utils/stats';
import { AvailabilityBadge, CategoryLine, ImpactBadge } from './SlateBadges';
import { SlateReasons, SlateVsUsual } from './SlateVsUsual';
import { InjuryChip } from './SlateInjuryChip';
import type { SlatePlayer } from '../../types';

function scheduleLine(player: SlatePlayer): string | null {
  const probability = toStatNumber(player.prob_active);
  if (probability === null || toStatNumber(player.proj_pts) === null) return null;
  return `${Math.round(probability * 100)}% to play, ${formatStat(player.proj_pts)} over the schedule`;
}

export const SlatePlayerRow = ({ player }: { player: SlatePlayer }): JSX.Element => {
  const schedule = scheduleLine(player);

  return (
    <li
      className={
        'flex flex-col gap-0.5 py-2 px-3 border-b border-base-300 last:border-b-0 ' +
        (player.slate_spotlight ? 'bg-base-200' : '')
      }
    >
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex items-baseline gap-1.5">
          {player.slate_spotlight && (
            <span
              className="text-[11px] font-semibold uppercase text-primary shrink-0"
              aria-label="Top projected impact on the slate"
            >
              Top
            </span>
          )}
          <span
            className={
              'text-sm truncate ' +
              (player.name_is_placeholder
                ? 'font-mono text-xs italic text-muted'
                : 'font-semibold')
            }
            title={
              player.name_is_placeholder
                ? 'Not on a roster yet, so this is his NBA id'
                : undefined
            }
          >
            {player.name}
          </span>
          {player.team_abbr && (
            <span className="text-[11px] text-muted uppercase tracking-wider shrink-0">
              {player.team_abbr}
            </span>
          )}
        </span>

        <span className="ml-auto flex items-baseline gap-3 shrink-0">
          <ImpactBadge player={player} />
          <AvailabilityBadge value={player.prob_active} />
        </span>
      </div>

      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span
          className="text-xs tabular-nums text-muted whitespace-nowrap"
          data-testid={`slate-headline-${player.nba_player_id}`}
        >
          <span className="font-semibold text-base-content">{formatStat(player.proj_pts_cond)}</span>{' '}
          pts if he plays
          <span className="text-faint"> · </span>
          {formatStat(player.proj_min_p50)} min
        </span>
        <CategoryLine player={player} />
        <InjuryChip player={player} />
      </div>

      {schedule && (
        <span
          className="text-[11px] tabular-nums text-faint"
          data-testid={`slate-schedule-${player.nba_player_id}`}
        >
          {schedule}
        </span>
      )}

      {(player.reasons.length > 0 || player.vs_usual !== null) && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <SlateReasons player={player} />
          <SlateVsUsual player={player} />
        </div>
      )}
    </li>
  );
};
