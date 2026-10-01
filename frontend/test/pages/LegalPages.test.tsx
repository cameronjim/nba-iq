import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { TermsPage } from '../../src/pages/TermsPage';
import { PrivacyPage } from '../../src/pages/PrivacyPage';
import { SiteFooter } from '../../src/components/SiteFooter';

const CONTACT = 'cjim02@student.ubc.ca';

function renderInRouter(ui: JSX.Element) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

describe('TermsPage', () => {
  it('renders the title, date, and contact email', () => {
    // arrange + act
    renderInRouter(<TermsPage />);

    // assert
    expect(screen.getByRole('heading', { level: 1, name: 'Terms of Service' })).toBeInTheDocument();
    expect(screen.getByText('Last updated October 1, 2026')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: CONTACT })[0]).toHaveAttribute('href', `mailto:${CONTACT}`);
  });

  it('numbers its sections and includes the gambling help line', () => {
    // arrange + act
    renderInRouter(<TermsPage />);

    // assert
    expect(screen.getByRole('heading', { level: 2, name: '1. About this site' })).toBeInTheDocument();
    expect(screen.getByText(/1-800-GAMBLER/)).toBeInTheDocument();
  });
});

describe('PrivacyPage', () => {
  it('renders the title, date, and contact email', () => {
    // arrange + act
    renderInRouter(<PrivacyPage />);

    // assert
    expect(screen.getByRole('heading', { level: 1, name: 'Privacy Policy' })).toBeInTheDocument();
    expect(screen.getByText('Last updated October 1, 2026')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: CONTACT })[0]).toHaveAttribute('href', `mailto:${CONTACT}`);
  });

  it('names the third parties that handle data', () => {
    // arrange + act
    renderInRouter(<PrivacyPage />);

    // assert
    expect(screen.getByText(/Anthropic receives the data behind the Claude features/)).toBeInTheDocument();
    expect(screen.getByText(/Neon hosts the Postgres database/)).toBeInTheDocument();
  });
});

describe('SiteFooter', () => {
  it('links to the about, terms, and privacy pages', () => {
    // arrange + act
    renderInRouter(<SiteFooter />);

    // assert
    expect(screen.getByRole('link', { name: 'About' })).toHaveAttribute('href', '/about');
    expect(screen.getByRole('link', { name: 'Terms' })).toHaveAttribute('href', '/terms');
    expect(screen.getByRole('link', { name: 'Privacy' })).toHaveAttribute('href', '/privacy');
  });

  it('states the data sources and non-affiliation', () => {
    // arrange + act
    renderInRouter(<SiteFooter />);

    // assert
    expect(screen.getByText(/Stats from NBA\.com, odds from ESPN/)).toBeInTheDocument();
    expect(screen.getByText('Not affiliated with the NBA or any team.')).toBeInTheDocument();
  });
});
