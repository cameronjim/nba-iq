import type { AnalyticsGameLog } from '../../types';
import { formatStat, formatText } from '../../utils/stats';
import { formatGameDate } from '../../utils/analytics';

interface RecentGamesTableProps {
  games: AnalyticsGameLog[];
}

const BOX_COLUMNS = [
  { key: 'minutes', label: 'MIN' },
  { key: 'pts', label: 'PTS' },
  { key: 'reb', label: 'REB' },
  { key: 'ast', label: 'AST' },
  { key: 'stl', label: 'STL' },
  { key: 'blk', label: 'BLK' },
  { key: 'tov', label: 'TOV' },
] as const;

// no result column: the payload carries the player's line, not the team's, so a W/L
// would be a guess.
export const RecentGamesTable = ({ games }: RecentGamesTableProps): JSX.Element | null => {
  if (games.length === 0) return null;

  const newestFirst = [...games].reverse();

  return (
    <section className="border-t border-base-300 pt-4">
      <div className="flex flex-col gap-3">
        <div>
          <h2 className="text-lg font-semibold">Recent Games</h2>
          <p className="text-xs text-muted mt-0.5">Newest first.</p>
        </div>

        <div className="overflow-x-auto border border-base-300">
          <table className="table table-xs w-full min-w-[560px]">
            <thead>
              <tr>
                <th className="whitespace-nowrap">Date</th>
                <th className="whitespace-nowrap">Opp</th>
                {BOX_COLUMNS.map((col) => (
                  <th key={col.key} className="text-right whitespace-nowrap">{col.label}</th>
                ))}
                <th className="text-right whitespace-nowrap">FG</th>
                <th className="text-right whitespace-nowrap">3P</th>
                <th className="text-right whitespace-nowrap">FT</th>
              </tr>
            </thead>
            <tbody>
              {newestFirst.map((game) => (
                <tr key={game.game_date}>
                  <td className="whitespace-nowrap font-medium">{formatGameDate(game.game_date)}</td>
                  <td className="whitespace-nowrap">
                    <span className="text-faint mr-1">{game.is_home ? 'vs' : '@'}</span>
                    {formatText(game.opponent_team_abbr)}
                  </td>
                  {BOX_COLUMNS.map((col) => (
                    <td key={col.key} className="text-right tabular-nums whitespace-nowrap">
                      {formatStat(game[col.key])}
                    </td>
                  ))}
                  <td className="text-right tabular-nums whitespace-nowrap">
                    {formatStat(game.fgm, 0)}-{formatStat(game.fga, 0)}
                  </td>
                  <td className="text-right tabular-nums whitespace-nowrap">
                    {formatStat(game.fg3m, 0)}-{formatStat(game.fg3a, 0)}
                  </td>
                  <td className="text-right tabular-nums whitespace-nowrap">
                    {formatStat(game.ftm, 0)}-{formatStat(game.fta, 0)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
};
