import { Flame } from 'lucide-react';
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
        'flex flex-col gap-0.5 py-1.5 px-2 -mx-2 rounded-md ' +
        (player.slate_spotlight
          ? 'bg-primary/10 ring-1 ring-primary/30'
          : player.spotlight
            ? 'bg-base-300/50'
            : '')
      }
    >
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex items-center gap-1.5">
          {player.slate_spotlight && (
            <Flame
              size={13}
              className="text-primary shrink-0"
              aria-label="Top projected impact on the slate"
            />
          )}
          <span
            className={
              'text-sm truncate ' +
              (player.name_is_placeholder ? 'font-mono text-xs italic opacity-60' : 'font-medium')
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
            <span className="text-[11px] opacity-50 uppercase tracking-wider shrink-0">
              {player.team_abbr}
            </span>
          )}
        </span>

        <span className="ml-auto flex items-center gap-1.5 shrink-0">
          <ImpactBadge player={player} />
          <AvailabilityBadge value={player.prob_active} />
        </span>
      </div>

      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span
          className="text-xs tabular-nums opacity-70 whitespace-nowrap"
          data-testid={`slate-headline-${player.nba_player_id}`}
        >
          <span className="font-semibold opacity-100">{formatStat(player.proj_pts_cond)}</span>{' '}
          pts if he plays
          <span className="opacity-40"> · </span>
          {formatStat(player.proj_min_p50)} min
        </span>
        <CategoryLine player={player} />
        <InjuryChip player={player} />
      </div>

      {schedule && (
        <span
          className="text-[11px] tabular-nums opacity-50"
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
