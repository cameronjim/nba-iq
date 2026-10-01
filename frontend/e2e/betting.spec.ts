import { test, expect } from '@playwright/test';
import { BettingPage } from './pages';
import { mockApi } from './fixtures/apiMock';
import type { BettingGame, BettingPicksResponse } from '../src/types';

const makeGame = (id: string, home: string, away: string): BettingGame => ({
  espn_event_id: id,
  home_team: home,
  away_team: away,
  home_abbrev: home.slice(0, 2).toUpperCase(),
  away_abbrev: away.slice(0, 2).toUpperCase(),
  game_date: '2026-06-10',
  tipoff: '6/10 - 8:30 PM EDT',
  provider: 'Draft Kings',
  markets: {
    spread: { home_line: -2.5, away_line: 2.5, home_price: -105, away_price: -115, home_implied: 0.5122, away_implied: 0.5349 },
    total: { line: 216.5, over_price: -112, under_price: -108, over_implied: 0.5283, under_implied: 0.5192 },
    moneyline: { home: -130, away: 105, home_implied: 0.5652, away_implied: 0.4878 },
  },
});

const ODDS_FIXTURE: BettingGame[] = [
  makeGame('401859966', 'New York Knicks', 'San Antonio Spurs'),
];

const MANY_GAMES: BettingGame[] = [
  makeGame('1', 'New York Knicks', 'San Antonio Spurs'),
  makeGame('2', 'Boston Celtics', 'Miami Heat'),
  makeGame('3', 'Denver Nuggets', 'Phoenix Suns'),
  makeGame('4', 'Dallas Mavericks', 'Houston Rockets'),
  makeGame('5', 'Orlando Magic', 'Atlanta Hawks'),
];

const PICKS_FIXTURE: BettingPicksResponse = {
  picks: [
    {
      game_id: '401859966', category: 'best_value', market: 'spread', selection: 'home',
      matchup: 'San Antonio Spurs @ New York Knicks', game_date: '2026-06-10',
      tipoff: '6/10 - 8:30 PM EDT', selection_label: 'New York Knicks -2.5',
      line: -2.5, american_odds: -105, implied_prob: 0.5122, implied_prob_novig: 0.4891,
      estimated_win_prob: 0.58, estimate_source: 'claude', edge: 0.0678,
      rationale: 'Rest advantage and a top-five defense at home.', confidence: 'medium',
    },
    {
      game_id: '401859966', category: 'safe', market: 'moneyline', selection: 'home',
      matchup: 'San Antonio Spurs @ New York Knicks', game_date: '2026-06-10',
      tipoff: '6/10 - 8:30 PM EDT', selection_label: 'New York Knicks ML (-130)',
      line: null, american_odds: -130, implied_prob: 0.5652, implied_prob_novig: 0.5367,
      estimated_win_prob: 0.62, estimate_source: 'claude', edge: 0.0548,
      rationale: 'Better team straight up.', confidence: 'high',
    },
  ],
  parlay: {
    legs: [
      { game_id: '401859966', market: 'spread', selection: 'home', selection_label: 'New York Knicks -2.5', matchup: 'San Antonio Spurs @ New York Knicks', american_odds: -105 },
      { game_id: '401859967', market: 'total', selection: 'under', selection_label: 'Under 216.5', matchup: 'Boston Celtics @ Miami Heat', american_odds: -108 },
    ],
    combined_american: 271,
    combined_implied_prob: 0.2695,
    rationale: 'Two slow-pace plays.',
    ev_note: 'Parlays multiply the house edge, so treat this as entertainment.',
  },
  summary: 'One strong value play on a thin slate.',
};

const signIn = async (page: import('@playwright/test').Page): Promise<void> => {
  await page.addInitScript(() => {
    window.localStorage.setItem('auth_token', 'fake-token-for-ui-tests');
  });
};

test.describe('Betting page', () => {
  test('signed-out visitors see the odds board, chat, glossary, and a sign-in prompt', async ({ page }) => {
    await mockApi(page, { bettingOdds: ODDS_FIXTURE });

    const betting = new BettingPage(page);
    await betting.goto();

    await expect(betting.disclaimer()).toBeVisible();
    await expect(betting.signInPrompt()).toBeVisible();
    await expect(betting.oddsBoardHeading()).toBeVisible();
    await expect(betting.glossaryHeading()).toBeVisible();
    await expect(betting.chatHeading()).toBeVisible();
    await expect(page.getByText('New York Knicks').first()).toBeVisible();
    await expect(page.getByText('56.5%').first()).toBeVisible();
  });

  test('odds board collapses to three games with a See more toggle', async ({ page }) => {
    await mockApi(page, { bettingOdds: MANY_GAMES });

    const betting = new BettingPage(page);
    await betting.goto();

    await expect(page.getByText('Denver Nuggets')).toBeVisible();
    await expect(page.getByText('Orlando Magic')).not.toBeVisible();

    await betting.seeMoreButton().click();

    await expect(page.getByText('Orlando Magic')).toBeVisible();
    await expect(page.getByRole('button', { name: 'See less' })).toBeVisible();
  });

  test('signed-in users see categorized AI picks and the parlay with its value note', async ({ page }) => {
    await mockApi(page, { bettingOdds: ODDS_FIXTURE, bettingPicks: PICKS_FIXTURE });
    await signIn(page);

    const betting = new BettingPage(page);
    await betting.goto();

    await expect(betting.categoryHeading('Best Value')).toBeVisible();
    await expect(betting.categoryHeading('Safe')).toBeVisible();
    await expect(betting.categoryHeading('Hail Mary')).toBeVisible();
    await expect(page.getByText('New York Knicks ML (-130)')).toBeVisible();
    await expect(page.getByText('medium confidence')).toBeVisible();
    await expect(betting.parlayHeading()).toBeVisible();
    await expect(page.getByText(/Parlays multiply the house edge/i)).toBeVisible();
  });

  test('adding a custom bet posts it and the ledger shows it', async ({ page }) => {
    await mockApi(page, { bettingOdds: ODDS_FIXTURE, bettingPicks: PICKS_FIXTURE });
    await signIn(page);

    // registered after mockApi so this stateful handler takes precedence over the default one.
    const tracked: Array<Record<string, unknown>> = [];
    await page.route('**/api/betting/bets**', (route) => {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON() as Record<string, unknown>;
        tracked.push({
          id: tracked.length + 1,
          nba_game_id: null, home_team: null, away_team: null, game_date: null,
          selection: null, line: null,
          status: 'pending', created_at: '2026-06-09T12:00:00Z', settled_at: null,
          american_odds: null, description: null,
          ...body,
        });
        route.fulfill({ status: 201, json: tracked[tracked.length - 1] });
        return;
      }
      route.fulfill({
        json: {
          bets: tracked,
          summary: { wins: 0, losses: 0, pushes: 0, pending: tracked.length, net: 0 },
        },
      });
    });

    const betting = new BettingPage(page);
    await betting.goto();

    await betting.addBetButton().click();
    await page.getByLabel('Bet type').selectOption('custom');
    await page.getByLabel('Describe the bet').fill('First basket: Wembanyama');
    await page.getByLabel('Odds', { exact: true }).fill('+900');
    await expect(page.getByRole('button', { name: 'Add bet' })).toBeDisabled();
    await page.getByLabel('Stake', { exact: true }).fill('10');
    await page.getByRole('button', { name: 'Add bet' }).click();

    // the row appears instantly (optimistic) and survives the server confirm
    await expect(page.getByRole('cell', { name: 'First basket: Wembanyama', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Won' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Lost' })).toBeVisible();
  });

  test('saving betting preferences re-runs the analysis with refresh=true', async ({ page }) => {
    await mockApi(page, { bettingOdds: ODDS_FIXTURE, bettingPicks: PICKS_FIXTURE });
    await signIn(page);

    const betting = new BettingPage(page);
    await betting.goto();

    await betting.prefsToggle().click();
    await page.getByRole('button', { name: 'Aggressive' }).click();

    const refreshRequest = page.waitForRequest((req) =>
      req.url().includes('/api/betting/picks') && req.url().includes('refresh=true')
    );
    await betting.savePrefsButton().click();
    await refreshRequest;
  });

  test('glossary entries expand with plain-english explanations', async ({ page }) => {
    await mockApi(page, { bettingOdds: ODDS_FIXTURE });

    const betting = new BettingPage(page);
    await betting.goto();

    await betting.glossaryToggle('Parlays').check();

    await expect(page.getByText(/ALL legs must win/i)).toBeVisible();
  });
});
