import { SkeletonTable } from '../Skeleton';
import { usePropJobs } from '../../hooks/usePropJobs';
import { STAT_PLACEHOLDER } from '../../utils/stats';
import {
  clvOrPlaceholder, hasSettledPicks, percentOrPlaceholder,
} from '../../utils/propJobs';

export const PropJobsPanel = (): JSX.Element => {
  const { job, summary, run } = usePropJobs();
  const running = job.state === 'running';

  return (
    <section>
      <h2 className="mb-3 font-display text-2xl font-semibold uppercase tracking-wide">Prop picks</h2>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn btn-sm btn-outline" disabled={running} onClick={() => void run('refresh')}>
          {running && job.job === 'refresh' ? 'Refreshing' : 'Refresh prop picks'}
        </button>
        <button type="button" className="btn btn-sm btn-outline" disabled={running} onClick={() => void run('settle')}>
          {running && job.job === 'settle' ? 'Settling' : 'Settle prop picks'}
        </button>
      </div>
      {job.state === 'done' && <p className="mt-3 text-muted" role="status">{job.message}</p>}
      {job.state === 'failed' && <p className="mt-3 text-error" role="alert">{job.message}</p>}

      <div className="mt-4">
        {summary.status === 'loading' && <SkeletonTable rows={3} cols={5} label="Loading prop summary" />}
        {summary.status === 'error' && (
          <p className="text-error" role="alert">Could not load the prop pick summary.</p>
        )}
        {summary.status === 'ready' && !hasSettledPicks(summary.markets) && (
          <p className="text-muted">No settled prop picks yet.</p>
        )}
        {summary.status === 'ready' && hasSettledPicks(summary.markets) && (
          <div className="overflow-x-auto border border-base-300 rounded-box">
            <table className="table table-sm">
              <thead>
                <tr>
                  <th>Market</th>
                  <th className="text-right">Picks</th>
                  <th className="text-right">Hit rate</th>
                  <th className="text-right">Avg EV</th>
                  <th className="text-right">Avg CLV</th>
                </tr>
              </thead>
              <tbody>
                {summary.markets.map((m) => (
                  <tr key={m.market}>
                    <td className="font-medium uppercase">{m.market}</td>
                    <td className="text-right">{m.picks}</td>
                    <td className="text-right">{percentOrPlaceholder(m.hit_rate, STAT_PLACEHOLDER)}</td>
                    <td className="text-right">{percentOrPlaceholder(m.avg_ev, STAT_PLACEHOLDER)}</td>
                    <td className="text-right">{clvOrPlaceholder(m.avg_clv_points, STAT_PLACEHOLDER)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
};
