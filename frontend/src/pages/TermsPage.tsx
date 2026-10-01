import { Link } from 'react-router-dom';
import { ContactLink, LegalDocument, type LegalSection } from '../components/LegalDocument';

const sections: LegalSection[] = [
  {
    heading: 'About this site',
    body: (
      <p>
        NBA IQ is a hobby project run by one person. It shows NBA stats, model projections, a fantasy roster tool,
        betting odds, and a bet ledger. By using the site you agree to these terms. If you do not agree, do not use
        the site.
      </p>
    ),
  },
  {
    heading: 'No warranty',
    body: (
      <p>
        The site is provided as is, with no warranty of any kind. It can be down, slow, or wrong, and I can change or
        remove any part of it at any time. I am not liable for any loss that comes from using it, including losses
        from decisions you make about fantasy teams or bets.
      </p>
    ),
  },
  {
    heading: 'Data can be wrong or late',
    body: (
      <p>
        Stats, schedules, injury reports, odds, and 2K ratings come from third-party sources and are copied on a
        schedule, so they can lag the source or contain errors. A score, injury status, or line you see here may
        already have changed. Check the original source before you rely on it.
      </p>
    ),
  },
  {
    heading: 'Projections and AI output',
    body: (
      <p>
        Projections come from a statistical model. Team analysis, waiver suggestions, betting picks, and chat replies
        are written by Claude, an AI model from Anthropic, using the data and preferences you provide. Both can be
        wrong. Treat them as one input among many.
      </p>
    ),
  },
  {
    heading: 'Betting is informational',
    body: (
      <>
        <p>
          The betting section shows publicly posted odds and lets you keep a private record of bets you placed
          elsewhere. It does not take bets or hold money, and nothing on it is betting, financial, or legal advice.
        </p>
        <p>
          You must be of legal gambling age where you live, and you are responsible for following your local laws.
          If gambling is causing you harm, call 1-800-GAMBLER in the United States or contact a local problem
          gambling service.
        </p>
      </>
    ),
  },
  {
    heading: 'Accounts',
    body: (
      <p>
        You can browse most of the site without an account. An account is for one person. Give accurate sign-in
        details, keep your password private, and tell me if you think someone else has access. You are responsible
        for what happens under your account.
      </p>
    ),
  },
  {
    heading: 'Acceptable use',
    body: (
      <p>
        Do not scrape the site at a rate that affects other users, try to get around rate limits or sign-in, probe
        for security holes, or use the Claude features for anything unrelated to basketball. I may block requests
        that do.
      </p>
    ),
  },
  {
    heading: 'Third-party names and data',
    body: (
      <p>
        NBA IQ is not affiliated with or endorsed by the NBA, any team, ESPN, CBS Sports, 2K Sports, Take-Two, or
        Anthropic. Team names, logos, and player images belong to their owners and appear here to identify the teams
        and players in the data.
      </p>
    ),
  },
  {
    heading: 'Ending your account',
    body: (
      <p>
        You can stop using the site at any time. To have your account and its data deleted, email <ContactLink />{' '}
        from the address on the account. I can suspend or remove an account that breaks these terms. See the{' '}
        <Link to="/privacy" className="link">
          privacy policy
        </Link>{' '}
        for what is stored.
      </p>
    ),
  },
  {
    heading: 'Changes to these terms',
    body: (
      <p>
        I may update these terms. The date at the top shows the latest revision. If you keep using the site after a
        change, you accept the new version.
      </p>
    ),
  },
  {
    heading: 'Contact',
    body: (
      <p>
        Questions about these terms: <ContactLink />.
      </p>
    ),
  },
];

export const TermsPage = (): JSX.Element => <LegalDocument title="Terms of Service" sections={sections} />;
