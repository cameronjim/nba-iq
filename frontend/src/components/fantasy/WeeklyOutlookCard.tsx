import { IconRefresh } from '../icons';
import { SkeletonTable } from '../Skeleton';
import { formatTimestampWithZone } from '../../utils/analytics';
import type { WeeklyOutlookState } from '../../hooks/useWeeklyOutlook';
import type {
  OutlookCategory,
  OutlookCategoryKey,
  OutlookPlayer,
  WeeklyOutlookResponse,
  WeeklyOutlookStatus,
} from '../../types';

const CATEGORY_LABELS: Record<OutlookCategoryKey, string> = {
  pts: 'PTS',
  reb: 'REB',
  ast: 'AST',
  stl: 'STL',
  blk: 'BLK',
  fg3m: '3PM',
  fg_pct: 'FG%',
  ft_pct: 'FT%',
  tov: 'TO',
};

const EMPTY_MESSAGES: Record<Exclude<WeeklyOutlookStatus, 'ok'>, string> = {
  empty_roster: 'Add players to your roster to see a weekly outlook.',
  no_run: 'No model run is available yet, so there is nothing to simulate.',
  no_games: 'None of your players have projected games in this window.',
};

const HIGH_MISS_RISK = 0.5;
const MODERATE_MISS_RISK = 0.2;

interface WeeklyOutlookCardProps {
  state: WeeklyOutlookState;
  onReload: () => void;
}

function isRatio(category: OutlookCategoryKey): boolean {
  return category === 'fg_pct' || category === 'ft_pct';
}

function formatOutlookValue(category: OutlookCategoryKey, value: number | null): string {
  if (value === null) return '-';
  return isRatio(category) ? `${(value * 100).toFixed(1)}%` : value.toFixed(1);
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function missRiskClass(risk: number): string {
  if (risk >= HIGH_MISS_RISK) return 'text-error';
  if (risk >= MODERATE_MISS_RISK) return 'text-warning';
  return 'text-success';
}

function CategoryRow({ row }: { row: OutlookCategory }): JSX.Element {
  const label = CATEGORY_LABELS[row.category];
  const win = row.win_probability;
  return (
    <tr>
      <td className="font-medium">
        {label}
        {row.lower_is_better && <span className="text-xs text-muted ml-1">(lower wins)</span>}
      </td>
      <td>{formatOutlookValue(row.category, row.p50)}</td>
      <td className="whitespace-nowrap text-xs text-muted">
        {formatOutlookValue(row.category, row.p10)} to {formatOutlookValue(row.category, row.p90)}
      </td>
      <td className="text-xs text-muted">{formatOutlookValue(row.category, row.opponent)}</td>
      <td>
        {win === null ? (
          <span className="text-xs text-faint">-</span>
        ) : (
          <div className="flex items-center gap-2">
            <progress
              className="progress progress-primary w-24"
              value={Math.round(win * 100)}
              max={100}
              aria-label={`${label} win probability`}
            />
            <span className="text-xs tabular-nums">{percent(win)}</span>
          </div>
        )}
      </td>
    </tr>
  );
}

function MissRiskChip({ player }: { player: OutlookPlayer }): JSX.Element {
  if (player.games_scheduled === 0) {
    return <span className="text-xs text-muted">{player.name}: no games</span>;
  }
  return (
    <span
      className={`text-xs font-semibold ${missRiskClass(player.miss_risk)}`}
      title={`${player.games_scheduled} games scheduled, ${player.expected_games.toFixed(1)} expected`}
    >
      {player.name}: {percent(player.miss_risk)} miss risk
    </span>
  );
}

function Provenance({ data }: { data: WeeklyOutlookResponse }): JSX.Element {
  const fallbacks = data.provenance.fallback_spread_players;
  const publishedAt = formatTimestampWithZone(data.provenance.predicted_at);
  return (
    <div className="text-xs text-muted space-y-1">
      <p data-testid="weekly-outlook-provenance">
        {[
          publishedAt ? `Published ${publishedAt}` : null,
          'illustrative outlook: category win odds are against a fixed typical opponent',
        ]
          .filter(Boolean)
          .join(' · ')}
      </p>
      {data.opponent && <p>Typical opponent: {data.opponent.definition}.</p>}
      {fallbacks.length > 0 && (
        <p>
          Default spreads used (no stored quantiles):{' '}
          {fallbacks.map((p) => `${p.name} (${p.stats.join(', ')})`).join('; ')}
        </p>
      )}
    </div>
  );
}

function Body({ state, onReload }: WeeklyOutlookCardProps): JSX.Element {
  if (state.status === 'idle' || state.status === 'loading') {
    return (
      <div className="p-4">
        <SkeletonTable rows={9} cols={5} label="Loading weekly outlook" />
      </div>
    );
  }

  if (state.status === 'error') {
    return (
      <div className="p-6 text-center flex flex-col items-center gap-3">
        <p className="text-sm text-error">{state.message}</p>
        <button onClick={onReload} className="btn btn-sm">Try again</button>
      </div>
    );
  }

  const { data } = state;
  if (data.status !== 'ok') {
    return (
      <div className="p-6 text-center">
        <p className="text-sm text-muted">{EMPTY_MESSAGES[data.status]}</p>
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4">
      <div className="overflow-x-auto">
        <table className="table table-sm">
          <thead>
            <tr>
              <th>Category</th>
              <th title="Median simulated weekly total">Median</th>
              <th title="10th to 90th percentile of the simulated week">p10 to p90</th>
              <th title="Typical opponent's expected weekly total">Opponent</th>
              <th>Win chance</th>
            </tr>
          </thead>
          <tbody>
            {data.categories.map((row) => (
              <CategoryRow key={row.category} row={row} />
            ))}
          </tbody>
        </table>
      </div>

      <div>
        <h3 className="text-sm font-semibold mb-1">Chance of missing at least one game</h3>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {data.players.map((player) => (
            <MissRiskChip key={player.player_id} player={player} />
          ))}
        </div>
      </div>

      <Provenance data={data} />
    </div>
  );
}

export function WeeklyOutlookCard({ state, onReload }: WeeklyOutlookCardProps): JSX.Element {
  const loading = state.status === 'loading';
  const range = state.status === 'ready' ? state.data.window : null;
  return (
    <section className="border-t border-base-300 pt-3">
      <div>
        <div className="pb-2 flex items-center justify-between">
          <h2 className="font-display text-xl font-semibold uppercase tracking-wide">
            Weekly Outlook
            {range && (
              <span className="ml-2 font-sans normal-case tracking-normal font-normal text-xs text-muted">
                {range.from} to {range.to}
              </span>
            )}
          </h2>
          <button onClick={onReload} disabled={loading} className="btn btn-ghost btn-xs gap-1.5">
            <IconRefresh size={12} />
            {loading ? 'Refreshing' : 'Refresh'}
          </button>
        </div>
        <Body state={state} onReload={onReload} />
      </div>
    </section>
  );
}
