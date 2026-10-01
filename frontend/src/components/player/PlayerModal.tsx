import { Link } from 'react-router-dom';
import type { Player } from '../../types';
import { IconClose } from '../icons';
import { PlayerCareerSection } from './PlayerCareerSection';
import { Rating2kBadge } from '../ratings2k/Rating2kBadge';

interface PlayerModalProps {
  player: Player | null;
  onClose: () => void;
}

export const PlayerModal = ({ player, onClose }: PlayerModalProps) => {
  if (!player) return null;

  const injuryAlertClass = (status: string): string => {
    if (status === 'Out') return 'alert alert-error';
    if (['Day-To-Day', 'Day_To_Day', 'Questionable'].includes(status)) return 'alert alert-warning';
    if (status === 'Probable') return 'alert alert-success';
    return 'alert alert-error';
  };

  const n = (v: unknown): number => Number(v) || 0;

  const statGroups = [
    {
      label: 'Scoring',
      stats: [
        { label: 'PPG', value: n(player.points_per_game).toFixed(1) },
        { label: 'FG%', value: n(player.field_goal_percentage).toFixed(1) + '%' },
        { label: '3P%', value: n(player.three_point_percentage).toFixed(1) + '%' },
        { label: 'FT%', value: n(player.free_throw_percentage).toFixed(1) + '%' },
      ],
    },
    {
      label: 'Rebounds & Assists',
      stats: [
        { label: 'RPG', value: n(player.rebounds_per_game).toFixed(1) },
        { label: 'APG', value: n(player.assists_per_game).toFixed(1) },
      ],
    },
    {
      label: 'Defense',
      stats: [
        { label: 'SPG', value: n(player.steals_per_game).toFixed(1) },
        { label: 'BPG', value: n(player.blocks_per_game).toFixed(1) },
      ],
    },
    {
      label: 'Other',
      stats: [
        { label: 'TOV', value: n(player.turnovers_per_game).toFixed(1) },
        { label: 'MIN', value: n(player.minutes_per_game).toFixed(1) },
        { label: 'GP', value: String(player.games_played) },
      ],
    },
  ];

  return (
    <div className="modal modal-open">
      <div className="modal-box max-w-lg">
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-4">
            {player.headshot_url && (
              <div className="avatar">
                <div className="w-16 rounded-box border border-base-300">
                  <img
                    src={player.headshot_url}
                    alt={player.name}
                    onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                  />
                </div>
              </div>
            )}
            <div>
              <h3 className="font-display font-semibold text-3xl uppercase tracking-wide">{player.name}</h3>
              <p className="text-sm text-muted">{player.team} · {player.position}</p>
              <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                {/* renders nothing when the player has no 2K match */}
                <Rating2kBadge playerName={player.name} />
                <Link
                  to={`/player/${player.id}`}
                  onClick={onClose}
                  className="badge badge-sm badge-primary font-semibold"
                  title="Trends, percentiles and projections"
                >
                  Full Analytics
                </Link>
              </div>
            </div>
          </div>
          <button className="btn btn-sm btn-circle btn-ghost" onClick={onClose} aria-label="Close">
            <IconClose size={14} />
          </button>
        </div>

        {player.injury_status && (
          <div className={`${injuryAlertClass(player.injury_status)} mb-4 py-2`}>
            <span className="text-xs font-bold uppercase">{player.injury_status.replace(/_/g, ' ')}</span>
            {player.injury_detail && (
              <span className="text-xs ml-2 text-muted">· {player.injury_detail}</span>
            )}
          </div>
        )}

        {statGroups.map((group) => (
          <div key={group.label} className="mb-4">
            <h4 className="text-sm font-semibold mb-2">{group.label}</h4>
            <div className="stats stats-horizontal w-full border border-base-300">
              {group.stats.map((stat) => (
                <div key={stat.label} className="stat px-4 py-3">
                  <div className="stat-value text-lg">{stat.value}</div>
                  <div className="stat-title text-[10px] uppercase tracking-wider">{stat.label}</div>
                </div>
              ))}
            </div>
          </div>
        ))}

        <PlayerCareerSection nbaPlayerId={player.nba_id} />
      </div>
      <div className="modal-backdrop" onClick={onClose} />
    </div>
  );
};
