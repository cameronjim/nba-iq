import { PreseasonBadge } from './SlateBadges';
import { SlatePlayerRow } from './SlatePlayerRow';
import type { SlateGame } from '../../types';

interface SlateGameCardProps {
  game: SlateGame;
  // the run does not reach this date, so there is nothing to project yet.
  scheduleOnly?: boolean;
}

export const SlateGameCard = ({ game, scheduleOnly = false }: SlateGameCardProps): JSX.Element => (
  <section className="card border border-base-300">
    <div className="card-body p-0 gap-0">
      <div className="flex items-baseline justify-between gap-2 px-3 py-2 border-b border-base-300 bg-base-200">
        <h2 className="font-display text-lg font-semibold uppercase tracking-wide">
          {game.away_team_abbr ?? 'TBD'} <span className="text-faint font-normal">@</span>{' '}
          {game.home_team_abbr ?? 'TBD'}
        </h2>
        <span className="flex items-center gap-1.5 shrink-0">
          {game.preseason && <PreseasonBadge />}
          {game.game_status && (
            <span className="text-xs text-muted shrink-0">{game.game_status}</span>
          )}
        </span>
      </div>

      {scheduleOnly ? (
        <p className="text-xs text-muted p-3" data-testid="slate-schedule-only">
          projections not published yet
        </p>
      ) : game.players.length === 0 ? (
        <p className="text-xs text-muted p-3">No projected players for this game yet.</p>
      ) : (
        <ul className="flex flex-col">
          {game.players.map((player) => (
            <SlatePlayerRow key={player.nba_player_id} player={player} />
          ))}
        </ul>
      )}
    </div>
  </section>
);
