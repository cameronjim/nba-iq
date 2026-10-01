import { useState } from 'react';
import { formatAmerican, formatPercent, formatLine } from '../../utils/formatOdds';
import type { BettingGame } from '../../types';

interface BettingOddsBoardProps {
  games: BettingGame[];
  loading: boolean;
  error: string;
  onRetry: () => void;
}

const VISIBLE_GAMES = 3;

interface OddsRowProps {
  label: string;
  cells: Array<{ text: string; implied: number | null } | null>;
}

const formatPrice = (price: number | null): string => (price == null ? '-' : formatAmerican(price));

const OddsRow = ({ label, cells }: OddsRowProps) => (
  <div className="grid grid-cols-[4.5rem_1fr_1fr] gap-2 items-center text-xs">
    <span className="opacity-50 font-medium">{label}</span>
    {cells.map((cell, i) =>
      cell ? (
        <span key={i} className="flex items-center gap-1.5">
          <span className="font-medium">{cell.text}</span>
          {cell.implied != null && (
            <span className="badge badge-ghost badge-xs whitespace-nowrap" title="Implied probability: the chance the sportsbook's price says this outcome has">
              {formatPercent(cell.implied)}
            </span>
          )}
        </span>
      ) : (
        <span key={i} className="opacity-30">-</span>
      )
    )}
  </div>
);

const GameCard = ({ game }: { game: BettingGame }) => {
  const { spread, total, moneyline } = game.markets;
  const hasMarkets = !!(spread || total || moneyline);

  return (
    <div className="card bg-base-200">
      <div className="card-body p-4 gap-2">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="font-semibold text-sm">{game.away_team}</div>
            <div className="text-xs opacity-50">at</div>
            <div className="font-semibold text-sm">{game.home_team}</div>
          </div>
          <span className="text-xs opacity-50 whitespace-nowrap">{game.tipoff}</span>
        </div>

        {hasMarkets ? (
          <div className="space-y-1.5 mt-1">
            <div className="grid grid-cols-[4.5rem_1fr_1fr] gap-2 text-[10px] uppercase tracking-wide opacity-40">
              <span />
              <span>{game.away_abbrev || 'Away'}</span>
              <span>{game.home_abbrev || 'Home'}</span>
            </div>
            <OddsRow
              label="Spread"
              cells={
                spread
                  ? [
                      { text: `${formatLine(spread.away_line)} (${formatPrice(spread.away_price)})`, implied: spread.away_implied },
                      { text: `${formatLine(spread.home_line)} (${formatPrice(spread.home_price)})`, implied: spread.home_implied },
                    ]
                  : [null, null]
              }
            />
            <OddsRow
              label="Total"
              cells={
                total
                  ? [
                      { text: `O ${total.line} (${formatPrice(total.over_price)})`, implied: total.over_implied },
                      { text: `U ${total.line} (${formatPrice(total.under_price)})`, implied: total.under_implied },
                    ]
                  : [null, null]
              }
            />
            <OddsRow
              label="Moneyline"
              cells={
                moneyline
                  ? [
                      { text: formatAmerican(moneyline.away), implied: moneyline.away_implied },
                      { text: formatAmerican(moneyline.home), implied: moneyline.home_implied },
                    ]
                  : [null, null]
              }
            />
            {game.provider && (
              <p className="text-[10px] opacity-30 pt-1">Lines: {game.provider}</p>
            )}
          </div>
        ) : (
          <p className="text-xs opacity-40 py-2">Odds not yet posted for this game.</p>
        )}
      </div>
    </div>
  );
};

export const BettingOddsBoard = ({ games, loading, error, onRetry }: BettingOddsBoardProps) => {
  const [expanded, setExpanded] = useState(false);

  if (loading) {
    return (
      <div className="card bg-base-200">
        <div className="card-body flex flex-col items-center py-12 gap-3">
          <span className="loading loading-spinner loading-lg" />
          <p className="text-sm opacity-50">Loading upcoming games and odds...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="card bg-base-200">
        <div className="card-body flex flex-col items-center py-12 gap-4">
          <p className="text-error text-sm">{error}</p>
          <button onClick={onRetry} className="btn btn-primary btn-sm">Try Again</button>
        </div>
      </div>
    );
  }

  if (games.length === 0) {
    return (
      <div className="card bg-base-200">
        <div className="card-body flex flex-col items-center py-12 gap-2 text-center">
          <p className="font-semibold text-sm">No upcoming games</p>
          <p className="text-xs opacity-60">There are no NBA games scheduled in the next few days.</p>
        </div>
      </div>
    );
  }

  const visible = expanded ? games : games.slice(0, VISIBLE_GAMES);
  const hiddenCount = games.length - VISIBLE_GAMES;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {visible.map((game) => (
          <GameCard key={game.espn_event_id} game={game} />
        ))}
      </div>
      {hiddenCount > 0 && (
        <div className="flex justify-center">
          <button onClick={() => setExpanded(!expanded)} className="btn btn-ghost btn-sm">
            {expanded ? 'See less' : `See more (${hiddenCount})`}
          </button>
        </div>
      )}
    </div>
  );
};
