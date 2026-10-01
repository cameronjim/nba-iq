import { useMemo, useState } from 'react';
import { IconChevronUp, IconChevronDown } from '../icons';
import type { PlayerSeasonRow } from '../../types';
import { getTeamLogoUrl } from '../../utils/teamLogos';
import { SEASON_STAT_COLUMNS, type SeasonStatKey } from '../../utils/seasonColumns';
import { compareStats, formatStat, formatText } from '../../utils/stats';

interface SeasonPlayerTableProps {
  rows: PlayerSeasonRow[];
  onSelect?: (row: PlayerSeasonRow) => void;
}

type SortKey = SeasonStatKey | 'player_name' | 'team';

const TEXT_KEYS: SortKey[] = ['player_name', 'team'];

export const SeasonPlayerTable = ({ rows, onSelect }: SeasonPlayerTableProps): JSX.Element => {
  const [sortKey, setSortKey] = useState<SortKey>('points_per_game');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => {
      if (sortKey === 'player_name' || sortKey === 'team') {
        const aVal = a[sortKey] ?? '';
        const bVal = b[sortKey] ?? '';
        return sortDir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      }
      return compareStats(a[sortKey], b[sortKey], sortDir);
    });
  }, [rows, sortKey, sortDir]);

  const handleSort = (key: SortKey): void => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir(TEXT_KEYS.includes(key) ? 'asc' : 'desc');
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
        <table className="table table-sm table-fixed min-w-[1100px] w-full">
          <thead>
            <tr>
              <th
                onClick={() => handleSort('player_name')}
                title="Player Name"
                className={`cursor-pointer select-none whitespace-nowrap w-[220px] ${sortKey === 'player_name' ? 'font-bold' : ''}`}
              >
                <span className="inline-flex items-center gap-1">
                  Player
                  {sortIcon('player_name')}
                </span>
              </th>
              <th
                onClick={() => handleSort('team')}
                title="Team"
                className={`cursor-pointer select-none whitespace-nowrap w-[78px] ${sortKey === 'team' ? 'font-bold' : ''}`}
              >
                <span className="inline-flex items-center gap-1">
                  Team
                  {sortIcon('team')}
                </span>
              </th>
              {SEASON_STAT_COLUMNS.map((col) => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  title={col.full}
                  className={`cursor-pointer select-none whitespace-nowrap text-right ${col.w} ${sortKey === col.key ? 'font-bold' : ''}`}
                >
                  <span className="inline-flex items-center gap-1">
                    {col.label}
                    {sortIcon(col.key)}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => {
              const teamLogo = row.team ? getTeamLogoUrl(row.team) : null;
              return (
                <tr
                  key={`${row.nba_player_id}-${row.season}-${row.team ?? ''}`}
                  className={`hover ${onSelect ? 'cursor-pointer' : ''}`}
                  onClick={onSelect ? () => onSelect(row) : undefined}
                >
                  <td className="whitespace-nowrap">
                    <span className="font-medium truncate block" title={row.player_name}>
                      {row.player_name}
                    </span>
                  </td>
                  <td className="whitespace-nowrap">
                    <span className="flex items-center gap-1.5">
                      {teamLogo && (
                        <img
                          src={teamLogo}
                          alt=""
                          className="w-4 h-4 object-contain"
                          onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                        />
                      )}
                      <span>{formatText(row.team)}</span>
                    </span>
                  </td>
                  {SEASON_STAT_COLUMNS.map((col) => (
                    <td key={col.key} className="whitespace-nowrap text-right tabular-nums">
                      {formatStat(row[col.key], col.decimals)}
                    </td>
                  ))}
                </tr>
              );
            })}
            {sorted.length === 0 && (
              <tr>
                <td colSpan={SEASON_STAT_COLUMNS.length + 2} className="text-center py-12 text-faint">
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
