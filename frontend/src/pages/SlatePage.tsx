import { Link } from 'react-router-dom';
import { useSlate } from '../hooks/useSlate';
import { formatTimestampBeside, formatTimestampWithZone } from '../utils/analytics';
import { formatRange, formatSlateDate } from '../utils/dates';
import { SlateGameCard } from '../components/slate/SlateGameCard';
import { SlateLegend } from '../components/slate/SlateLegend';
import { SlateSortPicker } from '../components/slate/SlateSortPicker';
import { SlateSkeleton } from '../components/slate/SlateSkeleton';
import type { SlateRun, SlateSort } from '../types';

const NO_RUN_NOTICE = 'No prediction run yet. Check back after the next model run.';

function coverageNotice(date: string, run: SlateRun): string {
  const covers =
    run.covers_from && run.covers_to
      ? `The latest run covers ${formatRange(run.covers_from, run.covers_to)}`
      : 'The latest run projected no games';
  return `No projections for ${formatSlateDate(date)} yet. ${covers}; each day's run looks seven days ahead and publishes around 9 AM PT.`;
}

const PRESEASON_NOTE =
  "Preseason minutes are not modelled: the model is trained on regular-season games, so starters' minutes and totals here run high.";

const ORDER_NOTE: Record<SlateSort, string> = {
  impact:
    'Players and games are ordered by projected impact across all nine categories. 0 is an average night.',
  edge:
    "Players and games are ordered by how far tonight's projection moves from each player's own usual, up or down, weighted toward minutes and points.",
};

export const SlatePage = (): JSX.Element => {
  const { date, isToday, setDate, sort, setSort, data, loading, error, reload } = useSlate();

  const run = data?.run ?? null;
  const publishedAt = formatTimestampWithZone(run?.predicted_at ?? null);
  const injuriesAsOf = formatTimestampBeside(
    run?.information_as_of ?? null,
    run?.predicted_at ?? null
  );
  const hasPreseason = data?.games.some((game) => game.preseason) ?? false;
  const scheduleOnly = run !== null && data?.covered === false;

  return (
    <div className="max-w-[900px] mx-auto px-4 py-6 pb-20">
      <header className="flex flex-wrap items-end justify-between gap-3 mb-5">
        <div>
          <h1 className="font-display text-3xl font-semibold uppercase tracking-wide leading-tight">
            {isToday ? <>Today&apos;s Projections</> : 'Projections'}
          </h1>
          <p className="text-sm text-muted mt-0.5" data-testid="slate-subtitle">
            {formatSlateDate(data?.date ?? date)}
            {publishedAt && ` · published ${publishedAt}`}
            {injuriesAsOf && ` · injuries as of ${injuriesAsOf}`}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <SlateSortPicker sort={sort} onChange={setSort} />
          <label className="form-control">
            <span className="sr-only">Game date</span>
            <input
              type="date"
              className="input input-bordered input-sm"
              value={date}
              aria-label="Game date"
              onChange={(e) => setDate(e.target.value)}
            />
          </label>
        </div>
      </header>

      {loading && !data ? (
        <SlateSkeleton />
      ) : !data ? (
        <div className="card border border-base-300">
          <div className="card-body flex flex-col items-center py-12 gap-4">
            <p className="text-error text-sm">{error || 'Failed to load the slate'}</p>
            <button onClick={() => void reload()} className="btn btn-primary btn-sm">
              Try Again
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {!data.run && (
            <div className="alert py-2.5 px-3">
              <span className="text-sm">{NO_RUN_NOTICE}</span>
            </div>
          )}

          {data.run && scheduleOnly && data.games.length > 0 && (
            <div className="alert py-2.5 px-3" data-testid="slate-coverage-notice">
              <span className="text-sm">{coverageNotice(data.date, data.run)}</span>
            </div>
          )}

          {data.games.length === 0 ? (
            <div className="card border border-base-300">
              <div className="card-body items-center text-center py-12 gap-1">
                <p className="text-sm font-semibold">No games scheduled</p>
                <p className="text-xs text-muted max-w-md">
                  Nothing on the schedule for {formatSlateDate(data.date)}. Pick another date, or
                  check{' '}
                  <Link to="/watchlist" className="link link-primary">
                    the watchlist
                  </Link>
                  .
                </p>
              </div>
            </div>
          ) : (
            <>
              {data.run && !scheduleOnly && <SlateLegend />}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {data.games.map((game) => (
                  <SlateGameCard
                    key={game.nba_game_id}
                    game={game}
                    scheduleOnly={scheduleOnly}
                  />
                ))}
              </div>
            </>
          )}
        </div>
      )}

      <footer className="text-[11px] text-muted mt-6 pt-3 border-t border-base-300 flex flex-col gap-1">
        <span data-testid="slate-order-note">{ORDER_NOTE[sort]}</span>
        <span>
          The headline points, minutes and category line are what he projects if he plays.
        </span>
        <span>
          The muted line is the same points with his chance of sitting priced in, as of when the
          run was published; the injury chip is the report right now.
        </span>
        {hasPreseason && <span data-testid="slate-preseason-note">{PRESEASON_NOTE}</span>}
      </footer>
    </div>
  );
};
