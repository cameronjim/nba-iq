import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useBettingOdds } from '../hooks/useBettingOdds';
import { useBetLedger } from '../hooks/useBetLedger';
import { GamesList } from '../components/betting/GamesList';
import { ModelPropsSection } from '../components/betting/ModelPropsSection';
import { MyBets } from '../components/betting/MyBets';
import { BettingGlossary } from '../components/betting/BettingGlossary';
import { ChatBox } from '../components/ChatBox';

interface BettingPageProps {
  isLoggedIn: boolean;
}

const Section = ({ title, children }: { title: string; children: ReactNode }): JSX.Element => (
  <section className="space-y-3">
    <h2 className="font-display text-xl font-semibold uppercase tracking-wide border-b border-base-300 pb-1">
      {title}
    </h2>
    {children}
  </section>
);

export const BettingPage = ({ isLoggedIn }: BettingPageProps) => {
  const { odds, oddsLoading, oddsError, reloadOdds } = useBettingOdds();
  const {
    bets, summary, loading: ledgerLoading, error: ledgerError,
    trackBet, settleBet, removeBet, reload: reloadLedger,
  } = useBetLedger(isLoggedIn);

  return (
    <div className="max-w-[1100px] mx-auto px-4 py-6 space-y-8">
      <div className="space-y-1">
        <h1 className="font-display text-3xl font-semibold uppercase tracking-wide">Betting</h1>
        <p className="text-xs text-muted">
          Picks are informational, not betting advice. 21+ where legal. Problem gambling help: 1-800-GAMBLER.{' '}
          <Link to="/terms" className="link">Terms</Link>
        </p>
      </div>

      <Section title="Tonight's games">
        <GamesList games={odds} loading={oddsLoading} error={oddsError} onRetry={reloadOdds} />
      </Section>

      <Section title="Prop picks">
        <p className="text-xs text-muted">Probabilities come from the projection model, not Claude.</p>
        <ModelPropsSection />
      </Section>

      <Section title="My bets">
        {isLoggedIn ? (
          <MyBets
            bets={bets}
            summary={summary}
            loading={ledgerLoading}
            error={ledgerError}
            games={odds}
            onRetry={() => void reloadLedger()}
            onTrackBet={trackBet}
            onSettleBet={settleBet}
            onRemoveBet={removeBet}
          />
        ) : (
          <p className="text-sm">Sign in to track your bets.</p>
        )}
      </Section>

      <ChatBox
        contextType="betting"
        isLoggedIn={isLoggedIn}
        emptyHint="Ask about tonight's lines, a specific matchup, or betting strategy."
      />

      <BettingGlossary />
    </div>
  );
};
