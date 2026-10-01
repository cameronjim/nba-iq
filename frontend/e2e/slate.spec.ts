import { test, expect } from '@playwright/test';
import { mockApi } from './fixtures/apiMock';
import { slateFixture } from './fixtures/slate';
import { ProjectionsPage } from './pages';

const RISER_ID = '1631096';
const STAR_ID = '1628983';

test.describe('Projections sort and reasons', () => {
  test('opens ranked by impact, with the reasons for a row that departs from his usual', async ({ page }) => {
    await mockApi(page);
    const projections = new ProjectionsPage(page);

    await projections.goto();

    await expect(projections.sortButton('Impact')).toHaveAttribute('aria-pressed', 'true');
    await expect(projections.rows.first()).toContainText('Steady Star');
    await expect(projections.reasons(RISER_ID)).toContainText('Role increase');
    await expect(projections.reasons(RISER_ID)).toContainText('Teammate out');
    await expect(projections.vsUsual(RISER_ID)).toContainText('MIN +10.0 · REB +2.2 · AST +1.2');
    await expect(projections.vsUsual(STAR_ID)).toHaveCount(0);
  });

  test('the edge toggle rewires the request and reorders the game', async ({ page }) => {
    const requested: string[] = [];
    await mockApi(page, {
      slate: (params) => {
        requested.push(params.get('sort') ?? 'none');
        return slateFixture(params);
      },
    });
    const projections = new ProjectionsPage(page);
    await projections.goto();
    await expect(projections.rows.first()).toContainText('Steady Star');

    await projections.sortButton('Edge vs usual').click();

    await expect(projections.sortButton('Edge vs usual')).toHaveAttribute('aria-pressed', 'true');
    await expect(projections.rows.first()).toContainText('Bench Riser');
    await expect(projections.orderNote).toContainText('own usual');
    expect(requested).toEqual(['none', 'edge']);
  });

  test('a preseason game carries the badge and the footer says why', async ({ page }) => {
    await mockApi(page);
    const projections = new ProjectionsPage(page);
    await projections.goto();

    await expect(projections.gameCard(/PHX.*@.*GSW/)).toContainText('Preseason');
    await expect(projections.gameCard(/LAL.*@.*OKC/)).not.toContainText('Preseason');
    await expect(projections.preseasonNote).toContainText('trained on regular-season games');
  });

  test('a vs-usual line opens to the evidence behind it', async ({ page }) => {
    await mockApi(page);
    const projections = new ProjectionsPage(page);
    await projections.goto();

    await projections.openVsUsual(RISER_ID);

    await expect(projections.vsUsual(RISER_ID)).toContainText('Minutes: 31.0 projected, usually 21.0 (+10.0)');
    await expect(projections.vsUsual(RISER_ID)).toContainText(
      'Usage freed: Hurt Starter usually plays 33.4 minutes, 5% to play'
    );
  });
});
