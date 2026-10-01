import { formatStat, toStatNumber, STAT_PLACEHOLDER } from '../../utils/stats';
import { reasonEvidenceLines } from '../../utils/vsUsual';
import { ReasonBadge } from './ReasonBadge';
import { WatchlistGameBreakdown } from './WatchlistGameBreakdown';
import { windowOption } from './WatchlistFilters';
import {
  AvailabilityBadge,
  DeltaPair,
  Drivers,
  GamesCount,
  PositionChip,
} from './WatchlistRowBadges';
import type { WatchlistEvidence, WatchlistPlayer } from '../../types';

function evidenceLines(
  player: WatchlistPlayer,
  evidence: WatchlistEvidence,
  days: number
): string[] {
  const lines: string[] = [];
  const multi = days > 1;
  // over a window every per-game number is a mean, so the wording has to say so.
  const per = multi ? ' per game' : '';

  const minutesDelta = toStatNumber(player.minutes.delta);
  if (minutesDelta !== null) {
    lines.push(
      `Minutes: ${formatStat(player.minutes.projected)} projected${per}, usually ${formatStat(player.minutes.usual)} (${minutesDelta > 0 ? '+' : ''}${formatStat(minutesDelta)})`
    );
  }
  if (multi) {
    lines.push(
      `Window: ${player.games_count} game${player.games_count === 1 ? '' : 's'} projected, ${formatStat(player.score, 2)} total at ${formatStat(player.score_per_game, 2)} a game`
    );
  }
  lines.push(...reasonEvidenceLines(evidence, player.points.usual));

  const impact = toStatNumber(player.impact);
  if (impact !== null) {
    const scope = multi
      ? `across the window, averaging the ${formatStat(player.impact_percentile, 0)}th percentile of a night's slate`
      : `tonight, the ${formatStat(player.impact_percentile, 0)}th percentile of the slate`;
    lines.push(`Impact: ${impact > 0 ? '+' : ''}${impact.toFixed(1)} ${scope}`);
  }

  return lines;
}

export const WatchlistRow = ({
  player,
  rank,
  days,
}: {
  player: WatchlistPlayer;
  rank: number;
  days: number;
}): JSX.Element => {
  const lines = evidenceLines(player, player.evidence, days);
  const impact = toStatNumber(player.impact);
  const option = windowOption(days);
  const multi = days > 1;

  return (
    <li className="border border-base-300 rounded-box bg-base-200">
      <details className="group">
        <summary className="flex flex-wrap items-center gap-x-3 gap-y-1.5 p-3 cursor-pointer list-none">
          <span className="text-xs tabular-nums opacity-40 w-6 shrink-0">{rank}</span>

          {/* `basis-full` up to `sm` is what makes the row wrap instead of overflow
              on a phone: with `min-w-0` alone this column shrinks to nothing. */}
          <span className="min-w-0 basis-full sm:basis-0 grow flex flex-col gap-0.5">
            <span className="flex items-baseline gap-1.5 min-w-0">
              <span
                className={
                  'text-sm truncate ' +
                  (player.name_is_placeholder
                    ? 'font-mono text-xs italic opacity-60'
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
              <PositionChip position={player.position} />
              <span className="text-[11px] opacity-50 uppercase tracking-wider shrink-0">
                {player.team_abbr ?? STAT_PLACEHOLDER}
                {/* over a window there is no single opponent to name. */}
                {!multi && player.opponent_team_abbr && (
                  <>
                    <span className="opacity-50 lowercase"> vs </span>
                    {player.opponent_team_abbr}
                  </>
                )}
              </span>
            </span>
            {/* the game count shares the deltas' line rather than the name's, so a
                phone does not truncate the name to make room for it. */}
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <GamesCount count={player.games_count} phrase={option.games} days={days} />
              <DeltaPair player={player} />
            </span>
            <Drivers drivers={player.drivers} />
          </span>

          <span className="flex flex-wrap gap-1">
            {player.reasons.map((reason) => (
              <ReasonBadge key={reason} reason={reason} />
            ))}
          </span>

          {impact !== null && (
            <span
              className="badge badge-ghost badge-sm tabular-nums"
              title={
                multi
                  ? 'Projected impact over the window. 0 is an average night.'
                  : 'Projected impact tonight. 0 is an average night.'
              }
            >
              {impact > 0 ? '+' : ''}
              {impact.toFixed(1)}
            </span>
          )}

          <AvailabilityBadge value={player.prob_active} />

          <span
            className="text-sm font-bold tabular-nums w-10 text-right"
            title={
              multi
                ? `Window total: ${formatStat(player.score_per_game, 2)} a game across ${player.games_count}.`
                : 'How far above his usual tonight projects him, and how much that matters tonight'
            }
          >
            {formatStat(player.score, 2)}
          </span>
        </summary>

        <div className="px-3 pb-3 pt-0 flex flex-col gap-2">
          {lines.length > 0 && (
            <ul
              className="flex flex-col gap-1"
              data-testid={`evidence-${player.nba_player_id}`}
            >
              {lines.map((line) => (
                <li key={line} className="text-xs opacity-70 pl-6">
                  {line}
                </li>
              ))}
            </ul>
          )}
          {multi && (
            <div className="pl-6 overflow-x-auto" data-testid={`games-${player.nba_player_id}`}>
              <WatchlistGameBreakdown games={player.games} />
            </div>
          )}
        </div>
      </details>
    </li>
  );
};
