import { SkeletonTable } from '../Skeleton';
import { SEASON_STAT_COLUMNS } from '../../utils/seasonColumns';
import { formatStat, formatText } from '../../utils/stats';
import { usePlayerCareer } from '../../hooks/usePlayerCareer';

const HEADING = 'Career by Season';

interface PlayerCareerSectionProps {
  nbaPlayerId: string | null | undefined;
  // only for surfaces opened on purpose to see history; the player modal prefers
  // to drop the section silently.
  emptyMessage?: string;
  framed?: boolean;
}

export const PlayerCareerSection = ({
  nbaPlayerId,
  emptyMessage,
  framed = false,
}: PlayerCareerSectionProps): JSX.Element | null => {
  const { seasons, loading, unavailable } = usePlayerCareer(nbaPlayerId);

  // one wrapper for every branch, so a framed host never gets a bare placeholder.
  const frame = (heading: boolean, body: JSX.Element): JSX.Element => {
    if (framed) {
      return (
        <section className="border-t border-base-300 pt-4 flex flex-col gap-3">
          {heading && <h2 className="text-lg font-semibold">{HEADING}</h2>}
          {body}
        </section>
      );
    }
    return (
      <div className="mb-4">
        {heading && (
          <h3 className="text-sm font-semibold mb-2">{HEADING}</h3>
        )}
        {body}
      </div>
    );
  };

  if (loading) {
    return frame(false, <SkeletonTable rows={4} cols={8} label="Loading career history" />);
  }

  if (unavailable) {
    if (!emptyMessage) return null;
    return frame(false, <p className="text-sm text-muted py-4">{emptyMessage}</p>);
  }

  return frame(
    true,
    /* both axes scroll inside the box so a wide or long career never resizes the host. */
    <div className="overflow-x-auto max-h-64 overflow-y-auto border border-base-300">
      <table className="table table-xs table-fixed table-pin-rows min-w-[900px] w-full">
        <thead>
          <tr>
            <th title="Season" className="whitespace-nowrap w-[86px]">Season</th>
            <th title="Team" className="whitespace-nowrap w-[60px]">Team</th>
            {SEASON_STAT_COLUMNS.map((col) => (
              <th key={col.key} title={col.full} className={`whitespace-nowrap text-right ${col.w}`}>
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {seasons.map((row) => (
            <tr key={`${row.season}-${row.team ?? ''}`}>
              <td className="whitespace-nowrap font-medium">{row.season}</td>
              <td className="whitespace-nowrap">{formatText(row.team)}</td>
              {SEASON_STAT_COLUMNS.map((col) => (
                <td key={col.key} className="whitespace-nowrap text-right tabular-nums">
                  {formatStat(row[col.key], col.decimals)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
