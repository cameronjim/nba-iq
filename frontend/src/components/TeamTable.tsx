import { useState } from 'react';
import { IconChevronUp, IconChevronDown } from './icons';
import type { Team } from '../types';
import { getTeamLogoUrl } from '../utils/teamLogos';

interface TeamTableProps {
  teams: Team[];
}

type SortKey = keyof Team;

const COLUMNS: { key: SortKey; label: string; full: string; format?: (v: number) => string }[] = [
  { key: 'name',                   label: 'Team',    full: 'Team Name' },
  { key: 'conference',             label: 'Conf',    full: 'Conference' },
  { key: 'wins',                   label: 'W',       full: 'Wins' },
  { key: 'losses',                 label: 'L',       full: 'Losses' },
  { key: 'points_per_game',        label: 'PPG',     full: 'Points Per Game',     format: (v) => Number(v).toFixed(1) },
  { key: 'rebounds_per_game',      label: 'RPG',     full: 'Rebounds Per Game',   format: (v) => Number(v).toFixed(1) },
  { key: 'assists_per_game',       label: 'APG',     full: 'Assists Per Game',    format: (v) => Number(v).toFixed(1) },
  { key: 'steals_per_game',        label: 'SPG',     full: 'Steals Per Game',     format: (v) => Number(v).toFixed(1) },
  { key: 'blocks_per_game',        label: 'BPG',     full: 'Blocks Per Game',     format: (v) => Number(v).toFixed(1) },
  { key: 'field_goal_percentage',  label: 'FG%',     full: 'Field Goal %',        format: (v) => Number(v).toFixed(1) },
  { key: 'three_point_percentage', label: '3P%',     full: '3-Point %',           format: (v) => Number(v).toFixed(1) },
  { key: 'free_throw_percentage',  label: 'FT%',     full: 'Free Throw %',        format: (v) => Number(v).toFixed(1) },
  { key: 'turnovers_per_game',     label: 'TOV',     full: 'Turnovers Per Game',  format: (v) => Number(v).toFixed(1) },
  { key: 'offensive_rating',       label: 'OFF RTG', full: 'Offensive Rating',    format: (v) => Number(v).toFixed(1) },
  { key: 'defensive_rating',       label: 'DEF RTG', full: 'Defensive Rating',    format: (v) => Number(v).toFixed(1) },
  { key: 'net_rating',             label: 'NET RTG', full: 'Net Rating',          format: (v) => Number(v).toFixed(1) },
];

const NUMERIC_KEYS = new Set(['wins','losses','points_per_game','rebounds_per_game','assists_per_game','steals_per_game','blocks_per_game','field_goal_percentage','three_point_percentage','free_throw_percentage','turnovers_per_game','defensive_rating','offensive_rating','net_rating']);

const alignClass = (key: SortKey): string => (NUMERIC_KEYS.has(key) ? 'text-right' : '');

export const TeamTable = ({ teams }: TeamTableProps) => {
  const [sortKey, setSortKey] = useState<SortKey>('wins');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const handleSort = (key: SortKey): void => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir(key === 'name' || key === 'conference' ? 'asc' : 'desc');
    }
  };

  const sorted = [...teams].sort((a, b) => {
    const aVal = a[sortKey];
    const bVal = b[sortKey];
    if (aVal == null) return 1;
    if (bVal == null) return -1;
    if (NUMERIC_KEYS.has(sortKey)) {
      const diff = Number(aVal) - Number(bVal);
      return sortDir === 'asc' ? diff : -diff;
    }
    return sortDir === 'asc'
      ? String(aVal).localeCompare(String(bVal))
      : String(bVal).localeCompare(String(aVal));
  });

  return (
    <div className="overflow-x-auto border border-base-300">
      <table className="table table-sm w-full">
        <thead>
          <tr>
            {COLUMNS.map((col) => (
              <th
                key={col.key}
                onClick={() => handleSort(col.key)}
                title={col.full}
                className={`cursor-pointer select-none whitespace-nowrap ${alignClass(col.key)} ${sortKey === col.key ? 'font-bold' : ''}`}
              >
                <span className="inline-flex items-center gap-1">
                  {col.label}
                  {sortKey === col.key
                    ? (sortDir === 'asc' ? <IconChevronUp size={12} /> : <IconChevronDown size={12} />)
                    : <IconChevronUp size={12} className="invisible" />}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((team) => (
            <tr key={team.id} className="hover">
              {COLUMNS.map((col) => {
                if (col.key === 'conference') {
                  return (
                    <td key={col.key}>
                      {team.conference && (
                        <span className="badge badge-sm badge-outline">
                          {team.conference}
                        </span>
                      )}
                    </td>
                  );
                }
                if (col.key === 'net_rating') {
                  const val = Number(team.net_rating ?? 0);

                  return (
                    <td key={col.key} className={`text-right font-medium ${val > 0 ? 'text-success' : val < 0 ? 'text-error' : ''}`}>
                      {val > 0 ? '+' : ''}{val.toFixed(1)}
                    </td>
                  );
                }
                if (col.key === 'name') {
                  // the abbreviation can be blank in older rows, hence the stored logo_url first.
                  const logo = team.logo_url || getTeamLogoUrl(team.abbreviation);
                  return (
                    <td key={col.key} className="font-medium whitespace-nowrap">
                      <span className="flex items-center gap-2">
                        {logo && (
                          <img
                            src={logo}
                            alt=""
                            className="w-6 h-6 object-contain"
                            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                          />
                        )}
                        <span>{team.name}</span>
                      </span>
                    </td>
                  );
                }
                return (
                  <td key={col.key} className={alignClass(col.key)}>
                    {col.format && team[col.key] != null
                      ? col.format(team[col.key] as number)
                      : String(team[col.key] ?? '-')}
                  </td>
                );
              })}
            </tr>
          ))}
          {sorted.length === 0 && (
            <tr>
              <td colSpan={COLUMNS.length} className="text-center py-12 text-faint">
                No teams found
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
};
