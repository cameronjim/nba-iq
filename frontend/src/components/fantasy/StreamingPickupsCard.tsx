import { IconPlus, IconRefresh } from '../icons';
import { SkeletonLines } from '../Skeleton';
import type { RosterResourceState } from '../../hooks/useRosterResource';
import type { Streamer, StreamersResponse, StreamersStatus } from '../../types';

const EMPTY_MESSAGES: Record<Exclude<StreamersStatus, 'ok'>, string> = {
  empty_roster: 'Add players to your roster to see streaming pickups.',
  no_candidates: 'No free agents in the ranking pool are projected for this window.',
};

interface StreamingPickupsCardProps {
  state: RosterResourceState<StreamersResponse>;
  onReload: () => void;
  onAdd: (streamer: Streamer) => void;
  addingId: number | null;
}

function driversText(streamer: Streamer): string | null {
  const helps = streamer.drivers.filter((d) => d.value > 0).map((d) => d.label);
  return helps.length === 0 ? null : `Helps most in ${helps.join(', ')}`;
}

function Body({ state, onReload, onAdd, addingId }: StreamingPickupsCardProps): JSX.Element {
  if (state.status === 'idle' || state.status === 'loading') {
    return <SkeletonLines lines={5} label="Loading streaming pickups" />;
  }
  if (state.status === 'error') {
    return (
      <div className="py-6 text-center flex flex-col items-center gap-3">
        <p className="text-sm text-error">{state.message}</p>
        <button onClick={onReload} className="btn btn-sm">Try again</button>
      </div>
    );
  }
  const { data } = state;
  if (data.status !== 'ok') {
    return <p className="py-6 text-center text-sm text-muted">{EMPTY_MESSAGES[data.status]}</p>;
  }
  return (
    <div className="space-y-2">
      <ol className="divide-y divide-base-300 border-y border-base-300">
        {data.streamers.map((streamer) => {
          const drivers = driversText(streamer);
          const adding = addingId === streamer.id;
          return (
            <li key={streamer.id} className="flex items-center justify-between gap-3 py-2">
              <div>
                <p className="text-sm">{streamer.sentence}</p>
                {drivers && <p className="text-xs text-muted">{drivers}</p>}
              </div>
              <button
                onClick={() => onAdd(streamer)}
                disabled={addingId !== null}
                className="btn btn-primary btn-xs gap-1 shrink-0"
                aria-label={`Add ${streamer.name}`}
              >
                <IconPlus size={12} /> {adding ? 'Adding' : 'Add'}
              </button>
            </li>
          );
        })}
      </ol>
      <p className="text-xs text-muted">
        Ranked by expected category wins added over the window, so players with more games rank higher.
      </p>
    </div>
  );
}

export function StreamingPickupsCard(props: StreamingPickupsCardProps): JSX.Element {
  const loading = props.state.status === 'loading';
  return (
    <section className="border-t border-base-300 pt-3" aria-labelledby="streamers-heading">
      <div className="pb-2 flex items-center justify-between">
        <h2 id="streamers-heading" className="font-display text-xl font-semibold uppercase tracking-wide">
          Streaming pickups
        </h2>
        <button onClick={props.onReload} disabled={loading} className="btn btn-ghost btn-xs gap-1.5">
          <IconRefresh size={12} />
          {loading ? 'Refreshing' : 'Refresh'}
        </button>
      </div>
      <Body {...props} />
    </section>
  );
}
