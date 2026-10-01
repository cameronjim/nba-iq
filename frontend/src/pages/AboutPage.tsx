import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

// every tech mentioned here must actually be in use in the repo, nothing aspirational.

interface Entry {
  term: string;
  detail: ReactNode;
}

const DefinitionList = ({ entries }: { entries: Entry[] }): JSX.Element => (
  <dl className="grid grid-cols-1 gap-x-8 sm:grid-cols-[11rem_1fr]">
    {entries.map((entry) => (
      <div key={entry.term} className="contents">
        <dt className="pt-3 font-medium sm:border-t sm:border-base-300 sm:py-2.5">{entry.term}</dt>
        <dd className="pb-1 text-sm text-muted sm:border-t sm:border-base-300 sm:py-2.5">{entry.detail}</dd>
      </div>
    ))}
  </dl>
);

const Section = ({ title, children }: { title: string; children: ReactNode }): JSX.Element => (
  <section className="border-t border-base-300 pt-6">
    <h2 className="mb-3 font-display text-2xl font-semibold uppercase tracking-wide">{title}</h2>
    <div className="space-y-4">{children}</div>
  </section>
);

const stack: Entry[] = [
  {
    term: 'Frontend',
    detail:
      'React 18 and TypeScript, built with Vite 6. Tailwind 4 and DaisyUI 5 for styling, React Router 7 for routes, Axios for the API client, Recharts for charts, and @react-oauth/google for Google sign-in.',
  },
  {
    term: 'Backend',
    detail:
      'Express 4 in TypeScript on Node 20, running as an AWS Lambda behind API Gateway through the Serverless Framework. PostgreSQL through the pg driver, JWT sessions, bcryptjs for passwords, and the Anthropic SDK for the Claude features.',
  },
  {
    term: 'Database',
    detail: 'Postgres on Neon. The production database and a separate dev branch each have their own connection string.',
  },
  {
    term: 'Hosting',
    detail: 'The frontend is static files on S3 behind CloudFront. Password-reset email goes out through Amazon SES.',
  },
  {
    term: 'Data pipeline',
    detail:
      'Python scripts using nba_api, requests, Beautiful Soup, and pdfplumber write into Postgres from GitHub Actions. The main refresh runs every six hours, with more frequent runs during game hours.',
  },
  {
    term: 'Projections',
    detail:
      'A Python package under ml/ trains and serves a minutes and availability model with scikit-learn and LightGBM. A daily GitHub Actions job publishes the next seven days of projections to the database.',
  },
];

const practices: Entry[] = [
  {
    term: 'Types and style',
    detail:
      'Strict TypeScript in the frontend and backend, no any, and a single AGENTS.md at the repo root that sets the conventions for both people and coding agents.',
  },
  {
    term: 'SQL',
    detail: 'All queries go through one helper and use parameter placeholders. User input is never interpolated into SQL.',
  },
  {
    term: 'Tests',
    detail:
      'Vitest unit tests, Vitest with Supertest for API routes against a mocked database, React Testing Library for components, and Playwright for end-to-end flows. Python tests cover the scraper and the ML package. Tests never call Anthropic or AWS.',
  },
  {
    term: 'Auth',
    detail:
      'Passwords are hashed with bcrypt at cost 10. Google sign-in tokens are verified server-side. Every user-scoped query is bound to the user id in the verified token, never to an id the client sends.',
  },
  {
    term: 'Password reset',
    detail:
      'Reset tokens are stored as SHA-256 hashes, expire after one hour, and work once. The forgot-password response is the same whether or not the email exists.',
  },
  {
    term: 'Rate limits',
    detail: 'Sign-in, password reset, page-view tracking, and the Claude endpoints are rate limited, with counters kept in Postgres.',
  },
];

const prSteps: Entry[] = [
  { term: 'Test', detail: 'Backend typecheck and tests, frontend typecheck, tests and build, the Playwright suite, and the ML and scraper pytest suites.' },
  { term: 'Preview', detail: 'If those pass, the backend deploys to the dev stage on Lambda and the frontend is synced to the dev bucket, with a CloudFront invalidation.' },
];

const mainSteps: Entry[] = [
  { term: 'Deploy', detail: 'After CI passes on main, a second workflow runs serverless deploy for the prod stage, builds the frontend against the prod API, syncs it to S3, and invalidates CloudFront.' },
  { term: 'Smoke test', detail: 'The workflow then requests /api/health and the frontend root and fails if either does not respond.' },
];

export const AboutPage = (): JSX.Element => {
  return (
    <div className="mx-auto max-w-[900px] space-y-8 px-4 py-8">
      <header className="max-w-prose">
        <h1 className="font-display text-3xl font-semibold uppercase tracking-wide">About NBA IQ</h1>
        <p className="mt-3 leading-relaxed">
          NBA IQ is a basketball site I built to look at player stats, nightly projections, and my own fantasy team
          in one place. It also has an odds board and a bet ledger. I wrote the frontend, the API, the database
          schema, the scraper, and the projection model myself, and I run it as a hobby project.
        </p>
        <p className="mt-3 leading-relaxed">
          Stats come from NBA.com, injury reports from CBS Sports, odds from ESPN, and 2K ratings from nba2kapi.com.
          The Claude features (team analysis, waiver suggestions, betting picks, and chat) send your roster and
          preferences to Anthropic. The <Link to="/privacy" className="link">privacy page</Link> lists exactly what
          is sent and stored.
        </p>
      </header>

      <Section title="What it is built with">
        <DefinitionList entries={stack} />
      </Section>

      <Section title="How the pieces fit">
        <div className="max-w-prose space-y-3 leading-relaxed">
          <p>
            The scraper writes player, team, game, and injury data into Postgres on its own schedule. The web app
            never waits on a scrape. The React app calls the Lambda API, which reads that data and, for the Claude
            pages, builds a prompt from it and calls Anthropic.
          </p>
          <p>
            In the frontend, components render, hooks hold state and effects, and a single API client makes every
            HTTP request. In the backend, routes validate input, services hold the logic, and one database helper
            runs every query. The fantasy score service has the formulas for NBA fantasy points, FanDuel, DraftKings,
            and Yahoo High Score.
          </p>
        </div>
      </Section>

      <Section title="Engineering practices">
        <DefinitionList entries={practices} />
      </Section>

      <Section title="Deployment">
        <div>
          <h3 className="mb-1 font-medium">On every pull request</h3>
          <DefinitionList entries={prSteps} />
        </div>
        <div>
          <h3 className="mb-1 font-medium">On merge to main</h3>
          <DefinitionList entries={mainSteps} />
        </div>
        <p className="max-w-prose text-sm leading-relaxed text-muted">
          Database migrations run from a separate manual workflow. It defaults to a dry run that prints the plan,
          applies each file in its own transaction, and stops at the first failure. Production runs are for additive
          changes only.
        </p>
      </Section>

      <section className="border-t border-base-300 pt-6">
        <h2 className="mb-3 font-display text-2xl font-semibold uppercase tracking-wide">Contact</h2>
        <p className="text-sm">
          <a href="mailto:cjim02@student.ubc.ca" className="link">
            cjim02@student.ubc.ca
          </a>
          {' · '}
          <a href="https://github.com/cameronjim" target="_blank" rel="noopener noreferrer" className="link">
            GitHub
          </a>
        </p>
      </section>
    </div>
  );
};
