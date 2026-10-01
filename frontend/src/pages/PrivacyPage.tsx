import { ContactLink, LegalDocument, type LegalSection } from '../components/LegalDocument';

const sections: LegalSection[] = [
  {
    heading: 'Who runs this',
    body: (
      <p>
        NBA IQ is a personal project run by one person. This page lists what the site stores about you, why, and who
        else handles it. Questions go to <ContactLink />.
      </p>
    ),
  },
  {
    heading: 'What the site stores',
    body: (
      <>
        <p>
          If you create an account: a username, an email address, and either a bcrypt hash of your password or a
          Google account id if you sign in with Google. A name and phone number are stored only if you add them on
          your profile.
        </p>
        <p>
          If you use the tools: your fantasy roster, your Team Preferences answers (including any free-text notes),
          and the bets you log (market, game, selection, line, odds, description, stake, and result). The latest
          Claude team analysis and waiver suggestions are cached per account so they do not have to
          be regenerated.
        </p>
        <p>
          If you request a password reset: a SHA-256 hash of the reset token and its expiry time. The token itself is
          only in the email link.
        </p>
        <p>You do not need an account to browse stats, projections, 2K ratings, or the odds board.</p>
      </>
    ),
  },
  {
    heading: 'Usage records',
    body: (
      <>
        <p>
          Each page you open records the page path (never the query string), the time, your browser&apos;s user-agent
          string, the referring site on your first page load, and your account id if you are signed in. I use this to
          see which pages get used.
        </p>
        <p>
          To limit abuse, sign-in, registration, password-reset, and page-tracking requests are counted per IP
          address, and Claude requests per account, in time windows. The IP address appears in those counters. It is
          not stored with the page-view records.
        </p>
      </>
    ),
  },
  {
    heading: 'Browser storage',
    body: (
      <p>
        The site sets no cookies. It uses your browser&apos;s local storage for three things: a sign-in token (valid
        for seven days), your chosen theme, and whether you dismissed the preferences prompt. Signing out removes the
        token. Fonts are served from this site, not from Google.
      </p>
    ),
  },
  {
    heading: 'Who else handles your data',
    body: (
      <>
        <p>
          Amazon Web Services runs the API (Lambda and API Gateway), serves the pages (S3 and CloudFront), and sends
          password-reset email (SES), so it sees your email address and request traffic. Neon hosts the Postgres
          database that holds everything described in sections 2 and 3.
        </p>
        <p>
          Anthropic receives the data behind the Claude features: your roster and its stats, your Team Preferences,
          and any text you type into a chat. Your username, email address, and password are not sent. Anthropic&apos;s
          own policies cover how it handles that data.
        </p>
        <p>
          Google handles the sign-in step if you choose Google, and the site receives your verified email address and
          Google account id from it. Team logos and player photos load from NBA.com&apos;s image servers directly in
          your browser, so NBA.com sees your IP address when they load. Odds are fetched from ESPN by the server, not
          by your browser.
        </p>
        <p>I do not sell your data and I do not run advertising or third-party analytics.</p>
      </>
    ),
  },
  {
    heading: 'How long it is kept',
    body: (
      <p>
        There is no automatic deletion today. Account data, usage records, and rate-limit counters stay in the
        database until I remove them. Deleting an account removes its roster, bets, preferences, cached Claude output,
        and reset tokens. Page-view records from that account stay, with the account id cleared.
      </p>
    ),
  },
  {
    heading: 'Your choices',
    body: (
      <p>
        You can edit your profile, change your password, and remove players from your roster in the app. There is no
        delete-account button yet. To get a copy of your data or have your account deleted, email <ContactLink />{' '}
        from the address on the account and I will do it by hand.
      </p>
    ),
  },
  {
    heading: 'Security',
    body: (
      <p>
        Passwords are hashed with bcrypt, traffic uses HTTPS, and every request for account data is checked against
        your sign-in token on the server. No system is perfectly secure, and I cannot promise that nothing will go
        wrong.
      </p>
    ),
  },
  {
    heading: 'Children',
    body: (
      <p>
        The site is not meant for children under 13, and the betting section is for people of legal gambling age.
        Email me if you think a child has made an account and I will delete it.
      </p>
    ),
  },
  {
    heading: 'Changes',
    body: <p>If I change what the site collects, I will update this page and the date at the top.</p>,
  },
];

export const PrivacyPage = (): JSX.Element => <LegalDocument title="Privacy Policy" sections={sections} />;
