import { useState } from 'react';
import { statLabel, formatTimestampWithZone } from '../../utils/analytics';
import { UpcomingGameRow } from './UpcomingGameRow';
import type { PlayerPredictionsResponse } from '../../types';

interface PlayerUpcomingGamesProps {
  data: PlayerPredictionsResponse | null;
}

const ALL_STATS = 'all';

// columns are built from whatever the run emitted, never a hardcoded stat list.
export const PlayerUpcomingGames = ({ data }: PlayerUpcomingGamesProps): JSX.Element | null => {
  const [selected, setSelected] = useState<string>(ALL_STATS);

  if (!data) return null;

  const { run, stats, games } = data;
  // a stat that vanished from a newer run must not leave the table blank.
  const active = selected !== ALL_STATS && stats.includes(selected) ? selected : ALL_STATS;
  const columns = active === ALL_STATS ? stats : [active];

  return (
    <section
      id="upcoming-games"
      data-testid="upcoming-games-section"
      className="border-t border-base-300 pt-4"
    >
      <div className="flex flex-col gap-3">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-lg font-semibold">Upcoming games</h2>
            {games.length > 0 && (
              <span className="badge badge-sm badge-outline">{games.length} games</span>
            )}
          </div>
          <p className="text-xs text-muted mt-0.5">
            Every projected game, earliest first. Stat lines assume{' '}
            <span className="font-semibold">he plays</span>; the badge is a model estimate, not an
            official injury designation.
          </p>
        </div>

        {run === null ? (
          <div className="py-8 text-center">
            <p className="text-sm font-semibold">No prediction run published yet</p>
            <p className="text-xs text-muted mt-1 max-w-md mx-auto">
              Fills in once a model run completes.
            </p>
          </div>
        ) : games.length === 0 ? (
          <div className="py-8 text-center">
            <p className="text-sm font-semibold">No upcoming games for this player in the current run</p>
            <p className="text-xs text-muted mt-1 max-w-md mx-auto">
              The run completed, but has no scheduled games for him.
            </p>
          </div>
        ) : (
          <>
            {stats.length > 1 && (
              <div
                role="tablist"
                aria-label="Prediction stat"
                className="flex flex-wrap gap-1 overflow-x-auto no-scrollbar"
              >
                <button
                  role="tab"
                  aria-selected={active === ALL_STATS}
                  onClick={() => setSelected(ALL_STATS)}
                  className={`btn btn-xs ${active === ALL_STATS ? 'btn-primary' : 'btn-ghost'}`}
                >
                  All
                </button>
                {stats.map((stat) => (
                  <button
                    key={stat}
                    role="tab"
                    aria-selected={active === stat}
                    onClick={() => setSelected(stat)}
                    className={`btn btn-xs ${active === stat ? 'btn-primary' : 'btn-ghost'}`}
                  >
                    {statLabel(stat)}
                  </button>
                ))}
              </div>
            )}

            <div className="overflow-x-auto border border-base-300">
              <table className="table table-xs w-full" data-testid="upcoming-games-table">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Opp</th>
                    <th>Availability</th>
                    {columns.map((stat) => (
                      <th key={stat} className="text-right whitespace-nowrap">
                        {statLabel(stat)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {games.map((game) => (
                    <UpcomingGameRow key={game.nba_game_id} game={game} columns={columns} />
                  ))}
                </tbody>
              </table>
            </div>

            <p className="text-[10px] text-muted">
              Each cell: the median if he plays, its likely range under it, and{' '}
              <span className="font-semibold">sched</span>, the same number counting the chance he
              sits.
            </p>

            <p className="text-[10px] text-faint">
              {[
                formatTimestampWithZone(run.predicted_at)
                  ? `published ${formatTimestampWithZone(run.predicted_at)}`
                  : null,
                formatTimestampWithZone(run.forecast_cutoff_at)
                  ? `data through ${formatTimestampWithZone(run.forecast_cutoff_at)}`
                  : null,
              ]
                .filter(Boolean)
                .join(' · ')}
            </p>
          </>
        )}
      </div>
    </section>
  );
};
