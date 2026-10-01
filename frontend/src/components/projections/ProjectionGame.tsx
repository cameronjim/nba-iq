import { ProjectionRow } from './ProjectionRow';
import { toProjectionRow } from '../../utils/projectionRow';
import type { SlateGame } from '../../types';

interface ProjectionGameProps {
  game: SlateGame;
  // the run does not reach this date, so there is nothing to project yet.
  scheduleOnly: boolean;
}

export const ProjectionGame = ({ game, scheduleOnly }: ProjectionGameProps): JSX.Element => (
  <section className="border border-base-300" aria-label={`${game.away_team_abbr ?? 'TBD'} at ${game.home_team_abbr ?? 'TBD'}`}>
    <div className="flex items-baseline justify-between gap-2 px-3 py-2 border-b border-base-300 bg-base-200">
      <h2 className="font-display text-lg font-semibold uppercase tracking-wide">
        {game.away_team_abbr ?? 'TBD'} <span className="text-faint font-normal">@</span>{' '}
        {game.home_team_abbr ?? 'TBD'}
      </h2>
      <span className="flex items-baseline gap-2 text-xs text-muted shrink-0">
        {game.preseason && <span>Preseason</span>}
        {game.game_status && <span>{game.game_status}</span>}
      </span>
    </div>

    {scheduleOnly ? (
      <p className="text-xs text-muted p-3" data-testid="schedule-only">
        Projections not published yet.
      </p>
    ) : game.players.length === 0 ? (
      <p className="text-xs text-muted p-3">No projected players for this game yet.</p>
    ) : (
      <ul className="flex flex-col">
        {game.players.map((player) => (
          <ProjectionRow key={player.nba_player_id} row={toProjectionRow({ kind: 'tonight', player, game })} />
        ))}
      </ul>
    )}
  </section>
);
