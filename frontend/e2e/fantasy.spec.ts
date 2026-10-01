import { test, expect, type Page } from '@playwright/test';
import { FantasyPage } from './pages';
import { mockApi } from './fixtures/apiMock';
import {
  ROSTER_FIXTURE,
  START_SIT_FIXTURE,
  STREAMERS_FIXTURE,
  TRADE_CHECK_FIXTURE,
} from './fixtures/decisions';

async function signIn(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem('auth_token', 'fake-token-for-ui-tests');
  });
}

test.describe('My Team decisions', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page, {
      roster: ROSTER_FIXTURE,
      startSit: START_SIT_FIXTURE,
      streamers: STREAMERS_FIXTURE,
      tradeCheck: TRADE_CHECK_FIXTURE,
    });
    await signIn(page);
  });

  test("this week's lineup reads the start/sit sentence and switches days", async ({ page }) => {
    const fantasy = new FantasyPage(page);
    await fantasy.goto();

    const lineup = fantasy.lineupSection();
    await expect(lineup.getByTestId('lineup-recommendation')).toHaveText('Start Test Allstar; sit Test Rolepar.');
    await expect(lineup.getByText('Test Allstar · vs BOS · 28 pts, 35 min if he plays · 82% to play')).toBeVisible();
    await expect(lineup.getByText('Test Rolepar · @ LAL · 12 pts, 26 min if he plays · 95% to play')).toBeVisible();

    await lineup.getByRole('tab', { name: /Jan 16/ }).click();
    await expect(lineup.getByTestId('lineup-recommendation')).toHaveText('No one on your roster plays.');
  });

  test('streaming pickups list the sentence and add the player to the roster', async ({ page }) => {
    const fantasy = new FantasyPage(page);
    await fantasy.goto();

    const streamers = fantasy.streamersSection();
    await expect(
      streamers.getByText('Pick up Test Rookie: 4 games this week, about +1.8 expected category wins.')
    ).toBeVisible();

    const addRequest = page.waitForRequest(
      (req) => req.method() === 'POST' && req.url().endsWith('/api/fantasy/roster')
    );
    await streamers.getByRole('button', { name: 'Add Test Rookie' }).click();
    expect((await addRequest).postDataJSON()).toEqual({ player_id: 3 });
    await expect(page.getByText('Added Test Rookie to your team')).toBeVisible();
  });

  test('check a trade sends the picked ids and shows the verdict and category table', async ({ page }) => {
    const fantasy = new FantasyPage(page);
    await fantasy.goto();

    const trade = fantasy.tradeSection();
    const check = trade.getByRole('button', { name: 'Check trade' });
    await expect(check).toBeDisabled();

    await trade.getByRole('button', { name: 'Test Allstar' }).click();
    await trade.getByRole('textbox', { name: 'Search players to get' }).fill('injured');
    await trade.getByRole('button', { name: /Test Injured/ }).click();

    const checkRequest = page.waitForRequest((req) => req.url().endsWith('/api/fantasy/trade-check'));
    await check.click();
    expect((await checkRequest).postDataJSON()).toEqual({ give: [1], get: [4] });

    await expect(trade.getByTestId('trade-verdict')).toHaveText(
      'This trade helps: +0.6 expected category wins, mainly REB and BLK; you lose some 3PM.'
    );
    await expect(trade.getByTestId('trade-note')).toHaveText(
      "illustrative verdict: based on the model's projections and a typical opponent"
    );
    await expect(trade.getByRole('row', { name: /REB/ })).toContainText('+0.31');
  });
});
