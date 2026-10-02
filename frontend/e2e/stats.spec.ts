import { test, expect } from '@playwright/test';
import { StatsPage, NavbarComponent } from './pages';
import { mockApi } from './fixtures/apiMock';
import { ALL_STAR, UNRANKED_ROOKIE } from './fixtures/players';

test.describe('Stats page', () => {
  test('app loads and renders the stats table with seeded players', async ({ page }) => {
    await mockApi(page);
    const stats = new StatsPage(page);

    await stats.goto();

    await expect(new NavbarComponent(page).statsLink()).toBeVisible();
    await expect(stats.playerTable.rowFor(ALL_STAR.name)).toBeVisible();
    await expect(stats.playerTable.rowFor(UNRANKED_ROOKIE.name)).toBeVisible();
  });

  test('shows "–" in the FS column for unranked players (below GP/MPG threshold)', async ({ page }) => {
    await mockApi(page);
    const stats = new StatsPage(page);

    await stats.goto();

    // business rule: players below the GP (<15) or MPG (<12) thresholds
    // have no fantasy_score, and the column shows "-" instead of a
    // misleading rank computed from a tiny sample.
    await expect(stats.playerTable.fsCellFor(UNRANKED_ROOKIE.name)).toHaveText('-');
  });

  test('formats fantasy score to one decimal place for ranked players', async ({ page }) => {
    await mockApi(page);
    const stats = new StatsPage(page);

    await stats.goto();

    await expect(stats.playerTable.fsCellFor(ALL_STAR.name)).toHaveText(
      ALL_STAR.fantasy_score!.toFixed(1)
    );
  });

  test('player search filters the table client-side', async ({ page }) => {
    await mockApi(page);
    const stats = new StatsPage(page);

    await stats.goto();
    await stats.searchPlayers('Allstar');

    await expect(stats.playerTable.rowFor(ALL_STAR.name)).toBeVisible();
    await expect(stats.playerTable.rowFor(UNRANKED_ROOKIE.name)).not.toBeVisible();
  });

  test('clicking a player row opens the PlayerModal and closing it dismisses', async ({ page }) => {
    await mockApi(page);
    const stats = new StatsPage(page);

    await stats.goto();
    await stats.openPlayer(ALL_STAR.name);

    await expect(stats.playerModal.heading()).toHaveText(ALL_STAR.name);

    await stats.playerModal.close();
    await expect(stats.playerModal.root()).not.toBeVisible();
  });

  test('sorting by FS reorders rows', async ({ page }) => {
    await mockApi(page);
    const stats = new StatsPage(page);

    await stats.goto();

    // default sort is PPG desc — clicking FS while it's not the sort key
    // resets to desc, so the highest fantasy_score row should be first.
    await stats.playerTable.clickSort('FS');

    // first td (index 0) is the compare checkbox; td index 1 holds the
    // player name with logo.
    const firstRowName = await stats.playerTable.rows().first().locator('td').nth(1).innerText();
    expect(firstRowName).toContain(ALL_STAR.name);
  });
});

test.describe('Scoreboard placement', () => {
  test('the stats page does not show the live scores strip', async ({ page }) => {
    await mockApi(page);
    const stats = new StatsPage(page);

    await stats.goto();

    await expect(stats.playerTable.rowFor(ALL_STAR.name)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Scroll scores left' })).toHaveCount(0);
  });

  test('the home page keeps the live scores strip', async ({ page }) => {
    await mockApi(page);

    await page.goto('/');

    await expect(page.getByRole('button', { name: 'Scroll scores left' })).toBeVisible();
  });
});
