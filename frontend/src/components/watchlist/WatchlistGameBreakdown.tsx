import { formatStat, toStatNumber, STAT_PLACEHOLDER } from '../../utils/stats';
import { shortDay } from '../../utils/dates';
import type { WatchlistGame } from '../../types';

export const WatchlistGameBreakdown = ({
  games,
}: {
  games: WatchlistGame[];
}): JSX.Element | null => {
  if (games.length === 0) return null;
  return (
    <table className="table table-xs w-auto">
      <thead>
        <tr className="text-[10px] uppercase tracking-wider text-muted">
          <th className="font-normal">Date</th>
          <th className="font-normal">Opp</th>
          <th className="font-normal text-right">Min</th>
          <th className="font-normal text-right">Pts</th>
          <th className="font-normal text-right">Impact</th>
          <th className="font-normal text-right">Adds</th>
        </tr>
      </thead>
      <tbody className="tabular-nums">
        {games.map((game) => (
          <tr key={`${game.game_date}-${game.nba_game_id}`}>
            <td className="whitespace-nowrap">{shortDay(game.game_date)}</td>
            <td className="uppercase text-muted">{game.opponent_team_abbr ?? STAT_PLACEHOLDER}</td>
            <td className="text-right">{formatStat(game.minutes_p50, 0)}</td>
            <td className="text-right">{formatStat(game.proj_pts)}</td>
            <td className="text-right text-muted">{formatStat(game.impact)}</td>
            <td
              className={
                'text-right ' + ((toStatNumber(game.score) ?? 0) > 0 ? 'font-semibold' : 'text-faint')
              }
            >
              {formatStat(game.score, 2)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
};
