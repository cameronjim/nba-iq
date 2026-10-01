const ENTRIES = [
  {
    term: 'Moneyline',
    body: 'The simplest bet: pick which team wins the game, no point spread involved. The odds tell you the payout. A favorite at -130 means you risk $130 to win $100; an underdog at +105 means you risk $100 to win $105. Bigger negative number means heavier favorite.',
  },
  {
    term: 'Spread (point spread)',
    body: 'A handicap that levels the matchup. If the Knicks are -2.5, they must win by 3 or more for a Knicks spread bet to cash. The other side, Spurs +2.5, wins if the Spurs win outright OR lose by 1-2 points. Spread bets usually pay close to even money (around -110).',
  },
  {
    term: 'Total (over/under)',
    body: "A bet on the combined final score of both teams, not who wins. If the total is 216.5, the over cashes when the teams combine for 217 or more, the under for 216 or fewer. You're betting on game pace and offense, not the winner.",
  },
  {
    term: 'Implied probability',
    body: 'What the price says about the chances. Odds of -110 imply about a 52.4% chance; +150 implies 40%. Open Prices on any game to see the odds, and each of Claude\'s picks compares its estimate with this number. A bet is only "good value" if the TRUE chance is higher than the implied one. That gap is the edge.',
  },
  {
    term: 'Vig (the house cut)',
    body: "Add up the implied probabilities of both sides of a bet and you'll get more than 100%. That extra is the sportsbook's built-in fee, the vig. It's why betting both sides loses money, and why you need a real edge just to break even long-term.",
  },
  {
    term: 'Parlays',
    body: "A parlay combines multiple bets, and ALL legs must win for it to pay out. Payouts look exciting, but the book's vig compounds across every leg, so the price you get is worse than the true combined odds. They're fun lottery tickets: keep them small and don't make them your main strategy.",
  },
  {
    term: 'Player props',
    body: 'Bets on an individual player rather than the game result, like "Brunson over 28.5 points" or "Wembanyama 3+ blocks". Books are often slower to adjust prop lines than game lines, which is why sharp bettors like them. Track yours in My Bets with the Player prop type.',
  },
  {
    term: 'Push',
    body: "A tie with the line. For example you took -6 and the team won by exactly 6, or the total landed exactly on the number. Your bet is refunded; nobody wins. Half-point lines (-6.5) exist specifically so pushes can't happen.",
  },
];

export const BettingGlossary = () => (
  <section>
    <h2 className="font-display text-xl font-semibold uppercase tracking-wide border-b border-base-300 pb-1 mb-3">
      New to betting? Start here
    </h2>
    <div className="space-y-2">
      {ENTRIES.map((entry) => (
        <div key={entry.term} className="collapse collapse-arrow border border-base-300">
          <input type="checkbox" aria-label={`Toggle explanation of ${entry.term}`} />
          <div className="collapse-title text-sm font-medium min-h-0 py-3">{entry.term}</div>
          <div className="collapse-content">
            <p className="text-sm text-muted leading-relaxed">{entry.body}</p>
          </div>
        </div>
      ))}
    </div>
  </section>
);
