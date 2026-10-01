import { PreseasonBadge } from './SlateBadges';
import { SlatePlayerRow } from './SlatePlayerRow';
import type { SlateGame } from '../../types';

export const SlateGameCard = ({ game }: { game: SlateGame }): JSX.Element => (
  <section className="card bg-base-200 border border-base-300">
    <div className="card-body p-4 sm:p-5 gap-2">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="font-bold text-base tracking-tight">
          {game.away_team_abbr ?? 'TBD'} <span className="opacity-40 font-normal">@</span>{' '}
          {game.home_team_abbr ?? 'TBD'}
        </h2>
        <span className="flex items-center gap-1.5 shrink-0">
          {game.preseason && <PreseasonBadge />}
          {game.game_status && (
            <span className="badge badge-ghost badge-sm shrink-0">{game.game_status}</span>
          )}
        </span>
      </div>

      {game.players.length === 0 ? (
        <p className="text-xs opacity-50 py-2">No projected players for this game yet.</p>
      ) : (
        <ul className="flex flex-col gap-0.5">
          {game.players.map((player) => (
            <SlatePlayerRow key={player.nba_player_id} player={player} />
          ))}
        </ul>
      )}
    </div>
  </section>
);
