import { useState } from 'react';
import { SkeletonTable } from '../Skeleton';
import { AddBetForm } from './AddBetForm';
import { BetsTable } from './BetsTable';
import { ledgerSummarySentence } from '../../utils/bettingCopy';
import type { Bet, BettingGame, BetStatus, LedgerSummary, NewBet, NewBetGameRef } from '../../types';

interface MyBetsProps {
  bets: Bet[];
  summary: LedgerSummary;
  loading: boolean;
  error: string;
  games: BettingGame[];
  onRetry: () => void;
  onTrackBet: (bet: NewBet, gameRef?: NewBetGameRef) => Promise<void>;
  onSettleBet: (id: number, status: BetStatus) => Promise<void>;
  onRemoveBet: (id: number) => Promise<void>;
}

export const MyBets = ({
  bets, summary, loading, error, games, onRetry, onTrackBet, onSettleBet, onRemoveBet,
}: MyBetsProps): JSX.Element => {
  const [adding, setAdding] = useState(false);
  const hasMoney = bets.some((b) => b.stake != null);

  const body = (): JSX.Element => {
    if (loading) return <SkeletonTable rows={4} cols={5} label="Loading bets" />;
    if (error && bets.length === 0) {
      return (
        <p className="text-sm">
          <span className="text-error">Couldn't load your bets.</span>{' '}
          <button onClick={onRetry} className="btn btn-ghost btn-xs">Try again</button>
        </p>
      );
    }
    if (bets.length === 0) {
      return <p className="text-sm text-muted">You haven't tracked any bets yet.</p>;
    }
    return (
      <>
        {error && <p className="text-sm text-error">{error}</p>}
        <p className="text-sm">{ledgerSummarySentence(summary, hasMoney)}</p>
        <BetsTable bets={bets} onSettleBet={onSettleBet} onRemoveBet={onRemoveBet} />
      </>
    );
  };

  return (
    <div className="space-y-3">
      {adding ? (
        <AddBetForm games={games} onTrackBet={onTrackBet} onDone={() => setAdding(false)} />
      ) : (
        <button onClick={() => setAdding(true)} className="btn btn-ghost btn-xs">Add a bet</button>
      )}
      {body()}
    </div>
  );
};
