import type { Locator, Page } from '@playwright/test';

export class LoginPage {
  readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  async goto(): Promise<void> {
    await this.page.goto('/login');
  }

  usernameInput(): Locator {
    return this.page.getByLabel('Username or email');
  }
}
