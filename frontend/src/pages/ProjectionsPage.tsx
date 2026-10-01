import type { ReactNode } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import { useProjections, type ProjectionScope } from '../hooks/useProjections';
import { formatTimestampWithZone } from '../utils/analytics';
import { formatRange, formatSlateDate } from '../utils/dates';
import { toProjectionRow } from '../utils/projectionRow';
import { ScopeToggle } from '../components/projections/ScopeToggle';
import { ProjectionGame } from '../components/projections/ProjectionGame';
import { ProjectionRow } from '../components/projections/ProjectionRow';
import { ProjectionsSkeleton } from '../components/projections/ProjectionsSkeleton';
import { POSITION_LABELS, WeekFilters } from '../components/projections/WeekFilters';
import type { PredictionRun, SlateResponse, SlateRun, WatchlistResponse } from '../types';

const WEEK_PATH = '/projections?scope=week';

const NO_RUN_NOTICE = 'No prediction run yet. Check back after the next model run.';

export const WatchlistRedirect = (): JSX.Element => <Navigate to={WEEK_PATH} replace />;

function coverageNotice(date: string, run: SlateRun): string {
  const covers =
    run.covers_from && run.covers_to
      ? `The latest run covers ${formatRange(run.covers_from, run.covers_to)}`
      : 'The latest run projected no games';
  return `No projections for ${formatSlateDate(date)} yet. ${covers}; each day's run looks seven days ahead and publishes around 9 AM PT.`;
}

function footerText(run: PredictionRun | SlateRun | null): string | null {
  if (!run) return null;
  const published = formatTimestampWithZone(run.predicted_at);
  const injuries =
    'information_as_of' in run ? formatTimestampWithZone(run.information_as_of) : null;
  const sentences = [
    published && `Published ${published}.`,
    injuries && `Injuries as of ${injuries}.`,
  ].filter((s): s is string => !!s);
  return sentences.length === 0 ? null : sentences.join(' ');
}

const EmptyCard = ({ title, children }: { title: string; children: ReactNode }): JSX.Element => (
  <div className="border border-base-300 py-10 px-4 text-center flex flex-col items-center gap-1">
    <p className="text-sm font-semibold">{title}</p>
    <p className="text-xs text-muted max-w-md">{children}</p>
  </div>
);

const Tonight = ({
  data,
  onWeek,
}: {
  data: SlateResponse;
  onWeek: () => void;
}): JSX.Element => {
  const scheduleOnly = data.run !== null && !data.covered;
  return (
    <div className="flex flex-col gap-4">
      {!data.run && <p className="text-sm border-y border-base-300 py-2">{NO_RUN_NOTICE}</p>}
      {data.run && scheduleOnly && data.games.length > 0 && (
        <p className="text-sm border-y border-base-300 py-2" data-testid="coverage-notice">
          {coverageNotice(data.date, data.run)}
        </p>
      )}
      {data.games.length === 0 ? (
        <EmptyCard title="No games scheduled">
          Nothing on the schedule for {formatSlateDate(data.date)}. Pick another date, or look at{' '}
          <button type="button" className="link link-primary" onClick={onWeek}>
            the next 7 days
          </button>
          .
        </EmptyCard>
      ) : (
        data.games.map((game) => (
          <ProjectionGame key={game.nba_game_id} game={game} scheduleOnly={scheduleOnly} />
        ))
      )}
    </div>
  );
};

const Week = ({
  data,
  players,
  team,
  onClearPosition,
  onClearTeam,
}: {
  data: WatchlistResponse;
  players: WatchlistResponse['players'];
  team: string;
  onClearPosition: () => void;
  onClearTeam: () => void;
}): JSX.Element => (
  <div className="flex flex-col gap-4">
    {!data.run && <p className="text-sm border-y border-base-300 py-2">{NO_RUN_NOTICE}</p>}
    {data.players.length === 0 && data.position !== null ? (
      <EmptyCard title={`No ${POSITION_LABELS[data.position].toLowerCase()} stand out this week`}>
        Try{' '}
        <button type="button" className="link link-primary" onClick={onClearPosition}>
          every position
        </button>
        .
      </EmptyCard>
    ) : data.players.length === 0 ? (
      <EmptyCard title="Nobody is projected above their own usual in this window">
        {data.run
          ? 'That is a normal answer on a quiet stretch.'
          : 'Check back after the next model run.'}
      </EmptyCard>
    ) : players.length === 0 ? (
      <EmptyCard title={`No ${team} players this week`}>
        <button type="button" className="link link-primary" onClick={onClearTeam}>
          Clear the team filter
        </button>{' '}
        to see every team.
      </EmptyCard>
    ) : (
      <ul className="flex flex-col border border-base-300" data-testid="week-list">
        {players.map((player) => (
          <ProjectionRow key={player.nba_player_id} row={toProjectionRow({ kind: 'week', player })} />
        ))}
      </ul>
    )}
  </div>
);

export const ProjectionsPage = (): JSX.Element => {
  const [params, setParams] = useSearchParams();
  const scope: ProjectionScope = params.get('scope') === 'week' ? 'week' : 'tonight';
  const projections = useProjections(scope);
  const { tonight, week, loading, error, reload } = projections;

  const setScope = (next: ProjectionScope): void =>
    setParams(next === 'week' ? { scope: 'week' } : {}, { replace: true });

  const data = scope === 'tonight' ? tonight : week;
  const footer = footerText(data?.run ?? null);

  return (
    <div className="max-w-[900px] mx-auto px-4 py-6 pb-20">
      <header className="flex flex-col gap-3 mb-5">
        <div>
          <h1 className="font-display text-3xl font-semibold uppercase tracking-wide leading-tight">
            Projections
          </h1>
          <p className="text-sm text-muted mt-0.5" data-testid="scope-caption">
            {scope === 'tonight'
              ? `Projected lines for ${formatSlateDate(projections.date)}, by game.`
              : `Players projected to do more than usual${week ? `, ${formatRange(week.window.from, week.window.to)}` : ''}.`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ScopeToggle scope={scope} onChange={setScope} />
          {scope === 'tonight' ? (
            <input
              type="date"
              className="input input-bordered input-sm"
              value={projections.date}
              aria-label="Game date"
              onChange={(e) => projections.setDate(e.target.value)}
            />
          ) : (
            <WeekFilters
              position={projections.position}
              positionOptions={week?.position_options ?? []}
              onPositionChange={projections.setPosition}
              team={projections.team}
              teams={projections.weekTeams}
              onTeamChange={projections.setTeam}
            />
          )}
        </div>
      </header>

      {loading && !data ? (
        <ProjectionsSkeleton />
      ) : error && !data ? (
        <div className="border border-base-300 flex flex-col items-center py-12 gap-4">
          <p className="text-error text-sm">{error}</p>
          <button onClick={() => void reload()} className="btn btn-primary btn-sm">
            Try Again
          </button>
        </div>
      ) : scope === 'tonight' && tonight ? (
        <Tonight data={tonight} onWeek={() => setScope('week')} />
      ) : week ? (
        <Week
          data={week}
          players={projections.weekPlayers}
          team={projections.team}
          onClearPosition={() => projections.setPosition(null)}
          onClearTeam={() => projections.setTeam('')}
        />
      ) : null}

      {footer && (
        <footer className="text-[11px] text-muted mt-6 pt-3 border-t border-base-300" data-testid="projections-footer">
          {footer}
        </footer>
      )}
    </div>
  );
};
