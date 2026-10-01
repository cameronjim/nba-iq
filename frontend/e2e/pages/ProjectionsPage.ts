import type { Locator, Page } from '@playwright/test';

export class ProjectionsPage {
  constructor(private readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto('/projections');
    await this.page.getByRole('heading', { name: /Projections/ }).waitFor();
  }

  sortButton(label: string): Locator {
    return this.page
      .getByRole('group', { name: 'Sort players by' })
      .getByRole('button', { name: label, exact: true });
  }

  get rows(): Locator {
    return this.page.locator('section.card li');
  }

  row(name: string): Locator {
    return this.rows.filter({ hasText: name });
  }

  reasons(playerId: string): Locator {
    return this.page.getByTestId(`reasons-${playerId}`);
  }

  vsUsual(playerId: string): Locator {
    return this.page.getByTestId(`vs-usual-${playerId}`);
  }

  async openVsUsual(playerId: string): Promise<void> {
    await this.vsUsual(playerId).locator('summary').click();
  }

  get orderNote(): Locator {
    return this.page.getByTestId('slate-order-note');
  }
}
