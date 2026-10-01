import { Link } from 'react-router-dom';
import { useBettingPicks } from '../hooks/useBettingPicks';
import { useBetLedger } from '../hooks/useBetLedger';
import { BettingOddsBoard } from '../components/betting/BettingOddsBoard';
import { BettingPicksPanel } from '../components/betting/BettingPicksPanel';
import { BettingPrefsPanel } from '../components/betting/BettingPrefsPanel';
import { BetLedger } from '../components/betting/BetLedger';
import { BettingGlossary } from '../components/betting/BettingGlossary';
import { ChatBox } from '../components/ChatBox';

interface BettingPageProps {
  isLoggedIn: boolean;
}

export const BettingPage = ({ isLoggedIn }: BettingPageProps) => {
  const {
    odds, oddsLoading, oddsError, reloadOdds,
    picks, picksLoading, refreshing, picksError, reloadPicks,
  } = useBettingPicks(isLoggedIn);
  const {
    bets, summary, loading: ledgerLoading, error: ledgerError,
    trackBet, settleBet, removeBet,
  } = useBetLedger(isLoggedIn);

  return (
    <div className="max-w-[1400px] mx-auto px-4 py-6 space-y-6">
      <h1 className="font-display text-3xl font-semibold uppercase tracking-wide">Betting</h1>
      {isLoggedIn ? (
        <>
          <BettingPrefsPanel onSaved={() => void reloadPicks(true)} />
          <BettingPicksPanel
            picks={picks}
            loading={picksLoading}
            refreshing={refreshing}
            error={picksError}
            onReload={(refresh) => void reloadPicks(refresh)}
          />
          <BetLedger
            bets={bets}
            summary={summary}
            loading={ledgerLoading}
            error={ledgerError}
            games={odds}
            onTrackBet={trackBet}
            onSettleBet={settleBet}
            onRemoveBet={removeBet}
          />
        </>
      ) : (
        <div className="border border-base-300 p-4 space-y-1">
          <p className="font-semibold">Sign in to see Claude's betting picks</p>
          <p className="text-sm text-muted max-w-xl">
            Signed-in users get Best Value, Safe, and Hail Mary picks based on their preferences, a suggested
            parlay, and a bet tracker. The odds board below is free to browse.
          </p>
        </div>
      )}

      <p className="text-xs text-muted">
        Picks are informational, not betting advice. 21+ where legal. Problem gambling help: 1-800-GAMBLER.{' '}
        <Link to="/terms" className="link">Terms</Link>
      </p>

      <section>
        <h2 className="font-display text-xl font-semibold uppercase tracking-wide border-b border-base-300 pb-1 mb-3">
          Upcoming Games & Odds
        </h2>
        <BettingOddsBoard games={odds} loading={oddsLoading} error={oddsError} onRetry={reloadOdds} />
      </section>

      <ChatBox
        contextType="betting"
        isLoggedIn={isLoggedIn}
        emptyHint="Ask about tonight's lines, a specific matchup, or betting strategy."
      />

      <BettingGlossary />
    </div>
  );
};
