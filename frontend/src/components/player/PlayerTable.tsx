import { useEffect, useMemo, useRef, useState } from 'react';
import { IconChevronUp, IconChevronDown } from '../icons';
import { SkeletonLines } from '../Skeleton';
import type { Player } from '../../types';
import { getTeamLogoUrl } from '../../utils/teamLogos';
import { PLAYER_IMAGE_FALLBACK } from '../../utils/playerImage';
import { InjuryBadge } from './InjuryBadge';

interface PlayerTableProps {
  players: Player[];
  onSelect: (player: Player) => void;
  selectedForCompare?: Player[];
  onToggleCompare?: (player: Player) => void;
}

type SortKey = keyof Player;

// authoritative: the table is table-layout: fixed, so content that does not fit is
// truncated and sorting can never resize a column.
const COLUMNS: { key: SortKey; label: string; full: string; format?: (v: number) => string; w: string }[] = [
  { key: 'name',                   label: 'Player', full: 'Player Name',                w: 'w-[220px]' },
  { key: 'team',                   label: 'Team',   full: 'Team',                        w: 'w-[78px]' },
  { key: 'position',               label: 'Pos',    full: 'Position',                    w: 'w-[60px]' },
  { key: 'fantasy_score',          label: 'FS',     full: 'Fantasy Score (per game)',    w: 'w-[68px]', format: (v) => Number(v).toFixed(1) },
  { key: 'points_per_game',        label: 'PPG',    full: 'Points Per Game',             w: 'w-[64px]', format: (v) => Number(v).toFixed(1) },
  { key: 'rebounds_per_game',      label: 'RPG',    full: 'Rebounds Per Game',           w: 'w-[64px]', format: (v) => Number(v).toFixed(1) },
  { key: 'assists_per_game',       label: 'APG',    full: 'Assists Per Game',            w: 'w-[64px]', format: (v) => Number(v).toFixed(1) },
  { key: 'steals_per_game',        label: 'SPG',    full: 'Steals Per Game',             w: 'w-[64px]', format: (v) => Number(v).toFixed(1) },
  { key: 'blocks_per_game',        label: 'BPG',    full: 'Blocks Per Game',             w: 'w-[64px]', format: (v) => Number(v).toFixed(1) },
  { key: 'field_goal_percentage',  label: 'FG%',    full: 'Field Goal %',                w: 'w-[64px]', format: (v) => Number(v).toFixed(1) },
  { key: 'three_point_percentage', label: '3P%',    full: '3-Point %',                   w: 'w-[64px]', format: (v) => Number(v).toFixed(1) },
  { key: 'free_throw_percentage',  label: 'FT%',    full: 'Free Throw %',                w: 'w-[64px]', format: (v) => Number(v).toFixed(1) },
  { key: 'turnovers_per_game',     label: 'TOV',    full: 'Turnovers Per Game',          w: 'w-[64px]', format: (v) => Number(v).toFixed(1) },
  { key: 'minutes_per_game',       label: 'MIN',    full: 'Minutes Per Game',            w: 'w-[64px]', format: (v) => Number(v).toFixed(1) },
  { key: 'games_played',           label: 'GP',     full: 'Games Played',                w: 'w-[58px]' },
];

const alignClass = (key: SortKey): string => (NUMERIC_KEYS.has(key) ? 'text-right' : '');

const INITIAL_ROWS = 30;
const ROW_INCREMENT = 30;
const INFINITE_THRESHOLD = 600;

const NUMERIC_KEYS = new Set([
  'points_per_game', 'rebounds_per_game', 'assists_per_game', 'steals_per_game',
  'blocks_per_game', 'field_goal_percentage', 'three_point_percentage',
  'free_throw_percentage', 'turnovers_per_game', 'minutes_per_game', 'games_played',
  'fantasy_score',
]);

export const PlayerTable = ({ players, onSelect, selectedForCompare = [], onToggleCompare }: PlayerTableProps) => {
  const [sortKey, setSortKey] = useState<SortKey>('points_per_game');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [visibleCount, setVisibleCount] = useState(INITIAL_ROWS);
  const sentinelRef = useRef<HTMLDivElement | null>(null);

  // reset the window on a filter or sort change so nobody opens 200 rows deep.
  useEffect(() => { setVisibleCount(INITIAL_ROWS); }, [players.length, sortKey, sortDir]);

  const sorted = useMemo(() => {
    return [...players].sort((a, b) => {
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
  }, [players, sortKey, sortDir]);

  const visible = sorted.slice(0, visibleCount);
  const hasMore = visibleCount < sorted.length;

  useEffect(() => {
    if (!hasMore) return;
    const node = sentinelRef.current;
    if (!node) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisibleCount((c) => Math.min(c + ROW_INCREMENT, sorted.length));
        }
      },
      { rootMargin: `0px 0px ${INFINITE_THRESHOLD}px 0px` }
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, [hasMore, sorted.length]);

  const handleSort = (key: SortKey): void => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir(key === 'name' || key === 'team' || key === 'position' ? 'asc' : 'desc');
    }
  };

  const compareIds = new Set(selectedForCompare.map((p) => p.id));
  const compareMaxed = selectedForCompare.length >= 3;

  return (
    <div>
      <div className="overflow-x-auto border border-base-300">
        <table className="table table-sm table-fixed min-w-[1200px] w-full">
          <thead>
            <tr>
              {onToggleCompare && <th className="w-10" />}
              {COLUMNS.map((col) => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  title={col.full}
                  className={`cursor-pointer select-none whitespace-nowrap ${col.w} ${alignClass(col.key)} ${sortKey === col.key ? 'font-bold' : ''}`}
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
            {visible.map((player) => {
              const isSelected = compareIds.has(player.id);
              const isDisabled = onToggleCompare && compareMaxed && !isSelected;
              const teamLogo = getTeamLogoUrl(player.team);
              return (
                <tr
                  key={player.id}
                  onClick={() => onSelect(player)}
                  className={`cursor-pointer hover ${isSelected ? 'bg-base-200' : ''}`}
                >
                  {onToggleCompare && (
                    <td onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={isSelected}
                        disabled={!!isDisabled}
                        onChange={() => onToggleCompare(player)}
                        className="checkbox checkbox-primary checkbox-xs"
                      />
                    </td>
                  )}
                  {COLUMNS.map((col) => (
                    <td
                      key={col.key}
                      className={`whitespace-nowrap ${alignClass(col.key)} ${col.format ? 'tabular-nums' : ''}`}
                    >
                      {col.key === 'name' ? (
                        <span className="flex items-center gap-2 overflow-hidden">
                          <div className="avatar flex-shrink-0">
                            <div className="w-7 rounded-box">
                              <img
                                src={player.headshot_url || PLAYER_IMAGE_FALLBACK}
                                alt=""
                                onError={(e) => { (e.target as HTMLImageElement).src = PLAYER_IMAGE_FALLBACK; }}
                              />
                            </div>
                          </div>
                          <span className="font-medium truncate" title={player.name}>{player.name}</span>
                          <InjuryBadge status={player.injury_status} detail={player.injury_detail} />
                        </span>
                      ) : col.key === 'team' ? (
                        <span className="flex items-center gap-1.5">
                          {teamLogo && (
                            <img
                              src={teamLogo}
                              alt=""
                              className="w-4 h-4 object-contain"
                              onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                            />
                          )}
                          <span>{player.team}</span>
                        </span>
                      ) : col.format && player[col.key] != null ? (
                        col.format(player[col.key] as number)
                      ) : (
                        String(player[col.key] ?? '-')
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
            {visible.length === 0 && (
              <tr>
                <td colSpan={COLUMNS.length + (onToggleCompare ? 1 : 0)} className="text-center py-12 text-faint">
                  No players found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {hasMore && (
        <div ref={sentinelRef} className="py-4">
          <SkeletonLines lines={1} label="Loading more players" />
        </div>
      )}

      {!hasMore && sorted.length > INITIAL_ROWS && (
        <div className="text-center text-xs text-faint py-3">
          {sorted.length} player{sorted.length !== 1 ? 's' : ''} total
        </div>
      )}
    </div>
  );
};
