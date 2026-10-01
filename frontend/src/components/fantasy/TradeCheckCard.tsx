import { useMemo, useState } from 'react';
import { IconClose, IconSearch } from '../icons';
import { SkeletonLines } from '../Skeleton';
import { usePlayerSearch } from '../../hooks/usePlayerSearch';
import type { TradeCheckState } from '../../hooks/useTradeCheck';
import type { TradeCategoryDelta, TradeCheckResponse, TradeCheckStatus } from '../../types';

const EMPTY_MESSAGES: Record<Exclude<TradeCheckStatus, 'ok'>, string> = {
  empty_roster: 'Add players to your roster before checking a trade.',
  no_run: 'No model run is available yet, so there is nothing to simulate.',
  no_games: 'None of these players have projected games in the next week.',
};

const TRADE_NOTE = "illustrative verdict: based on the model's projections and a typical opponent";

interface PickedPlayer {
  id: number;
  name: string;
}

interface TradeCheckCardProps {
  roster: PickedPlayer[];
  state: TradeCheckState;
  onCheck: (give: number[], get: number[]) => void;
}

function percent(value: number | null): string {
  return value === null ? '-' : `${Math.round(value * 100)}%`;
}

function signed(value: number | null): string {
  if (value === null) return '-';
  const fixed = Math.abs(value).toFixed(2);
  return `${value < 0 && Number(fixed) !== 0 ? '-' : '+'}${fixed}`;
}

function deltaClass(value: number | null): string {
  if (value === null || Math.abs(value) < 0.005) return 'text-muted';
  return value > 0 ? 'text-success' : 'text-error';
}

function CategoryTable({ rows }: { rows: TradeCategoryDelta[] }): JSX.Element {
  return (
    <div className="overflow-x-auto">
      <table className="table table-xs">
        <thead>
          <tr>
            <th>Category</th>
            <th title="Chance to win the category this week with your current roster">Now</th>
            <th title="Chance to win the category this week after the trade">After</th>
            <th title="Change in expected category wins">Change</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.category}>
              <td className="font-medium">{row.label}</td>
              <td className="tabular-nums">{percent(row.before)}</td>
              <td className="tabular-nums">{percent(row.after)}</td>
              <td className={`tabular-nums ${deltaClass(row.delta)}`}>{signed(row.delta)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Result({ data }: { data: TradeCheckResponse }): JSX.Element {
  if (data.status !== 'ok' || data.verdict === null) {
    const message = data.status === 'ok' ? EMPTY_MESSAGES.no_games : EMPTY_MESSAGES[data.status];
    return <p className="text-sm text-muted">{message}</p>;
  }
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium" data-testid="trade-verdict">{data.verdict}</p>
      {data.before_expected_wins !== null && data.after_expected_wins !== null && (
        <p className="text-xs text-muted tabular-nums">
          Expected category wins: {data.before_expected_wins.toFixed(1)} now, {data.after_expected_wins.toFixed(1)} after.
        </p>
      )}
      <CategoryTable rows={data.categories} />
      <p className="text-xs text-muted" data-testid="trade-note">{TRADE_NOTE}</p>
    </div>
  );
}

function Outcome({ state }: { state: TradeCheckState }): JSX.Element | null {
  if (state.status === 'idle') return null;
  if (state.status === 'checking') return <SkeletonLines lines={4} label="Checking trade" />;
  if (state.status === 'error') return <p className="text-sm text-error">{state.message}</p>;
  return <Result data={state.data} />;
}

export function TradeCheckCard({ roster, state, onCheck }: TradeCheckCardProps): JSX.Element {
  const [give, setGive] = useState<number[]>([]);
  const [get, setGet] = useState<PickedPlayer[]>([]);
  const [term, setTerm] = useState('');

  const rosterIds = useMemo(() => new Set(roster.map((p) => p.id)), [roster]);
  const excluded = useMemo(() => new Set([...rosterIds, ...get.map((p) => p.id)]), [rosterIds, get]);
  const search = usePlayerSearch(term, excluded);

  const giving = give.filter((id) => rosterIds.has(id));
  const checking = state.status === 'checking';
  const canCheck = giving.length > 0 && get.length > 0 && !checking;

  const toggleGive = (id: number): void => {
    setGive((prev) => (prev.includes(id) ? prev.filter((g) => g !== id) : [...prev, id]));
  };
  const addGet = (player: PickedPlayer): void => {
    setGet((prev) => [...prev, { id: player.id, name: player.name }]);
    setTerm('');
  };
  const removeGet = (id: number): void => {
    setGet((prev) => prev.filter((p) => p.id !== id));
  };

  return (
    <section className="border-t border-base-300 pt-3" aria-labelledby="trade-heading">
      <h2 id="trade-heading" className="pb-2 font-display text-xl font-semibold uppercase tracking-wide">
        Check a trade
      </h2>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <fieldset>
          <legend className="text-sm font-semibold mb-1">You give</legend>
          <div className="flex flex-wrap gap-1">
            {roster.map((player) => {
              const selected = giving.includes(player.id);
              return (
                <button
                  key={player.id}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => toggleGive(player.id)}
                  className={`btn btn-xs ${selected ? 'btn-primary' : 'btn-ghost border border-base-300'}`}
                >
                  {player.name}
                </button>
              );
            })}
          </div>
        </fieldset>

        <fieldset>
          <legend className="text-sm font-semibold mb-1">You get</legend>
          <label className="input input-bordered input-sm flex items-center gap-2">
            <IconSearch size={14} className="text-muted" />
            <input
              type="text"
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              placeholder="Search players to get"
              aria-label="Search players to get"
              className="grow"
            />
            {search.searching && <span className="text-xs text-muted">Searching</span>}
          </label>
          {search.failed && <p className="text-xs text-error mt-1">Player search failed. Try again.</p>}
          {search.results.length > 0 && (
            <ul className="mt-1 border border-base-300 divide-y divide-base-300 max-h-48 overflow-y-auto">
              {search.results.map((player) => (
                <li key={player.id}>
                  <button
                    type="button"
                    onClick={() => addGet(player)}
                    className="w-full text-left px-2 py-1 text-sm hover:bg-base-200"
                  >
                    {player.name} <span className="text-xs text-muted">{player.position} · {player.team}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {get.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {get.map((player) => (
                <span key={player.id} className="badge badge-outline gap-1">
                  {player.name}
                  <button type="button" onClick={() => removeGet(player.id)} aria-label={`Remove ${player.name}`}>
                    <IconClose size={10} />
                  </button>
                </span>
              ))}
            </div>
          )}
        </fieldset>
      </div>

      <div className="mt-3 space-y-3">
        <button
          type="button"
          onClick={() => onCheck(giving, get.map((p) => p.id))}
          disabled={!canCheck}
          className="btn btn-primary btn-sm"
        >
          {checking ? 'Checking' : 'Check trade'}
        </button>
        <Outcome state={state} />
      </div>
    </section>
  );
}
