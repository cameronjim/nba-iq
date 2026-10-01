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
  makeGame('5', 'Chicago Bulls', 'Toronto Raptors'),
  makeGame('6', 'Utah Jazz', 'Portland Trail Blazers'),
  makeGame('7', 'Orlando Magic', 'Atlanta Hawks'),
];

const KNICKS_GAME = 'San Antonio Spurs at New York Knicks · 6/10 - 8:30 PM EDT · New York Knicks favored by 2.5 · total 216.5';

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
  test('signed-out visitors see games in plain words, chat, glossary, and a sign-in prompt', async ({ page }) => {
    await mockApi(page, { bettingOdds: ODDS_FIXTURE });

    const betting = new BettingPage(page);
    await betting.goto();

    await expect(betting.disclaimer()).toBeVisible();
    await expect(betting.signInPrompt()).toBeVisible();
    await expect(betting.gamesHeading()).toBeVisible();
    await expect(betting.picksHeading()).toBeVisible();
    await expect(betting.betsHeading()).toHaveCount(0);
    await expect(betting.glossaryHeading()).toBeVisible();
    await expect(betting.chatHeading()).toBeVisible();
    await expect(page.getByText(KNICKS_GAME)).toBeVisible();
    await expect(page.getByText('56.5%')).toHaveCount(0);
  });

  test('a game row reveals American prices and the book under Prices', async ({ page }) => {
    await mockApi(page, { bettingOdds: ODDS_FIXTURE });

    const betting = new BettingPage(page);
    await betting.goto();

    const row = betting.gameRow(KNICKS_GAME);
    await expect(row.getByText('-2.5 (-105)')).not.toBeVisible();

    await betting.pricesToggle(row).click();

    await expect(row.getByText('-2.5 (-105)')).toBeVisible();
    await expect(row.getByText('Under 216.5 (-108)')).toBeVisible();
    await expect(row.getByText('-130')).toBeVisible();
    await expect(row.getByText('Prices from Draft Kings.')).toBeVisible();
  });

  test('the games list collapses long slates with a See more toggle', async ({ page }) => {
    await mockApi(page, { bettingOdds: MANY_GAMES });

    const betting = new BettingPage(page);
    await betting.goto();

    await expect(page.getByText(/^Portland Trail Blazers at Utah Jazz/)).toBeVisible();
    await expect(page.getByText(/^Atlanta Hawks at Orlando Magic/)).toHaveCount(0);

    await betting.seeMoreButton().click();

    await expect(page.getByText(/^Atlanta Hawks at Orlando Magic/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'See less' })).toBeVisible();
  });

  test("signed-in users see Claude's picks in plain words and a folded parlay idea", async ({ page }) => {
    await mockApi(page, { bettingOdds: ODDS_FIXTURE, bettingPicks: PICKS_FIXTURE });
    await signIn(page);

    const betting = new BettingPage(page);
    await betting.goto();

    await expect(page.getByText(/win chances below are Claude's estimates, not the model's/)).toBeVisible();
    await expect(page.getByText('New York Knicks -2.5 (-105)', { exact: true })).toBeVisible();
    await expect(page.getByText(/Best value · Medium confidence/)).toBeVisible();
    await expect(page.getByText('New York Knicks to win (-130)')).toBeVisible();
    await expect(page.getByText(/Safer · High confidence/)).toBeVisible();
    await expect(
      page.getByText('Claude thinks this hits about 58% of the time; the price implies 49%.')
    ).toBeVisible();

    const leg = page.getByText('Under 216.5 (-108), Boston Celtics at Miami Heat.');
    await expect(leg).not.toBeVisible();
    await betting.parlayToggle().click();
    await expect(leg).toBeVisible();
    await expect(page.getByText(/Parlays multiply the house edge/i)).toBeVisible();
  });

  test('adding a straight bet prefills the line and odds from the posted market', async ({ page }) => {
    await mockApi(page, { bettingOdds: ODDS_FIXTURE, bettingPicks: PICKS_FIXTURE });
    await signIn(page);

    const posted: Array<Record<string, unknown>> = [];
    await page.route('**/api/betting/bets**', (route) => {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON() as Record<string, unknown>;
        posted.push(body);
        route.fulfill({
          status: 201,
          json: {
            id: 1, nba_game_id: '401859966', home_team: 'New York Knicks', away_team: 'San Antonio Spurs',
            game_date: '2026-06-10', description: null, status: 'pending', created_at: '2026-06-09T12:00:00Z',
            settled_at: null, net: null, wager_type: 'cash', ...body,
          },
        });
        return;
      }
      route.fulfill({ json: { bets: [], summary: { wins: 0, losses: 0, pushes: 0, pending: 0, net: 0 } } });
    });

    const betting = new BettingPage(page);
    await betting.goto();

    await betting.addBetButton().click();
    await page.getByLabel('Which game').selectOption('401859966');
    await page.getByLabel('Which side').selectOption('home');
    await expect(page.getByText('Line and odds: -2.5 at -105')).toBeVisible();
    await page.getByLabel('Stake', { exact: true }).fill('20');
    await page.getByRole('button', { name: 'Add bet' }).click();

    await expect(page.getByRole('cell', { name: /New York Knicks -2\.5 \(-105\)/ })).toBeVisible();
    expect(posted[0]).toMatchObject({ market: 'spread', selection: 'home', line: -2.5, american_odds: -105, stake: 20 });
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
    await page.getByLabel('What kind of bet').selectOption('custom');
    await page.getByLabel('Describe the bet').fill('First basket: Wembanyama');
    await page.getByLabel('Odds', { exact: true }).fill('+900');
    await expect(page.getByRole('button', { name: 'Add bet' })).toBeDisabled();
    await page.getByLabel('Stake', { exact: true }).fill('10');
    await page.getByRole('button', { name: 'Add bet' }).click();

    // the row appears instantly (optimistic) and survives the server confirm
    await expect(page.getByRole('cell', { name: 'First basket: Wembanyama (+900)', exact: true })).toBeVisible();
    await expect(page.getByText('Pending', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Mark won' })).not.toBeVisible();

    await betting.manageToggle('First basket: Wembanyama (+900)').click();

    await expect(page.getByRole('button', { name: 'Mark won' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Mark lost' })).toBeVisible();
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
