import { Link } from 'react-router-dom';
import { LEGAL_CONTACT_EMAIL } from './LegalDocument';

export const SiteFooter = (): JSX.Element => (
  <footer className="mt-12 border-t border-base-300 bg-base-200">
    <div className="mx-auto max-w-[1100px] space-y-3 px-4 py-6 text-sm text-muted">
      <nav aria-label="Footer" className="flex flex-wrap gap-x-5 gap-y-1">
        <Link to="/about" className="link link-hover">
          About
        </Link>
        <Link to="/terms" className="link link-hover">
          Terms
        </Link>
        <Link to="/privacy" className="link link-hover">
          Privacy
        </Link>
        <a href={`mailto:${LEGAL_CONTACT_EMAIL}`} className="link link-hover">
          {LEGAL_CONTACT_EMAIL}
        </a>
      </nav>
      <p>Not affiliated with the NBA or any team.</p>
    </div>
  </footer>
);
