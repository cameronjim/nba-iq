import { useState } from 'react';
import { formatAmerican, formatPercent, formatLine } from '../../utils/formatOdds';
import { SkeletonTable } from '../Skeleton';
import type { BettingGame } from '../../types';

interface BettingOddsBoardProps {
  games: BettingGame[];
  loading: boolean;
  error: string;
  onRetry: () => void;
}

const VISIBLE_GAMES = 3;

interface OddsCellData {
  text: string;
  implied: number | null;
}

const formatPrice = (price: number | null): string => (price == null ? '-' : formatAmerican(price));

const OddsCell = ({ cell }: { cell: OddsCellData | null }) => (
  <td className="tabular whitespace-nowrap">
    {cell ? (
      <>
        <span>{cell.text}</span>
        {cell.implied != null && (
          <span
            className="text-faint ml-1.5"
            title="Implied probability: the chance the sportsbook's price says this outcome has"
          >
            {formatPercent(cell.implied)}
          </span>
        )}
      </>
    ) : (
      <span className="text-faint">-</span>
    )}
  </td>
);

const GameRows = ({ game }: { game: BettingGame }) => {
  const { spread, total, moneyline } = game.markets;
  const hasMarkets = !!(spread || total || moneyline);

  const awayCells: Array<OddsCellData | null> = [
    spread ? { text: `${formatLine(spread.away_line)} (${formatPrice(spread.away_price)})`, implied: spread.away_implied } : null,
    total ? { text: `O ${total.line} (${formatPrice(total.over_price)})`, implied: total.over_implied } : null,
    moneyline ? { text: formatAmerican(moneyline.away), implied: moneyline.away_implied } : null,
  ];
  const homeCells: Array<OddsCellData | null> = [
    spread ? { text: `${formatLine(spread.home_line)} (${formatPrice(spread.home_price)})`, implied: spread.home_implied } : null,
    total ? { text: `U ${total.line} (${formatPrice(total.under_price)})`, implied: total.under_implied } : null,
    moneyline ? { text: formatAmerican(moneyline.home), implied: moneyline.home_implied } : null,
  ];

  return (
    <>
      <tr className="border-t border-base-300">
        <td rowSpan={2} className="align-top text-xs text-muted whitespace-nowrap">
          <div>{game.tipoff}</div>
          {game.provider && <div className="text-faint">Lines: {game.provider}</div>}
        </td>
        <td className="font-semibold">{game.away_team}</td>
        {hasMarkets ? (
          awayCells.map((cell, i) => <OddsCell key={i} cell={cell} />)
        ) : (
          <td rowSpan={2} colSpan={3} className="text-xs text-muted">Odds not yet posted for this game.</td>
        )}
      </tr>
      <tr>
        <td className="font-semibold">{game.home_team}</td>
        {hasMarkets && homeCells.map((cell, i) => <OddsCell key={i} cell={cell} />)}
      </tr>
    </>
  );
};

export const BettingOddsBoard = ({ games, loading, error, onRetry }: BettingOddsBoardProps) => {
  const [expanded, setExpanded] = useState(false);

  if (loading) {
    return <SkeletonTable rows={6} cols={5} label="Loading upcoming games and odds" />;
  }

  if (error) {
    return (
      <div className="border border-base-300 py-8 flex flex-col items-center gap-3">
        <p className="text-error text-sm">{error}</p>
        <button onClick={onRetry} className="btn btn-primary btn-sm">Try Again</button>
      </div>
    );
  }

  if (games.length === 0) {
    return (
      <div className="border border-base-300 py-8 text-center">
        <p className="font-semibold text-sm">No upcoming games</p>
        <p className="text-xs text-muted">There are no NBA games scheduled in the next few days.</p>
      </div>
    );
  }

  const visible = expanded ? games : games.slice(0, VISIBLE_GAMES);
  const hiddenCount = games.length - VISIBLE_GAMES;

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto">
        <table className="table table-sm">
          <thead>
            <tr>
              <th>Tipoff</th>
              <th>Team</th>
              <th>Spread</th>
              <th>Total</th>
              <th>Moneyline</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((game) => (
              <GameRows key={game.espn_event_id} game={game} />
            ))}
          </tbody>
        </table>
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
