import { useState } from 'react';
import { IconRefresh } from '../icons';
import { SkeletonTable } from '../Skeleton';
import type { RosterResourceState } from '../../hooks/useRosterResource';
import type { StartSitDay, StartSitResponse, StartSitStatus } from '../../types';

const EMPTY_MESSAGES: Record<Exclude<StartSitStatus, 'ok'>, string> = {
  empty_roster: 'Add players to your roster to see a daily lineup.',
  no_run: 'No model run is available yet, so there is no lineup to set.',
  no_games: 'None of your players have projected games in the next week.',
};

interface LineupCardProps {
  state: RosterResourceState<StartSitResponse>;
  onReload: () => void;
}

function dayLabel(date: string): string {
  const [y, m, d] = date.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  });
}

function DayPanel({ day }: { day: StartSitDay }): JSX.Element {
  return (
    <div role="tabpanel" aria-label={dayLabel(day.date)} className="space-y-2">
      <p className="text-sm font-medium" data-testid="lineup-recommendation">{day.recommendation}</p>
      {day.players.length > 0 && (
        <ol className="divide-y divide-base-300 border-y border-base-300">
          {day.players.map((player) => (
            <li key={player.player_id} className="flex items-baseline gap-3 py-1.5 text-sm">
              <span
                className={`w-8 shrink-0 text-xs font-semibold uppercase ${player.start ? 'text-success' : 'text-muted'}`}
              >
                {player.start ? 'Start' : 'Sit'}
              </span>
              <span className={player.start ? '' : 'text-muted'}>{player.sentence}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function Lineup({ data }: { data: StartSitResponse }): JSX.Element {
  const firstWithGames = data.days.findIndex((day) => day.players.length > 0);
  const [selected, setSelected] = useState(Math.max(0, firstWithGames));
  const day = data.days[Math.min(selected, data.days.length - 1)];

  return (
    <div className="space-y-3">
      <div role="tablist" aria-label="Lineup day" className="flex flex-wrap gap-1">
        {data.days.map((d, i) => (
          <button
            key={d.date}
            role="tab"
            aria-selected={i === selected}
            onClick={() => setSelected(i)}
            className={`btn btn-xs ${i === selected ? 'btn-primary' : 'btn-ghost'}`}
          >
            {dayLabel(d.date)} <span className="tabular-nums">({d.players.length})</span>
          </button>
        ))}
      </div>
      {day && <DayPanel day={day} />}
      <p className="text-xs text-muted">
        Ordered by the model&apos;s projected 9-category value for each game, with {data.slots} starting slots.
      </p>
    </div>
  );
}

function Body({ state, onReload }: LineupCardProps): JSX.Element {
  if (state.status === 'idle' || state.status === 'loading') {
    return <SkeletonTable rows={6} cols={2} label="Loading this week's lineup" />;
  }
  if (state.status === 'error') {
    return (
      <div className="py-6 text-center flex flex-col items-center gap-3">
        <p className="text-sm text-error">{state.message}</p>
        <button onClick={onReload} className="btn btn-sm">Try again</button>
      </div>
    );
  }
  if (state.data.status !== 'ok') {
    return <p className="py-6 text-center text-sm text-muted">{EMPTY_MESSAGES[state.data.status]}</p>;
  }
  return <Lineup data={state.data} />;
}

export function LineupCard({ state, onReload }: LineupCardProps): JSX.Element {
  const loading = state.status === 'loading';
  return (
    <section className="border-t border-base-300 pt-3" aria-labelledby="lineup-heading">
      <div className="pb-2 flex items-center justify-between">
        <h2 id="lineup-heading" className="font-display text-xl font-semibold uppercase tracking-wide">
          This week&apos;s lineup
        </h2>
        <button onClick={onReload} disabled={loading} className="btn btn-ghost btn-xs gap-1.5">
          <IconRefresh size={12} />
          {loading ? 'Refreshing' : 'Refresh'}
        </button>
      </div>
      <Body state={state} onReload={onReload} />
    </section>
  );
}
