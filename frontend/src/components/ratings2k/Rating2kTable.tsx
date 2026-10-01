import { useMemo, useState } from 'react';
import { IconChevronUp, IconChevronDown } from '../icons';
import type { Rating2kSummary } from '../../types';
import { getTeamLogoUrl } from '../../utils/teamLogos';
import { PLAYER_IMAGE_FALLBACK } from '../../utils/playerImage';
import { compareStats, formatStat, formatText } from '../../utils/stats';
import { formatPositions, tierBadgeClass } from '../../utils/ratings2k';

interface Rating2kTableProps {
  rows: Rating2kSummary[];
  onSelect: (row: Rating2kSummary) => void;
}

type SortKey = 'name' | 'team' | 'overall';

export const Rating2kTable = ({ rows, onSelect }: Rating2kTableProps): JSX.Element => {
  const [sortKey, setSortKey] = useState<SortKey>('overall');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => {
      if (sortKey === 'overall') return compareStats(a.overall, b.overall, sortDir);
      const aVal = (sortKey === 'name' ? a.name : a.team) ?? '';
      const bVal = (sortKey === 'name' ? b.name : b.team) ?? '';
      return sortDir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
    });
  }, [rows, sortKey, sortDir]);

  const handleSort = (key: SortKey): void => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir(key === 'overall' ? 'desc' : 'asc');
    }
  };

  const sortIcon = (key: SortKey): JSX.Element => {
    if (sortKey !== key) {
      // always rendered, just hidden: an absent icon would change the column width.
      return <IconChevronUp size={12} className="invisible" />;
    }
    return sortDir === 'asc' ? <IconChevronUp size={12} /> : <IconChevronDown size={12} />;
  };

  return (
    <div>
      <div className="overflow-x-auto border border-base-300">
        {/* table-fixed: re-sorting can never change column widths. */}
        <table className="table table-sm table-fixed min-w-[640px] w-full">
          <thead>
            <tr>
              <th
                onClick={() => handleSort('name')}
                title="Player Name"
                className={`cursor-pointer select-none whitespace-nowrap w-[240px] ${sortKey === 'name' ? 'font-bold' : ''}`}
              >
                <span className="inline-flex items-center gap-1">
                  Player
                  {sortIcon('name')}
                </span>
              </th>
              <th
                onClick={() => handleSort('team')}
                title="Team"
                className={`cursor-pointer select-none whitespace-nowrap w-[150px] ${sortKey === 'team' ? 'font-bold' : ''}`}
              >
                <span className="inline-flex items-center gap-1">
                  Team
                  {sortIcon('team')}
                </span>
              </th>
              <th title="Positions" className="whitespace-nowrap w-[110px]">Pos</th>
              <th
                onClick={() => handleSort('overall')}
                title="Overall Rating"
                className={`cursor-pointer select-none whitespace-nowrap text-right w-[90px] ${sortKey === 'overall' ? 'font-bold' : ''}`}
              >
                <span className="inline-flex items-center gap-1">
                  OVR
                  {sortIcon('overall')}
                </span>
              </th>
              <th title="Game Version" className="whitespace-nowrap w-[90px]">Game</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => {
              const teamLogo = row.team ? getTeamLogoUrl(row.team) : null;
              return (
                <tr
                  key={row.slug}
                  onClick={() => onSelect(row)}
                  className="cursor-pointer hover"
                >
                  <td className="whitespace-nowrap">
                    <span className="flex items-center gap-2 overflow-hidden">
                      <span className="avatar flex-shrink-0">
                        <span className="w-7 rounded-box bg-base-200 block overflow-hidden">
                          <img
                            src={row.player_image || PLAYER_IMAGE_FALLBACK}
                            alt=""
                            onError={(e) => {
                              (e.target as HTMLImageElement).src = PLAYER_IMAGE_FALLBACK;
                            }}
                          />
                        </span>
                      </span>
                      <span className="font-medium truncate" title={row.name}>{row.name}</span>
                    </span>
                  </td>
                  <td className="whitespace-nowrap">
                    <span className="flex items-center gap-1.5 overflow-hidden">
                      {teamLogo && (
                        <img
                          src={teamLogo}
                          alt=""
                          className="w-4 h-4 object-contain flex-shrink-0"
                          onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                        />
                      )}
                      <span className="truncate" title={row.team ?? ''}>{formatText(row.team)}</span>
                    </span>
                  </td>
                  <td className="whitespace-nowrap">{formatText(formatPositions(row.positions))}</td>
                  <td className="whitespace-nowrap text-right">
                    <span className={`badge badge-sm font-semibold tabular-nums ${tierBadgeClass(row.overall)}`}>
                      {formatStat(row.overall, 0)}
                    </span>
                  </td>
                  <td className="whitespace-nowrap text-muted">{formatText(row.game_version)}</td>
                </tr>
              );
            })}
            {sorted.length === 0 && (
              <tr>
                <td colSpan={5} className="text-center py-12 text-faint">
                  No players found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {sorted.length > 0 && (
        <div className="text-center text-xs text-faint py-3">
          {sorted.length} player{sorted.length !== 1 ? 's' : ''}
        </div>
      )}
    </div>
  );
};
