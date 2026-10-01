import type { Locator, Page } from '@playwright/test';

export class BettingPage {
  readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  async goto(): Promise<void> {
    await this.page.goto('/betting');
  }

  disclaimer(): Locator {
    return this.page.getByText(/1-800-GAMBLER/);
  }

  signInPrompt(): Locator {
    return this.page.getByText(/Sign in to see Claude's betting picks/i);
  }

  gamesHeading(): Locator {
    return this.page.getByRole('heading', { name: "Tonight's games" });
  }

  picksHeading(): Locator {
    return this.page.getByRole('heading', { name: 'Picks', exact: true });
  }

  betsHeading(): Locator {
    return this.page.getByRole('heading', { name: 'My bets' });
  }

  glossaryHeading(): Locator {
    return this.page.getByRole('heading', { name: /New to betting\? Start here/i });
  }

  // the daisyui collapse hides a checkbox over the title, so clicks must target the checkbox.
  glossaryToggle(term: string): Locator {
    return this.page.getByLabel(`Toggle explanation of ${term}`);
  }

  gameRow(sentence: string): Locator {
    return this.page.getByRole('listitem').filter({ hasText: sentence });
  }

  pricesToggle(row: Locator): Locator {
    return row.getByText('Prices', { exact: true });
  }

  parlayToggle(): Locator {
    return this.page.getByText("Claude's parlay idea", { exact: true });
  }

  addBetButton(): Locator {
    return this.page.getByRole('button', { name: 'Add a bet' });
  }

  manageToggle(betText: string): Locator {
    return this.page.getByLabel(`Manage bet: ${betText}`);
  }

  prefsToggle(): Locator {
    return this.page.getByRole('button', { name: /Betting Preferences/i });
  }

  savePrefsButton(): Locator {
    return this.page.getByRole('button', { name: /Save & Re-analyze/i });
  }

  chatHeading(): Locator {
    return this.page.getByText('Ask Claude', { exact: true });
  }

  seeMoreButton(): Locator {
    return this.page.getByRole('button', { name: /^See \d+ more$/ });
  }
}
