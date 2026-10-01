import { formatLine } from '../../utils/formatOdds';
import { gameSentence, priceText } from '../../utils/bettingCopy';
import type { BettingGame } from '../../types';

interface GameRowProps {
  game: BettingGame;
}

const withPrice = (label: string, price: number | null): string => `${label} (${priceText(price)})`;

export const GameRow = ({ game }: GameRowProps): JSX.Element => {
  const { spread, total, moneyline } = game.markets;
  const rows: Array<{ market: string; away: string; home: string }> = [
    {
      market: 'Spread',
      away: spread ? withPrice(formatLine(spread.away_line), spread.away_price) : 'not posted',
      home: spread ? withPrice(formatLine(spread.home_line), spread.home_price) : 'not posted',
    },
    {
      market: 'Total',
      away: total ? withPrice(`Over ${total.line}`, total.over_price) : 'not posted',
      home: total ? withPrice(`Under ${total.line}`, total.under_price) : 'not posted',
    },
    {
      market: 'To win',
      away: moneyline ? priceText(moneyline.away) : 'not posted',
      home: moneyline ? priceText(moneyline.home) : 'not posted',
    },
  ];

  return (
    <li className="py-2 border-t border-base-300 first:border-t-0">
      <p className="text-sm">{gameSentence(game)}</p>
      <details className="text-xs mt-1">
        <summary className="cursor-pointer text-muted w-fit">Prices</summary>
        <div className="overflow-x-auto mt-1">
          <table className="table table-xs w-auto">
            <thead>
              <tr>
                <th />
                <th>{game.away_team}</th>
                <th>{game.home_team}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.market}>
                  <td className="text-muted">{row.market}</td>
                  <td className="tabular whitespace-nowrap">{row.away}</td>
                  <td className="tabular whitespace-nowrap">{row.home}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-faint mt-1">
          {game.provider ? `Prices from ${game.provider}.` : 'Sportsbook not named.'}
        </p>
      </details>
    </li>
  );
};
