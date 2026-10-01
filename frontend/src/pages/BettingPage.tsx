import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useBettingPicks } from '../hooks/useBettingPicks';
import { useBetLedger } from '../hooks/useBetLedger';
import { GamesList } from '../components/betting/GamesList';
import { PicksList } from '../components/betting/PicksList';
import { ModelPropsSection } from '../components/betting/ModelPropsSection';
import { MyBets } from '../components/betting/MyBets';
import { BettingPrefsPanel } from '../components/betting/BettingPrefsPanel';
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
  const {
    odds, oddsLoading, oddsError, reloadOdds,
    picks, picksLoading, refreshing, picksError, reloadPicks,
  } = useBettingPicks(isLoggedIn);
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

      <Section title="Picks">
        <p className="text-xs text-muted">
          The win chances below are Claude's estimates, not the model's. Model-based player props will appear
          here when available.
        </p>
        {isLoggedIn ? (
          <>
            <BettingPrefsPanel onSaved={() => void reloadPicks(true)} />
            <PicksList
              picks={picks}
              loading={picksLoading}
              refreshing={refreshing}
              error={picksError}
              onReload={(refresh) => void reloadPicks(refresh)}
            />
          </>
        ) : (
          <p className="text-sm">Sign in to see Claude's betting picks and track your own bets.</p>
        )}
        <ModelPropsSection />
      </Section>

      {isLoggedIn && (
        <Section title="My bets">
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
        </Section>
      )}

      <ChatBox
        contextType="betting"
        isLoggedIn={isLoggedIn}
        emptyHint="Ask about tonight's lines, a specific matchup, or betting strategy."
      />

      <BettingGlossary />
    </div>
  );
};
