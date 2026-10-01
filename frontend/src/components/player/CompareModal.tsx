import type { Player } from '../../types';
import { IconClose } from '../icons';

interface CompareModalProps {
  players: Player[];
  onClose: () => void;
}

const STATS: { key: keyof Player; label: string; format: (v: number) => string; higherIsBetter: boolean }[] = [
  { key: 'points_per_game', label: 'Points (PPG)', format: (v) => Number(v).toFixed(1), higherIsBetter: true },
  { key: 'rebounds_per_game', label: 'Rebounds (RPG)', format: (v) => Number(v).toFixed(1), higherIsBetter: true },
  { key: 'assists_per_game', label: 'Assists (APG)', format: (v) => Number(v).toFixed(1), higherIsBetter: true },
  { key: 'steals_per_game', label: 'Steals (SPG)', format: (v) => Number(v).toFixed(1), higherIsBetter: true },
  { key: 'blocks_per_game', label: 'Blocks (BPG)', format: (v) => Number(v).toFixed(1), higherIsBetter: true },
  { key: 'field_goal_percentage', label: 'FG%', format: (v) => Number(v).toFixed(1) + '%', higherIsBetter: true },
  { key: 'free_throw_percentage', label: 'FT%', format: (v) => Number(v).toFixed(1) + '%', higherIsBetter: true },
  { key: 'three_pointers_made', label: '3-Pointers Made', format: (v) => Number(v).toFixed(1), higherIsBetter: true },
  { key: 'turnovers_per_game', label: 'Turnovers (TOV)', format: (v) => Number(v).toFixed(1), higherIsBetter: false },
];

const FANTASY_WEIGHTS: Partial<Record<keyof Player, number>> = {
  points_per_game: 1, rebounds_per_game: 1, assists_per_game: 1, steals_per_game: 2, blocks_per_game: 2,
  field_goal_percentage: 0.5, free_throw_percentage: 0.5, three_pointers_made: 1.5, turnovers_per_game: -1,
};

const FALLBACK_SVG =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'%3E%3Ccircle cx='20' cy='20' r='20' fill='%23252836'/%3E%3Ccircle cx='20' cy='15' r='7' fill='%234b5563'/%3E%3Cellipse cx='20' cy='35' rx='12' ry='8' fill='%234b5563'/%3E%3C/svg%3E";

const GRID_COLS_CLASS: Record<number, string> = {
  2: '[grid-template-columns:180px_repeat(2,1fr)]',
  3: '[grid-template-columns:180px_repeat(3,1fr)]',
};

function fantasyScore(player: Player): number {
  return Object.entries(FANTASY_WEIGHTS).reduce((sum, [key, weight]) => {
    return sum + (Number(player[key as keyof Player]) || 0) * (weight ?? 0);
  }, 0);
}

export const CompareModal = ({ players, onClose }: CompareModalProps) => {
  if (players.length < 2) return null;

  const scores = players.map(fantasyScore);
  const gridClass = GRID_COLS_CLASS[players.length] ?? GRID_COLS_CLASS[3];

  return (
    <div className="modal modal-open">
      <div className="modal-box max-w-3xl max-h-[90vh] flex flex-col p-0">
        <div className="flex items-center justify-between px-5 py-4 border-b border-base-300 flex-shrink-0">
          <h3 className="font-display font-semibold text-xl uppercase tracking-wide">Player Comparison</h3>
          <button className="btn btn-sm btn-circle btn-ghost" onClick={onClose} aria-label="Close">
            <IconClose size={14} />
          </button>
        </div>

        <div className="overflow-y-auto">
          <div className={`grid border-b border-base-300 ${gridClass}`}>
            <div className="px-4 py-3" />
            {players.map((p) => (
              <div key={p.id} className="px-4 py-3 flex flex-col items-center gap-2 border-l border-base-300">
                <div className="avatar">
                  <div className="w-12 rounded-box border border-base-300">
                    <img
                      src={p.headshot_url || FALLBACK_SVG}
                      alt={p.name}
                      onError={(e) => { (e.target as HTMLImageElement).src = FALLBACK_SVG; }}
                    />
                  </div>
                </div>
                <div className="text-center">
                  <div className="text-sm font-semibold leading-tight">{p.name}</div>
                  <div className="text-xs text-muted mt-0.5">{p.position} · {p.team}</div>
                </div>
              </div>
            ))}
          </div>

          {STATS.map((stat) => {
            const values = players.map((p) => Number(p[stat.key]) || 0);
            const best = stat.higherIsBetter ? Math.max(...values) : Math.min(...values);
            return (
              <div
                key={stat.key}
                className={`grid border-b border-base-300 ${gridClass}`}
              >
                <div className="px-4 py-3 flex items-center">
                  <span className="text-xs font-medium text-muted">{stat.label}</span>
                </div>
                {players.map((p, pIdx) => {
                  const val = values[pIdx];
                  const isBest = val === best;
                  return (
                    <div
                      key={p.id}
                      className={`px-4 py-3 flex items-center justify-center border-l border-base-300 ${isBest ? 'font-bold' : ''}`}
                    >
                      <span className="text-sm tabular-nums">{stat.format(val)}</span>
                      {isBest && players.length > 1 && <span className="sr-only"> best</span>}
                    </div>
                  );
                })}
              </div>
            );
          })}

          <div className={`grid bg-base-200 ${gridClass}`}>
            <div className="px-4 py-3.5 flex items-center">
              <span className="text-xs font-bold text-primary uppercase tracking-wider">Fantasy Score</span>
            </div>
            {players.map((p, pIdx) => {
              const score = scores[pIdx];
              const isBest = score === Math.max(...scores);
              return (
                <div
                  key={p.id}
                  className={`px-4 py-3.5 flex items-center justify-center border-l border-base-300 ${isBest ? 'text-primary font-bold' : ''}`}
                >
                  <span className="text-sm tabular-nums">{score.toFixed(1)}</span>
                  {isBest && <span className="sr-only"> best</span>}
                </div>
              );
            })}
          </div>
        </div>
      </div>
      <div className="modal-backdrop" onClick={onClose} />
    </div>
  );
};
