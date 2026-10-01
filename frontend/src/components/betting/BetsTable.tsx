import { IconTrash } from '../icons';
import { formatMoney, formatSignedMoney } from '../../utils/formatOdds';
import { ledgerBetText, resultWord } from '../../utils/bettingCopy';
import type { Bet, BetMarket, BetStatus, WagerType } from '../../types';

interface BetsTableProps {
  bets: Bet[];
  onSettleBet: (id: number, status: BetStatus) => Promise<void>;
  onRemoveBet: (id: number) => Promise<void>;
}

const KIND_LABEL: Partial<Record<BetMarket, string>> = {
  prop: 'Player prop',
  parlay: 'Parlay',
};

const WAGER_NOTE: Partial<Record<WagerType, string>> = {
  bonus_bet: 'Bonus bet',
  odds_boost: 'Odds boost',
};

const RESULT_CLASS: Record<BetStatus, string> = {
  pending: 'text-muted',
  won: 'text-success font-semibold',
  lost: 'text-error font-semibold',
  push: 'font-semibold',
};

// straight bets settle automatically from final scores; the rest are graded by hand.
const AUTO_SETTLED: BetMarket[] = ['spread', 'total', 'moneyline'];

function detailLine(bet: Bet): string {
  const parts = [
    bet.home_team && bet.away_team ? `${bet.away_team} at ${bet.home_team}` : null,
    KIND_LABEL[bet.market] ?? null,
    WAGER_NOTE[bet.wager_type] ?? null,
  ];
  return parts.filter((p): p is string => p !== null).join(' · ');
}

function profitText(bet: Bet): string {
  if (bet.net != null) return formatSignedMoney(bet.net);
  if (bet.status === 'pending' && bet.to_win != null) return `to win ${formatMoney(bet.to_win)}`;
  return '';
}

const profitClass = (net: number | null): string =>
  net != null && net > 0 ? 'text-success' : net != null && net < 0 ? 'text-error' : 'text-muted';

export const BetsTable = ({ bets, onSettleBet, onRemoveBet }: BetsTableProps): JSX.Element => (
  <div className="overflow-x-auto">
    <table className="table table-sm">
      <thead>
        <tr>
          <th>Date</th>
          <th>Bet</th>
          <th>Stake</th>
          <th>Result</th>
          <th>Profit</th>
          <th><span className="sr-only">Manage</span></th>
        </tr>
      </thead>
      <tbody>
        {bets.map((bet) => {
          const text = ledgerBetText(bet);
          const detail = detailLine(bet);
          const manual = !AUTO_SETTLED.includes(bet.market);
          return (
            <tr key={bet.id} className="align-top">
              <td className="whitespace-nowrap text-xs tabular">{(bet.game_date ?? bet.created_at).slice(0, 10)}</td>
              <td className="text-sm max-w-72">
                <div className="font-medium">{text}</div>
                {detail && <div className="text-xs text-muted">{detail}</div>}
              </td>
              <td className="text-xs tabular">{bet.stake != null ? formatMoney(bet.stake) : ''}</td>
              <td className={`text-xs ${RESULT_CLASS[bet.status]}`}>{resultWord(bet.status)}</td>
              <td className={`text-xs tabular whitespace-nowrap ${profitClass(bet.net)}`}>{profitText(bet)}</td>
              <td className="text-xs">
                <details>
                  <summary className="cursor-pointer text-muted w-fit" aria-label={`Manage bet: ${text}`}>Manage</summary>
                  <div className="flex flex-wrap items-center gap-1 mt-1">
                    {bet.status === 'pending' && manual && (
                      <>
                        <button onClick={() => void onSettleBet(bet.id, 'won')} className="btn btn-ghost btn-xs">
                          Mark won
                        </button>
                        <button onClick={() => void onSettleBet(bet.id, 'lost')} className="btn btn-ghost btn-xs">
                          Mark lost
                        </button>
                      </>
                    )}
                    {bet.status === 'pending' && !manual && (
                      <span className="text-muted">Settles from the final score.</span>
                    )}
                    <button
                      onClick={() => void onRemoveBet(bet.id)}
                      className="btn btn-ghost btn-xs gap-1"
                      aria-label={`Delete bet: ${text}`}
                    >
                      <IconTrash size={12} />
                      Delete
                    </button>
                  </div>
                </details>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  </div>
);
