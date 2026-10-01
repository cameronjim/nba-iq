import { IconChevronRight } from '../icons';
import { formatStat, STAT_PLACEHOLDER } from '../../utils/stats';
import { shortDay } from '../../utils/dates';
import type { ProjectionRowModel } from '../../utils/projectionRow';

function hasDetails(row: ProjectionRowModel): boolean {
  return (
    row.categories !== null ||
    row.sitsSentence !== null ||
    row.evidence.length > 0 ||
    row.games.length > 0
  );
}

export const ProjectionDetails = ({ row }: { row: ProjectionRowModel }): JSX.Element | null => {
  if (!hasDetails(row)) return null;
  return (
    <details className="group text-xs" data-testid={`details-${row.id}`}>
      <summary className="list-none cursor-pointer inline-flex items-center gap-0.5 text-muted">
        <IconChevronRight size={11} className="shrink-0 group-open:rotate-90" />
        Details
      </summary>
      <div className="flex flex-col gap-1.5 mt-1.5 pl-4 text-muted">
        {row.categories && (
          <p className="tabular-nums">
            <span className="text-base-content">If he plays:</span> {row.categories}
          </p>
        )}
        {row.sitsSentence && <p className="tabular-nums">{row.sitsSentence}</p>}
        {row.games.length > 0 && (
          <div className="overflow-x-auto">
            <table className="table table-xs w-auto" data-testid={`games-${row.id}`}>
              <thead>
                <tr className="text-[10px] uppercase text-muted">
                  <th className="font-normal">Date</th>
                  <th className="font-normal">Opp</th>
                  <th className="font-normal text-right">Min</th>
                  <th className="font-normal text-right">Pts</th>
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {row.games.map((game) => (
                  <tr key={`${game.game_date}-${game.nba_game_id}`}>
                    <td className="whitespace-nowrap">{shortDay(game.game_date)}</td>
                    <td className="uppercase">{game.opponent_team_abbr ?? STAT_PLACEHOLDER}</td>
                    <td className="text-right">{formatStat(game.minutes_p50, 0)}</td>
                    <td className="text-right">{formatStat(game.proj_pts)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {row.evidence.length > 0 && (
          <ul className="flex flex-col gap-0.5 tabular-nums">
            {row.evidence.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        )}
      </div>
    </details>
  );
};
