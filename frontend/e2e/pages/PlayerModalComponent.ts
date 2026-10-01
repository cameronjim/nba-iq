import type { Locator, Page } from '@playwright/test';

export class PlayerModalComponent {
  readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  root(): Locator {
    return this.page.locator('.modal.modal-open');
  }

  heading(): Locator {
    return this.root().getByRole('heading', { level: 3 });
  }

  closeButton(): Locator {
    return this.root().getByRole('button', { name: 'Close', exact: true });
  }

  async close(): Promise<void> {
    await this.closeButton().click();
  }
}
