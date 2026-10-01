import type { ReactNode } from 'react';

export const LEGAL_CONTACT_EMAIL = 'cjim02@student.ubc.ca';
export const LEGAL_LAST_UPDATED = 'October 1, 2026';

export interface LegalSection {
  heading: string;
  body: ReactNode;
}

interface LegalDocumentProps {
  title: string;
  sections: LegalSection[];
}

export const ContactLink = (): JSX.Element => (
  <a href={`mailto:${LEGAL_CONTACT_EMAIL}`} className="link">
    {LEGAL_CONTACT_EMAIL}
  </a>
);

export const LegalDocument = ({ title, sections }: LegalDocumentProps): JSX.Element => (
  <article className="mx-auto max-w-prose px-4 py-8">
    <h1 className="font-display text-3xl font-semibold uppercase tracking-wide">{title}</h1>
    <p className="mt-1 text-sm text-muted">Last updated {LEGAL_LAST_UPDATED}</p>
    {sections.map((section, i) => (
      <section key={section.heading} className="mt-8">
        <h2 className="mb-2 font-display text-xl font-semibold uppercase tracking-wide">
          {i + 1}. {section.heading}
        </h2>
        <div className="space-y-3 leading-relaxed">{section.body}</div>
      </section>
    ))}
  </article>
);
