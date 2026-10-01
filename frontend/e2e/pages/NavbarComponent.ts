import type { Locator, Page } from '@playwright/test';

export class NavbarComponent {
  readonly page: Page;
  readonly root: Locator;

  constructor(page: Page) {
    this.page = page;
    this.root = page.locator('header.navbar');
  }

  statsLink(): Locator {
    return this.root.getByRole('link', { name: /^Stats$/i });
  }

  signInButton(): Locator {
    return this.root.getByRole('button', { name: /Sign In/i });
  }

  async goToSignIn(): Promise<void> {
    await this.signInButton().click();
  }
}
