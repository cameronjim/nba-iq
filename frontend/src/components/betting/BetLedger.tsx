import { useState } from 'react';
import { IconTrash } from '../icons';
import { SkeletonTable } from '../Skeleton';
import { formatAmerican, formatLine, formatMoney, formatSignedMoney } from '../../utils/formatOdds';
import type {
  Bet, BettingGame, BetStatus, LedgerSummary, NewBet, NewBetGameRef,
  BetMarket, BetSelection, StraightMarket, WagerType,
} from '../../types';

interface BetLedgerProps {
  bets: Bet[];
  summary: LedgerSummary;
  loading: boolean;
  error: string;
  games: BettingGame[];
  onTrackBet: (bet: NewBet, gameRef?: NewBetGameRef) => Promise<void>;
  onSettleBet: (id: number, status: BetStatus) => Promise<void>;
  onRemoveBet: (id: number) => Promise<void>;
}

const STATUS_RESULT: Record<Bet['status'], { text: string; className: string }> = {
  pending: { text: 'Pending', className: 'text-muted' },
  won: { text: 'W', className: 'text-success font-semibold' },
  lost: { text: 'L', className: 'text-error font-semibold' },
  push: { text: 'P', className: 'font-semibold' },
};

const MARKET_LABEL: Record<BetMarket, string> = {
  spread: 'Spread',
  total: 'Total',
  moneyline: 'Moneyline',
  prop: 'Player prop',
  parlay: 'Parlay',
  custom: 'Custom',
};

const WAGER_LABEL: Record<WagerType, string> = {
  cash: 'Cash',
  bonus_bet: 'Bonus bet',
  odds_boost: 'Odds boost',
};

// straight bets settle automatically from final scores; the rest are graded by hand.
const STRAIGHT: BetMarket[] = ['spread', 'total', 'moneyline'];

function betLabel(bet: Bet): string {
  if (bet.description) return bet.description;
  if (bet.market === 'total') {
    return `${bet.selection === 'over' ? 'Over' : 'Under'} ${bet.line}`;
  }
  const team = bet.selection === 'home' ? bet.home_team : bet.away_team;
  if (bet.market === 'moneyline') return `${team} ML`;
  return `${team} ${bet.line != null ? formatLine(bet.line) : ''}`.trim();
}

interface AddBetFormProps {
  games: BettingGame[];
  onTrackBet: (bet: NewBet, gameRef?: NewBetGameRef) => Promise<void>;
  onDone: () => void;
}

const AddBetForm = ({ games, onTrackBet, onDone }: AddBetFormProps) => {
  const [market, setMarket] = useState<BetMarket>('spread');
  const [gameId, setGameId] = useState('');
  const [selection, setSelection] = useState<BetSelection>('home');
  const [description, setDescription] = useState('');
  const [oddsText, setOddsText] = useState('');
  const [stakeText, setStakeText] = useState('');
  const [wagerType, setWagerType] = useState<WagerType>('cash');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const isStraight = STRAIGHT.includes(market);
  const game = games.find((g) => g.espn_event_id === gameId);

  // line and odds come from the posted markets; unposted markets are flagged.
  const resolveStraight = (): { line: number | null; odds: number } | null => {
    if (!game) return null;
    const { spread, total, moneyline } = game.markets;
    if (market === 'spread' && spread) {
      const odds = selection === 'home' ? spread.home_price : spread.away_price;
      if (odds == null) return null;
      return { line: selection === 'home' ? spread.home_line : spread.away_line, odds };
    }
    if (market === 'total' && total) {
      const odds = selection === 'over' ? total.over_price : total.under_price;
      return odds == null ? null : { line: total.line, odds };
    }
    if (market === 'moneyline' && moneyline) {
      return { line: null, odds: selection === 'home' ? moneyline.home : moneyline.away };
    }
    return null;
  };
  const resolved = isStraight ? resolveStraight() : null;

  const selectionChoices: Array<{ value: BetSelection; label: string }> =
    market === 'total'
      ? [{ value: 'over', label: 'Over' }, { value: 'under', label: 'Under' }]
      : [
          { value: 'away', label: game ? game.away_team : 'Away' },
          { value: 'home', label: game ? game.home_team : 'Home' },
        ];

  const handleMarketChange = (next: BetMarket): void => {
    setMarket(next);
    setSelection(next === 'total' ? 'over' : 'home');
    setError('');
  };

  const parsedOdds = (): number | null => {
    const trimmed = oddsText.trim();
    if (!trimmed) return null;
    const n = parseInt(trimmed, 10);
    return Number.isNaN(n) ? null : n;
  };

  const parsedStake = (): number | null => {
    const n = parseFloat(stakeText.trim());
    return Number.isNaN(n) || n <= 0 ? null : n;
  };

  const canSubmit =
    parsedStake() != null &&
    (isStraight
      ? !!game && !!resolved
      : description.trim().length >= 3 && parsedOdds() != null);

  const handleSubmit = async (): Promise<void> => {
    const stake = parsedStake();
    const odds = parsedOdds();
    if (!canSubmit || stake == null || saving) return;
    setSaving(true);
    setError('');
    try {
      // applies optimistically, so the form closes without waiting for the network.
      const gameRef = game
        ? { home_team: game.home_team, away_team: game.away_team, game_date: game.game_date }
        : undefined;
      if (isStraight && game && resolved) {
        await onTrackBet({
          market: market as StraightMarket,
          nba_game_id: game.espn_event_id,
          selection,
          line: resolved.line,
          american_odds: resolved.odds,
          stake,
          wager_type: wagerType,
        }, gameRef);
      } else if (odds != null) {
        await onTrackBet({
          market,
          description: description.trim(),
          american_odds: odds,
          stake,
          wager_type: wagerType,
          ...(market === 'prop' && game ? { nba_game_id: game.espn_event_id } : {}),
        }, market === 'prop' ? gameRef : undefined);
      }
      onDone();
    } catch {
      setError('Failed to add bet');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="border border-base-300 bg-base-200 p-3 space-y-3">
      <div className="flex flex-wrap gap-3 items-end">
        <div>
          <label className="text-xs font-semibold block mb-1" htmlFor="addbet-market">Bet type</label>
          <select
            id="addbet-market"
            className="select select-bordered select-sm"
            value={market}
            onChange={(e) => handleMarketChange(e.target.value as BetMarket)}
          >
            {(Object.keys(MARKET_LABEL) as BetMarket[]).map((m) => (
              <option key={m} value={m}>{MARKET_LABEL[m]}</option>
            ))}
          </select>
        </div>

        {(isStraight || market === 'prop') && (
          <div>
            <label className="text-xs font-semibold block mb-1" htmlFor="addbet-game">
              Game{market === 'prop' ? ' (optional)' : ''}
            </label>
            <select
              id="addbet-game"
              className="select select-bordered select-sm w-56"
              value={gameId}
              onChange={(e) => setGameId(e.target.value)}
            >
              <option value="">Pick a game...</option>
              {games.map((g) => (
                <option key={g.espn_event_id} value={g.espn_event_id}>
                  {g.away_team} @ {g.home_team} ({g.game_date})
                </option>
              ))}
            </select>
          </div>
        )}

        {isStraight && (
          <div>
            <label className="text-xs font-semibold block mb-1" htmlFor="addbet-selection">Side</label>
            <select
              id="addbet-selection"
              className="select select-bordered select-sm w-44"
              value={selection}
              onChange={(e) => setSelection(e.target.value as BetSelection)}
            >
              {selectionChoices.map((c) => (
                <option key={c.value} value={c.value}>{c.label}</option>
              ))}
            </select>
          </div>
        )}

        {!isStraight && (
          <div>
            <label className="text-xs font-semibold block mb-1" htmlFor="addbet-odds">Odds</label>
            <input
              id="addbet-odds"
              type="text"
              placeholder="+600"
              value={oddsText}
              onChange={(e) => setOddsText(e.target.value)}
              className="input input-bordered input-sm w-24"
            />
          </div>
        )}

        <div>
          <label className="text-xs font-semibold block mb-1" htmlFor="addbet-stake">Stake</label>
          <label className="input input-bordered input-sm flex items-center gap-1 w-28">
            $
            <input
              id="addbet-stake"
              type="number"
              min={0.01}
              step={0.01}
              placeholder="10"
              value={stakeText}
              onChange={(e) => setStakeText(e.target.value)}
              className="w-full"
            />
          </label>
        </div>

        <div>
          <label className="text-xs font-semibold block mb-1" htmlFor="addbet-wager">Wager kind</label>
          <select
            id="addbet-wager"
            className="select select-bordered select-sm"
            value={wagerType}
            onChange={(e) => setWagerType(e.target.value as WagerType)}
          >
            {(Object.keys(WAGER_LABEL) as WagerType[]).map((w) => (
              <option key={w} value={w}>{WAGER_LABEL[w]}</option>
            ))}
          </select>
        </div>

        <button
          onClick={handleSubmit}
          disabled={!canSubmit || saving}
          className="btn btn-primary btn-sm"
        >
          {saving ? 'Adding' : 'Add bet'}
        </button>
        <button onClick={onDone} className="btn btn-ghost btn-sm">Cancel</button>
      </div>

      {!isStraight && (
        <div>
          <label className="text-xs font-semibold block mb-1" htmlFor="addbet-description">
            {market === 'prop' && 'Describe the prop'}
            {market === 'parlay' && 'List the legs'}
            {market === 'custom' && 'Describe the bet'}
          </label>
          <input
            id="addbet-description"
            type="text"
            maxLength={300}
            placeholder={
              market === 'prop'
                ? 'e.g. "Brunson over 28.5 points"'
                : market === 'parlay'
                  ? 'e.g. "Knicks ML + Under 216.5 + Celtics -3"'
                  : 'e.g. "First basket: Wembanyama (from another sportsbook)"'
            }
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="input input-bordered input-sm w-full"
          />
        </div>
      )}

      {isStraight && game && !resolved && (
        <p className="text-xs text-warning">That market isn't posted for this game yet.</p>
      )}
      {isStraight && game && resolved && (
        <p className="text-xs text-muted">
          Line: {resolved.line != null ? formatLine(resolved.line) : 'n/a'} · Odds: {formatAmerican(resolved.odds)}
        </p>
      )}
      {error && <p className="text-xs text-error">{error}</p>}
    </div>
  );
};

export const BetLedger = ({ bets, summary, loading, error, games, onTrackBet, onSettleBet, onRemoveBet }: BetLedgerProps) => {
  const [adding, setAdding] = useState(false);

  const hasMoney = bets.some((b) => b.stake != null);

  return (
    <section>
      <div className="border-b border-base-300 pb-1 mb-3 flex items-baseline justify-between">
        <h2 className="font-display text-xl font-semibold uppercase tracking-wide">My Bets</h2>
        {!adding && (
          <button onClick={() => setAdding(true)} className="btn btn-ghost btn-xs">+ Add bet</button>
        )}
      </div>

      <div className="space-y-4">
        {adding && <AddBetForm games={games} onTrackBet={onTrackBet} onDone={() => setAdding(false)} />}

        {loading ? (
          <SkeletonTable rows={4} cols={7} label="Loading bets" />
        ) : error ? (
          <p className="text-error text-sm text-center py-4">{error}</p>
        ) : bets.length === 0 ? (
          <p className="text-sm text-muted py-4">
            No bets tracked yet. Add spreads, totals, moneylines, props, parlays, or anything custom.
          </p>
        ) : (
          <>
            <p className="text-xs font-semibold">
              <span>{summary.pending} pending</span>
              {hasMoney && (
                <span className={`ml-3 ${summary.net > 0 ? 'text-success' : summary.net < 0 ? 'text-error' : 'text-muted'}`}>
                  Net: {formatSignedMoney(summary.net)}
                </span>
              )}
            </p>

            <div className="overflow-x-auto">
              <table className="table table-sm">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Type</th>
                    <th>Bet</th>
                    <th>Game</th>
                    <th>Odds</th>
                    <th>Stake</th>
                    <th>Status</th>
                    <th>+/-</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {bets.map((bet) => {
                    const manual = !STRAIGHT.includes(bet.market);
                    return (
                      <tr key={bet.id}>
                        <td className="whitespace-nowrap text-xs">
                          {(bet.game_date ?? bet.created_at).slice(0, 10)}
                        </td>
                        <td className="text-xs whitespace-nowrap">
                          {MARKET_LABEL[bet.market]}
                          {bet.wager_type !== 'cash' && (
                            <span className="text-muted"> ({WAGER_LABEL[bet.wager_type]})</span>
                          )}
                        </td>
                        <td className="font-medium text-xs max-w-60">{betLabel(bet)}</td>
                        <td className="text-xs">
                          {bet.home_team ? `${bet.away_team} @ ${bet.home_team}` : ''}
                        </td>
                        <td className="text-xs">{bet.american_odds != null ? formatAmerican(bet.american_odds) : ''}</td>
                        <td className="text-xs">{bet.stake != null ? formatMoney(bet.stake) : ''}</td>
                        <td className={`text-xs ${STATUS_RESULT[bet.status].className}`} title={bet.status}>
                          {STATUS_RESULT[bet.status].text}
                        </td>
                        <td className={`text-xs font-medium whitespace-nowrap ${
                          bet.net != null && bet.net > 0 ? 'text-success' : bet.net != null && bet.net < 0 ? 'text-error' : 'text-muted'
                        }`}>
                          {bet.net != null
                            ? formatSignedMoney(bet.net)
                            : bet.status === 'pending' && bet.to_win != null
                              ? `to win ${formatMoney(bet.to_win)}`
                              : ''}
                        </td>
                        <td>
                          <div className="flex items-center gap-1 justify-end">
                            {bet.status === 'pending' && manual && (
                              <>
                                <button
                                  onClick={() => void onSettleBet(bet.id, 'won')}
                                  className="btn btn-ghost btn-xs text-success"
                                  title="Mark this bet as won"
                                >
                                  Won
                                </button>
                                <button
                                  onClick={() => void onSettleBet(bet.id, 'lost')}
                                  className="btn btn-ghost btn-xs text-error"
                                  title="Mark this bet as lost"
                                >
                                  Lost
                                </button>
                              </>
                            )}
                            <button
                              onClick={() => void onRemoveBet(bet.id)}
                              className="btn btn-ghost btn-xs"
                              aria-label={`Delete bet: ${betLabel(bet)}`}
                              title="Delete this bet"
                            >
                              <IconTrash size={12} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </section>
  );
};
