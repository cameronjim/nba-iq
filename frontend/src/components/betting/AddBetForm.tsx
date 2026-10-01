import { useState } from 'react';
import { formatAmerican, formatLine } from '../../utils/formatOdds';
import type {
  BetMarket, BetSelection, BettingGame, NewBet, NewBetGameRef, StraightMarket, WagerType,
} from '../../types';

interface AddBetFormProps {
  games: BettingGame[];
  onTrackBet: (bet: NewBet, gameRef?: NewBetGameRef) => Promise<void>;
  onDone: () => void;
}

const MARKET_LABEL: Record<BetMarket, string> = {
  spread: 'Spread',
  total: 'Total (over/under)',
  moneyline: 'Team to win',
  prop: 'Player prop',
  parlay: 'Parlay',
  custom: 'Something else',
};

const WAGER_LABEL: Record<WagerType, string> = {
  cash: 'Cash',
  bonus_bet: 'Bonus bet',
  odds_boost: 'Odds boost',
};

const STRAIGHT: BetMarket[] = ['spread', 'total', 'moneyline'];

const isStraightMarket = (m: BetMarket): m is StraightMarket => STRAIGHT.includes(m);

interface PostedMarket {
  line: number | null;
  odds: number;
}

function postedMarket(game: BettingGame | undefined, market: BetMarket, selection: BetSelection): PostedMarket | null {
  if (!game) return null;
  const { spread, total, moneyline } = game.markets;
  if (market === 'spread' && spread) {
    const odds = selection === 'home' ? spread.home_price : spread.away_price;
    return odds == null ? null : { line: selection === 'home' ? spread.home_line : spread.away_line, odds };
  }
  if (market === 'total' && total) {
    const odds = selection === 'over' ? total.over_price : total.under_price;
    return odds == null ? null : { line: total.line, odds };
  }
  if (market === 'moneyline' && moneyline) {
    return { line: null, odds: selection === 'home' ? moneyline.home : moneyline.away };
  }
  return null;
}

const parseOdds = (text: string): number | null => {
  const n = parseInt(text.trim(), 10);
  return Number.isNaN(n) ? null : n;
};

const parseNumber = (text: string): number | null => {
  const n = parseFloat(text.trim());
  return Number.isNaN(n) ? null : n;
};

const LABEL = 'text-xs font-semibold block mb-1';

export const AddBetForm = ({ games, onTrackBet, onDone }: AddBetFormProps): JSX.Element => {
  const [market, setMarket] = useState<BetMarket>('spread');
  const [gameId, setGameId] = useState('');
  const [selection, setSelection] = useState<BetSelection>('home');
  const [description, setDescription] = useState('');
  const [lineText, setLineText] = useState('');
  const [oddsText, setOddsText] = useState('');
  const [stakeText, setStakeText] = useState('');
  const [wagerType, setWagerType] = useState<WagerType>('cash');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const isStraight = STRAIGHT.includes(market);
  const game = games.find((g) => g.espn_event_id === gameId);
  const posted = isStraight ? postedMarket(game, market, selection) : null;

  // line and odds are refilled from the posted market whenever the choice changes, then stay editable.
  const choose = (next: { market?: BetMarket; gameId?: string; selection?: BetSelection }): void => {
    const nextMarket = next.market ?? market;
    const nextGameId = next.gameId ?? gameId;
    const nextSelection = next.selection ?? selection;
    setMarket(nextMarket);
    setGameId(nextGameId);
    setSelection(nextSelection);
    setError('');
    if (!STRAIGHT.includes(nextMarket)) {
      setLineText('');
      setOddsText('');
      return;
    }
    const filled = postedMarket(games.find((g) => g.espn_event_id === nextGameId), nextMarket, nextSelection);
    setLineText(filled?.line != null ? String(filled.line) : '');
    setOddsText(filled ? String(filled.odds) : '');
  };

  const selectionChoices: Array<{ value: BetSelection; label: string }> =
    market === 'total'
      ? [{ value: 'over', label: 'Over' }, { value: 'under', label: 'Under' }]
      : [
          { value: 'away', label: game ? game.away_team : 'Away team' },
          { value: 'home', label: game ? game.home_team : 'Home team' },
        ];

  const odds = parseOdds(oddsText);
  const line = parseNumber(lineText);
  const stakeValue = parseNumber(stakeText);
  const stake = stakeValue != null && stakeValue > 0 ? stakeValue : null;
  const needsLine = market === 'spread' || market === 'total';

  const canSubmit =
    stake != null &&
    odds != null &&
    (isStraight ? !!game && (!needsLine || line != null) : description.trim().length >= 3);

  const handleSubmit = async (): Promise<void> => {
    if (!canSubmit || stake == null || odds == null || saving) return;
    setSaving(true);
    setError('');
    try {
      const gameRef = game
        ? { home_team: game.home_team, away_team: game.away_team, game_date: game.game_date }
        : undefined;
      if (isStraightMarket(market) && game) {
        await onTrackBet({
          market,
          nba_game_id: game.espn_event_id,
          selection,
          line: needsLine ? line : null,
          american_odds: odds,
          stake,
          wager_type: wagerType,
        }, gameRef);
      } else {
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
      setError("Couldn't add that bet. Try again.");
    } finally {
      setSaving(false);
    }
  };

  const oddsField = (
    <div>
      <label className={LABEL} htmlFor="addbet-odds">Odds</label>
      <input
        id="addbet-odds"
        type="text"
        placeholder="-110"
        value={oddsText}
        onChange={(e) => setOddsText(e.target.value)}
        className="input input-bordered input-sm w-24"
      />
    </div>
  );

  const summaryText = (): string => {
    if (odds == null) return 'not set';
    const price = formatAmerican(odds);
    return needsLine && line != null ? `${formatLine(line)} at ${price}` : price;
  };

  return (
    <div className="border border-base-300 bg-base-200 p-3 space-y-3">
      <div className="flex flex-wrap gap-3 items-end">
        <div>
          <label className={LABEL} htmlFor="addbet-market">What kind of bet</label>
          <select
            id="addbet-market"
            className="select select-bordered select-sm"
            value={market}
            onChange={(e) => choose({ market: e.target.value as BetMarket, selection: e.target.value === 'total' ? 'over' : 'home' })}
          >
            {(Object.keys(MARKET_LABEL) as BetMarket[]).map((m) => (
              <option key={m} value={m}>{MARKET_LABEL[m]}</option>
            ))}
          </select>
        </div>

        {(isStraight || market === 'prop') && (
          <div>
            <label className={LABEL} htmlFor="addbet-game">
              Which game{market === 'prop' ? ' (optional)' : ''}
            </label>
            <select
              id="addbet-game"
              className="select select-bordered select-sm w-60"
              value={gameId}
              onChange={(e) => choose({ gameId: e.target.value })}
            >
              <option value="">Pick a game</option>
              {games.map((g) => (
                <option key={g.espn_event_id} value={g.espn_event_id}>
                  {g.away_team} at {g.home_team} ({g.game_date})
                </option>
              ))}
            </select>
          </div>
        )}

        {isStraight && (
          <div>
            <label className={LABEL} htmlFor="addbet-selection">Which side</label>
            <select
              id="addbet-selection"
              className="select select-bordered select-sm w-48"
              value={selection}
              onChange={(e) => choose({ selection: e.target.value as BetSelection })}
            >
              {selectionChoices.map((c) => (
                <option key={c.value} value={c.value}>{c.label}</option>
              ))}
            </select>
          </div>
        )}

        {!isStraight && oddsField}

        <div>
          <label className={LABEL} htmlFor="addbet-stake">Stake</label>
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
          <label className={LABEL} htmlFor="addbet-wager">Paid with</label>
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

        <button onClick={() => void handleSubmit()} disabled={!canSubmit || saving} className="btn btn-primary btn-sm">
          {saving ? 'Adding' : 'Add bet'}
        </button>
        <button onClick={onDone} className="btn btn-ghost btn-sm">Cancel</button>
      </div>

      {!isStraight && (
        <div>
          <label className={LABEL} htmlFor="addbet-description">
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
                  ? 'e.g. "Knicks to win + Under 216.5 + Celtics -3"'
                  : 'e.g. "First basket: Wembanyama"'
            }
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="input input-bordered input-sm w-full"
          />
        </div>
      )}

      {isStraight && game && (
        <details key={`${gameId}-${market}-${selection}`} open={!posted} className="text-xs">
          <summary className="cursor-pointer text-muted w-fit">Line and odds: {summaryText()}</summary>
          <div className="flex flex-wrap gap-3 items-end mt-2">
            {needsLine && (
              <div>
                <label className={LABEL} htmlFor="addbet-line">Line</label>
                <input
                  id="addbet-line"
                  type="text"
                  value={lineText}
                  onChange={(e) => setLineText(e.target.value)}
                  className="input input-bordered input-sm w-24"
                />
              </div>
            )}
            {oddsField}
          </div>
          {!posted && (
            <p className="text-warning mt-1">That market isn't posted for this game yet, so enter the line and odds you got.</p>
          )}
        </details>
      )}
      {error && <p className="text-xs text-error">{error}</p>}
    </div>
  );
};
