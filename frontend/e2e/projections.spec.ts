import { test, expect } from '@playwright/test';
import { mockApi } from './fixtures/apiMock';
import { slateFixture, uncoveredSlateFixture } from './fixtures/slate';
import { watchlistFixture } from './fixtures/watchlist';
import { ProjectionsPage } from './pages';

const RISER_ID = '1631096';
const STAR_ID = '1628983';
const GUARD_ID = '1629630';

test.describe('Projections, tonight', () => {
  test('opens on tonight with one plain line per player and no machinery', async ({ page }) => {
    await mockApi(page);
    const projections = new ProjectionsPage(page);

    await projections.goto();

    await expect(projections.scopeButton('Tonight')).toHaveAttribute('aria-pressed', 'true');
    await expect(projections.line(STAR_ID)).toHaveText(
      'Steady Star · OKC vs LAL · 33 pts, 35 min if he plays · 95% to play'
    );
    await expect(projections.note(RISER_ID)).toHaveText('Up from his usual 21 min: Hurt Starter is out.');
    await expect(projections.note(STAR_ID)).toHaveCount(0);
    await expect(page.getByText('Role increase')).toHaveCount(0);
    await expect(page.getByRole('group', { name: 'Sort players by' })).toHaveCount(0);
    await expect(projections.dateInput).toBeVisible();
  });

  test('a Details disclosure opens to the category line and the evidence', async ({ page }) => {
    await mockApi(page);
    const projections = new ProjectionsPage(page);
    await projections.goto();

    await projections.openDetails(RISER_ID);

    await expect(projections.details(RISER_ID)).toContainText('If he plays:');
    await expect(projections.details(RISER_ID)).toContainText('14.8 points averaged over the chance he sits.');
    await expect(projections.details(RISER_ID)).toContainText('Minutes: 31.0 projected, usually 21.0 (+10.0)');
  });

  test('a preseason game is tagged in plain text, and the footer has the times', async ({ page }) => {
    await mockApi(page);
    const projections = new ProjectionsPage(page);
    await projections.goto();

    await expect(projections.game(/PHX.*@.*GSW/)).toContainText('Preseason');
    await expect(projections.game(/LAL.*@.*OKC/)).not.toContainText('Preseason');
    await expect(projections.footer).toContainText('Published Feb 4');
    await expect(projections.footer).toContainText('Injuries as of Feb 4');
    await expect(projections.footer).not.toContainText('v1-decomposed');
  });

  test('a date past the latest run says so and shows the games as schedule only', async ({ page }) => {
    await mockApi(page, { slate: uncoveredSlateFixture });
    const projections = new ProjectionsPage(page);
    await projections.goto();

    await expect(projections.coverageNotice).toContainText(
      'No projections for Tue, Oct 20 yet. The latest run covers Oct 1 to Oct 7'
    );
    await expect(projections.scheduleOnly(/LAL.*@.*OKC/)).toHaveText('Projections not published yet.');
  });

  test('tonight asks for the default order and never sends a sort', async ({ page }) => {
    const sorts: Array<string | null> = [];
    await mockApi(page, {
      slate: (params) => {
        sorts.push(params.get('sort'));
        return slateFixture(params);
      },
    });
    const projections = new ProjectionsPage(page);

    await projections.goto();
    await expect(projections.line(STAR_ID)).toBeVisible();

    expect(sorts).toEqual([null]);
  });
});

test.describe('Projections, next 7 days', () => {
  test('switching scope asks for a week and ranks the busy guard first', async ({ page }) => {
    const days: string[] = [];
    await mockApi(page, {
      watchlist: (params) => {
        days.push(params.get('days') ?? 'none');
        return watchlistFixture(params);
      },
    });
    const projections = new ProjectionsPage(page);
    await projections.goto();

    await projections.scopeButton('Next 7 days').click();

    await expect(page).toHaveURL(/\/projections\?scope=week$/);
    await expect(projections.weekRows.first()).toContainText('Windowed Guard');
    await expect(projections.line(GUARD_ID)).toContainText('MEM, 4 games');
    await expect(projections.dateInput).toHaveCount(0);
    expect(days).toEqual(['7']);
  });

  test('a row opens to every game in the window', async ({ page }) => {
    await mockApi(page);
    const projections = new ProjectionsPage(page);
    await projections.goto('/projections?scope=week');

    await projections.openDetails(GUARD_ID);

    await expect(projections.breakdown(GUARD_ID).locator('tbody tr')).toHaveCount(4);
    await expect(projections.breakdown(GUARD_ID)).toContainText('SAC');
  });

  test('the team select filters in the browser', async ({ page }) => {
    await mockApi(page);
    const projections = new ProjectionsPage(page);
    await projections.goto('/projections?scope=week');
    await expect(projections.weekRows).toHaveCount(2);

    await projections.teamSelect.selectOption('BOS');

    await expect(projections.weekRows).toHaveCount(1);
    await expect(projections.weekRows.first()).toContainText('Rested Forward');
  });

  test('the old watchlist address lands on the next 7 days', async ({ page }) => {
    await mockApi(page);
    const projections = new ProjectionsPage(page);

    await projections.goto('/watchlist');

    await expect(page).toHaveURL(/\/projections\?scope=week$/);
    await expect(projections.scopeButton('Next 7 days')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Watchlist' })).toHaveCount(0);
  });
});
