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

  oddsBoardHeading(): Locator {
    return this.page.getByRole('heading', { name: /Upcoming Games & Odds/i });
  }

  glossaryHeading(): Locator {
    return this.page.getByRole('heading', { name: /New to betting\? Start here/i });
  }

  // the daisyui collapse hides a checkbox over the title, so clicks must target the checkbox.
  glossaryToggle(term: string): Locator {
    return this.page.getByLabel(`Toggle explanation of ${term}`);
  }

  categoryHeading(name: 'Best Value' | 'Safe' | 'Hail Mary'): Locator {
    return this.page.getByRole('heading', { name, exact: true });
  }

  parlayHeading(): Locator {
    return this.page.getByRole('heading', { name: /Suggested Parlay/i });
  }

  ledgerHeading(): Locator {
    return this.page.getByRole('heading', { name: 'My Bets' });
  }

  addBetButton(): Locator {
    return this.page.getByRole('button', { name: '+ Add bet' });
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
    return this.page.getByRole('button', { name: /See more/ });
  }
}
