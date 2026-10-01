import type { Locator, Page } from '@playwright/test';

export class ProjectionsPage {
  constructor(private readonly page: Page) {}

  async goto(path = '/projections'): Promise<void> {
    await this.page.goto(path);
    await this.page.getByRole('heading', { name: 'Projections', exact: true }).waitFor();
  }

  scopeButton(label: 'Tonight' | 'Next 7 days'): Locator {
    return this.page
      .getByRole('group', { name: 'Show projections for' })
      .getByRole('button', { name: label, exact: true });
  }

  get dateInput(): Locator {
    return this.page.getByLabel('Game date');
  }

  get teamSelect(): Locator {
    return this.page.getByRole('combobox', { name: 'Filter by team' });
  }

  get positionSelect(): Locator {
    return this.page.getByRole('combobox', { name: 'Filter by position' });
  }

  line(playerId: string): Locator {
    return this.page.getByTestId(`line-${playerId}`);
  }

  note(playerId: string): Locator {
    return this.page.getByTestId(`note-${playerId}`);
  }

  details(playerId: string): Locator {
    return this.page.getByTestId(`details-${playerId}`);
  }

  async openDetails(playerId: string): Promise<void> {
    await this.details(playerId).getByText('Details', { exact: true }).click();
  }

  breakdown(playerId: string): Locator {
    return this.page.getByTestId(`games-${playerId}`);
  }

  get weekRows(): Locator {
    return this.page.getByTestId('week-list').getByTestId(/^row-/);
  }

  game(matchup: RegExp): Locator {
    return this.page
      .locator('section')
      .filter({ has: this.page.getByRole('heading', { name: matchup }) });
  }

  get coverageNotice(): Locator {
    return this.page.getByTestId('coverage-notice');
  }

  scheduleOnly(matchup: RegExp): Locator {
    return this.game(matchup).getByTestId('schedule-only');
  }

  get footer(): Locator {
    return this.page.getByTestId('projections-footer');
  }
}
