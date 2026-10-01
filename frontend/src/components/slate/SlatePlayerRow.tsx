import { Flame } from 'lucide-react';
import { formatStat } from '../../utils/stats';
import { AvailabilityBadge, CategoryLine, ImpactBadge } from './SlateBadges';
import { SlateReasons, SlateVsUsual } from './SlateVsUsual';
import { InjuryChip } from './SlateInjuryChip';
import type { SlatePlayer } from '../../types';

export const SlatePlayerRow = ({ player }: { player: SlatePlayer }): JSX.Element => (
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
      <span className="text-xs tabular-nums opacity-70 whitespace-nowrap">
        <span className="font-semibold opacity-100">{formatStat(player.proj_pts)}</span> pts
        <span className="opacity-40"> · </span>
        {formatStat(player.proj_min_p50)} min
      </span>
      <CategoryLine player={player} />
      <InjuryChip player={player} />
    </div>

    {(player.reasons.length > 0 || player.vs_usual !== null) && (
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <SlateReasons player={player} />
        <SlateVsUsual player={player} />
      </div>
    )}
  </li>
);
