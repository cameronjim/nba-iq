import type { Locator, Page } from '@playwright/test';

export class FantasyPage {
  readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  async goto(): Promise<void> {
    await this.page.goto('/fantasy');
  }

  signInPrompt(): Locator {
    return this.page.getByText(/Sign in to use My Team/i);
  }

  lineupSection(): Locator {
    return this.page.getByRole('region', { name: "This week's lineup" });
  }

  streamersSection(): Locator {
    return this.page.getByRole('region', { name: 'Streaming pickups' });
  }

  tradeSection(): Locator {
    return this.page.getByRole('region', { name: 'Check a trade' });
  }
}
