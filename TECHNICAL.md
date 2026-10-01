# Technical Reference

Deep-dive documentation for `nba-iq`. Start with [README.md](README.md) for
what the app is; this file covers how it actually works. For contribution
conventions (style, testing, pre-commit checklist), see [AGENTS.md](AGENTS.md).

## Data Sources

| Source | Used for | Notes |
|---|---|---|
| [`stats.nba.com`](https://stats.nba.com) (via `nba_api`) | Player and team per-game stats | Sits behind Akamai, which **blocks AWS and GitHub Actions IP ranges**. Requests from CI can fail; the scraper tolerates it and the app keeps serving the last good data from Postgres. |
| ESPN public API (`site.api.espn.com`) | Games, scores, live scoreboard, betting odds | Not IP-blocked from AWS, which is why it backs the scoreboard and odds board instead of `cdn.nba.com`. |
| [CBS Sports](https://www.cbssports.com/nba/injuries/) | Injury reports and specific positions | Parsed with Beautiful Soup. |
| [`nba2kapi.com`](https://nba2kapi.com) | NBA 2K overall ratings, 35 attributes, badges, and rating history | Data originates from [2kratings.com](https://www.2kratings.com). Not affiliated with or endorsed by 2K Sports, Take-Two, or the NBA. Matching to our players is name-based, since 2K publishes no NBA ids. |
| `stats.nba.com` &rarr; `scheduleleaguev2` | The season schedule, including games not yet played | Publishes the whole season in advance, which is what lets a prediction be made for tonight's game before any box score exists. When the endpoint is unavailable it falls back to the `nba.com/games?date=` pages (real NBA game ids, reachable from CI), then to reconstructing completed games from `leaguegamelog`. ESPN is deliberately *not* a fallback: it keys on its own event ids, which do not join to any `stats.nba.com` game id. |
| `nba.com` web pages (`/games?date=`, `/players`) | Schedule fallback and roster fallback for `player_team_stints` | Read from the `__NEXT_DATA__` JSON embedded in each page, with a desktop user agent. The players index gives every current team assignment in one request. |
| `stats.nba.com` &rarr; `playergamelogs`, `leaguegamelog` | Per-game player and team box scores | One request each covers a whole season, or any date window within it, so the incremental sync costs two requests per run rather than one per game. |
| `stats.nba.com` &rarr; `boxscoresummaryv3` (fallback `boxscoresummaryv2`) | Official per-game inactive lists, from the `InactivePlayers` result set | The only phase that costs a request **per game**, which is why the truth-layer backfill is slow and opt-in. `v3` is tried first because `nba_api` documents `v2` as having no data for games on or after 2025-04-10. |

## Configuration

Runtime configuration lives in GitHub Actions secrets and variables, and
Serverless injects it into the Lambda environment (see `backend/serverless.yml`).
The backend, scraper, and seed script all read from a single set of variables;
the frontend has its own, which Vite reads at build time.

Backend, scraper, and seed:

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | yes | Postgres connection string |
| `AUTH_SECRET` | yes | JWT signing key. Must be at least 32 characters and not a placeholder, or `backend/src/config.ts` refuses to boot. |
| `ANTHROPIC_API_KEY` | for AI features | Anthropic API key |
| `FRONTEND_URL` | no | Comma-separated CORS allow-list |
| `GOOGLE_CLIENT_ID` | for Google Sign-In | OAuth client ID, verified server-side |
| `FROM_EMAIL` | for password reset | An SES-verified sender identity |
| `PORT` | no | Backend port, defaults to `3001` |

Frontend build:

| Variable | Description |
|---|---|
| `VITE_API_URL` | Origin of the deployed API |
| `VITE_GOOGLE_CLIENT_ID` | Google OAuth client ID. Public by design, since it is baked into the bundle. Leave empty to hide the Google button. |

Never put a secret in a `VITE_`-prefixed variable. Vite inlines those values into
the static JavaScript at build time, so anything there is readable by anyone who
loads the page.

## API

Base path `/api`. All responses are JSON.

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/health` | GET | none | Liveness check, used by the post-deploy smoke test |
| `/status` | GET | none | Last-updated timestamps per data source |
| `/status/benchmarks` | GET | none | League benchmarks computed from the player pool |
| `/players` | GET | none | List players (search, team/position filters) |
| `/players/:id` | GET | none | Player detail |
| `/teams` | GET | none | All teams |
| `/teams/:id` | GET | none | Team detail |
| `/games` | GET | none | Today's and recent games |
| `/games/live` | GET | none | Live scoreboard, ESPN-backed |
| `/ratings2k/players` | GET | none | NBA 2K cards (roster type, search, sort, paging) |
| `/ratings2k/players/:slug` | GET | none | One 2K card with attributes, badges, and rating history |
| `/ratings2k/by-player-name` | GET | none | Resolve an app player to their 2K card by name |
| `/auth/register` | POST | none | Create an account (rate limited) |
| `/auth/login` | POST | none | Email/password sign-in (rate limited) |
| `/auth/google` | POST | none | Google Sign-In, ID token verified server-side |
| `/auth/forgot-password` | POST | none | Send a reset email (rate limited) |
| `/auth/reset-password` | POST | none | Consume a reset token (rate limited) |
| `/auth/me` | GET | user | Current user, including the `is_admin` flag |
| `/auth/profile` | PATCH | user | Update profile fields |
| `/auth/change-password` | PATCH | user | Change password |
| `/auth/set-email` | PATCH | user | Set or change email address |
| `/fantasy/roster` | GET | user | Current user's roster |
| `/fantasy/roster` | POST | user | Add a player |
| `/fantasy/roster/:playerId` | DELETE | user | Drop a player |
| `/fantasy/weekly-outlook` | GET | user | Simulated category ranges and win probabilities for the roster's week (`start`, `days` up to 14) |
| `/predictions/slate` | GET | none | Projections slate for a date; `sort=impact` (default) or `sort=edge` (largest move vs each player's usual) |
| `/preferences` | GET / PATCH | user | Team Preferences questionnaire |
| `/ai/chat` | POST | user | AI chat with roster, waiver, or betting context |
| `/ai/team-analysis` | GET | user | 9-category team analysis |
| `/ai/waiver-suggestions` | GET | user | Trade targets and waiver pickups |
| `/betting/odds` | GET | none | Odds board for upcoming games |
| `/betting/picks` | GET | user | AI picks and suggested parlay |
| `/betting/bets` | GET / POST | user | List or track bets |
| `/betting/bets/:id` | PATCH / DELETE | user | Settle or remove a bet |
| `/track/pageview` | POST | optional | Pageview beacon, counts anonymous visits |
| `/admin/users` | GET | admin | User list |
| `/admin/stats` | GET | admin | Totals and 24-hour activity |
| `/admin/views` | GET | admin | Pageview breakdown |

Everything under `/api/ai/*` also enforces a daily per-user request cap.

## Architecture

```
                        GitHub Actions cron (every 6h)
                                    │
    stats.nba.com ─┐                ▼
    ESPN API ──────┼──────► scraper/run_scraper.py ──► upsert
    CBS Sports ────┘                                     │
                                                         ▼
  Browser ──► CloudFront ──► S3 (React SPA)      PostgreSQL (Neon)
                                                         ▲
                                 ┌───────────────────────┘
  Browser ──► API Gateway ──► Lambda (Express)
                                 ├──► Anthropic Claude API
                                 └──► AWS SES (password reset)
```

The scraper writes to Postgres on its own schedule, fully decoupled from the API.
Request latency therefore never depends on data collection, and a blocked scrape
only means the app serves slightly staler stats.

Inside the backend, route handlers validate input and return JSON, `requireAuth`
binds the user id from the JWT rather than from a query parameter, services own
the business logic (fantasy scoring, Claude calls, odds math, SES sends), and
every database access funnels through one parameterized `query()` helper in
`db.ts`.

On the frontend, `src/api/client.ts` is the only module that speaks HTTP. No
component fetches directly.

## Database Schema and Migrations

- **`db/schema.sql`** declares 27 tables: `players`, `teams`, `games`, `users`,
  `password_reset_tokens`, `my_roster`, `analysis_cache`, `waiver_cache`,
  `bets`, `betting_cache`, `chat_history`, `page_views`, `rate_limits`,
  `player_season_stats`, `team_season_stats`, `nba_2k_players`,
  `nba_2k_attributes`, `nba_2k_badges`, `nba_2k_rating_history`,
  `schema_migrations`, `ingestion_runs`, `nba_schedule`, `player_game_logs`,
  `team_game_logs`, `player_game_status`, `player_team_stints`, and
  `player_injury_reports`. It uses `CREATE TABLE / INDEX IF NOT EXISTS`
  throughout, so re-running it is safe.
- **`db/migrations/`** holds sequential, hand-written SQL migrations, `001`
  through `016`, covering email and password reset, team conference backfill,
  user preferences, Google Sign-In, profile fields, betting, bet money fields,
  rate limits, admin and pageviews, the team abbreviation backfill, the
  historical season-stats tables, the NBA 2K ratings tables, the data truth
  layer, the prediction store and its run provenance, and the odds history. Each is idempotent, so re-applying one is
  harmless.
- The four `nba_2k_*` tables are key-value rather than wide (one row per
  attribute, per badge, per game version) because 2K reshuffles its attribute and
  badge sets every September, and with no migration runner a wide table would
  need a hand-applied migration every game year.
- There is no migration runner. A fresh database needs `schema.sql` **and then
  every migration in numeric order**. `schema.sql` has drifted slightly behind
  and does not declare `users.ai_preferences`, which migration `003` adds and the
  preferences and AI features depend on, so the schema alone is not enough.
- Migrations are applied by hand in the Neon SQL editor.
- Since there is no runner, "did I already apply this here?" has historically had
  no answer. Migration `013` adds a `schema_migrations` table and
  `scraper/check_migrations.py` reports against it:

  ```bash
  cd scraper
  python check_migrations.py                        # prod (DATABASE_URL)
  python check_migrations.py --dev                  # dev branch
  python check_migrations.py --record 013_truth_layer.sql
  ```

  It is read-only except for `--record`, which writes a `schema_migrations` row
  for a migration you have just applied by hand. It never executes SQL from a
  migration file, so it cannot be mistaken for a runner. A database with no
  `schema_migrations` table is reported as "nothing recorded" rather than
  treated as empty.
- Admin has no signup path and is enforced server-side. Grant it directly:
  `UPDATE users SET is_admin = TRUE WHERE email = 'you@example.com';`

### The data truth layer

Migration `013` adds the tables a fantasy availability model trains on. The
short version of why: a model fitted only on recorded game logs answers "how
much will he produce **given** he plays", and a Phase 0 feasibility spike
measured that as overstating production over the real schedule by roughly half.
The training unit therefore has to be the *scheduled player-game*, including the
ones a player missed and the reason he missed them.

| Table | Holds |
|---|---|
| `nba_schedule` | Every scheduled game, played or not, keyed on NBA's game id. Separate from `games`, which keys on ESPN event ids — the two id spaces do not join. |
| `player_game_logs` | One row per player per game they recorded a line for. `minutes` is decimal (34.20 for `34:12`); `dnp_reason` is the verbatim box-score `COMMENT`. Migration `018` adds `oreb` / `dreb` / `pf`, the starter's box-score `position`, and `details_source` / `details_fetched_at`; those, plus `started` and `dnp_reason`, come from one `boxscoretraditionalv3` call per game. |
| `team_game_logs` | Two rows per game. Doubles as the completed-game schedule. Migration `018` adds team `oreb` / `dreb` / `pf`. |
| `player_game_status` | **The training universe.** One row per scheduled player-game, appeared or not, with `rostered` / `listed_inactive` / `played` kept distinct. Rows come from game logs plus the official inactive list (`source` `boxscoresummary<v>+playergamelogs`), and from the box-details pass, which inserts a row for each active player who never entered and has no other row (`source` `boxscoretraditionalv3`, `played` false, `dnp_reason` the box-score comment, `listed_inactive` NULL for `DND` / `NWT` comments). That insert never overwrites; see `ml/MODEL.md` 17.8. |
| `player_team_stints` | Which team a player belonged to over which span, so a feature cannot leak a trade backwards into pre-trade rows. |
| `player_injury_reports` | Append-only history of scraped injury designations — "what was known at the time", which the overwrite-in-place `players.injury_status` cannot answer. |
| `ingestion_runs` | One row per truth-layer scraper phase invocation, for tracing which rows came from which run. |
| `odds_snapshots` / `espn_event_map` | Migration `016`. Append-only odds history and the ESPN event to NBA game id map; see "Odds snapshots" below. |
| `prediction_runs` / `player_game_predictions` | Migration `014`. The append-only prediction store — see `ml/README.md` and `ml/MODEL.md` for the modeling side. |

Ids are `TEXT` throughout, because NBA game ids carry leading zeros
(`0022300061`) and stop being valid ids the moment something parses them as a
number.

**The incremental half runs as part of the normal 6-hour cron** — roster snapshot,
schedule, game logs, then game status, after the existing four scrapes. The run
exits non-zero if the schedule sync failed from every source. Each phase logs and
continues on failure, so an outage in one cannot cost the others. The game-log
sync is watermarked on `MAX(game_date)` and re-reads a trailing few-day window to
pick up scorer corrections. The status phase is bounded to recent games per run,
since it costs one request per game.

#### Runbook: migration → backfill → validate

1. **Apply the migration by hand, to both databases.** Paste the migration file
   into the Neon SQL editor and run it against **prod first, then the dev
   branch**. Then record it:

   ```bash
   cd scraper
   python check_migrations.py --record 013_truth_layer.sql
   python check_migrations.py --dev --record 013_truth_layer.sql
   ```

2. **Backfill, locally.** Like `--backfill-history`, this must run from a
   residential IP: `stats.nba.com` is Akamai-blocked from GitHub Actions and
   throttles hard, so it is deliberately not part of the cron.

   ```bash
   cd scraper
   pip install -r requirements.txt
   python run_scraper.py --backfill-game-logs --dev          # rehearse on dev
   python run_scraper.py --backfill-game-logs --dry-run      # reads only, writes nothing
   python run_scraper.py --backfill-game-logs                # prod, 2022-23 to current
   python run_scraper.py --backfill-game-logs --from 2022-23 --to 2024-25
   ```

   Expect it to take hours: schedule and game logs are two requests per season,
   while the inactive lists are one request per game with a deliberate pacing
   delay between requests to stay under Akamai's radar. It is resumable — a
   game is only fetched if it has no `player_game_status` rows at all — so a
   killed run picks back up where it left off.

   Box-score details (migration `018`) backfill separately, one season and one
   `boxscoretraditionalv3` request per game, oldest first. A game is selected
   while any of its player rows has `details_fetched_at` NULL, so it resumes
   the same way; `--limit` bounds a slice (about 300 games per 30 minutes at
   the 5s delay). The manual `Box Details Backfill` workflow runs that slice,
   but exits immediately if `stats.nba.com` is unreachable from the runner.
   The normal cron fills the same columns for newly completed games beside the
   inactive-list fetch.

   ```bash
   python run_scraper.py --backfill-box-details --season 2024-25 --limit 300
   ```

3. **Validate.** Read-only, takes no locks, safe against prod mid-scrape:

   ```bash
   python run_scraper.py --validate-game-logs
   python run_scraper.py --validate-game-logs --from 2024-25 --to 2025-26
   ```

   Per season it checks two team rows per completed game, no duplicate
   player/game keys, `FGM <= FGA`, `3PM <= 3PA`, `3PM <= FGM`, `FTM <= FTA`,
   player-points summing to team points, and lists any completed scheduled game
   with no logs or no status rows. It finishes with `pg_total_relation_size` for
   each new table.

`--dry-run` is honoured by every write path in the scraper, including the four
original scrapes: reads still run, writes are counted and skipped.

#### Tests

The truth layer's parsing, watermark, stint, validation, and derivation logic is
pure and unit-tested with no database and no network:

```bash
python -m pytest scraper/test_truth_layer.py
```

Fixtures are copied from `nba_api`'s own `expected_data` column declarations, so
a fixture that drifts from the real response shape cannot pass silently.

### Odds snapshots

Migration `016` adds `odds_snapshots`, an append-only history of the ESPN
scoreboard odds the Betting page otherwise only fetches live. `scraper/odds.py`
reads today through today+2 (Eastern), one request per day, and writes one row
per market and selection (spread home/away, total over/under, moneyline
home/away) for every game not yet tipped. The away spread line is the home line
sign-flipped; `price` is NULL with `price_observed = false` when the provider
published no price, never a default. Each event is matched to an `nba_schedule`
game on Eastern date plus home and away tricodes, through an explicit ESPN to
NBA alias table (`GS` to `GSW`, `NY` to `NYK`, ...), and the match is kept in
`espn_event_map`; an unmatched event is still recorded, with a NULL
`nba_game_id`, and counted in its `ingestion_runs` notes. It runs at the end of
the full scrape and on its own every 30 minutes from 15:00 to 03:30 UTC
(`python run_scraper.py --odds-only`).

## Prediction system

`ml/` trains and serves the availability/minutes/production models behind the
Projections and Watchlist tabs. It is a separate package with its own README
and living spec: see [`ml/README.md`](ml/README.md) and
[`ml/MODEL.md`](ml/MODEL.md).

The backend reads the production run in three more places (MODEL.md 19): the
Improve Team waiver and trade candidates are ranked by expected category wins
added before Claude sees them (`services/candidateRanking.ts`), My Team's weekly
outlook is a seeded joint simulation of the roster week
(`services/weeklySimulation.ts`), and every Projections row carries its deltas
against the player's usual and can be sorted by them (`services/projectionReasons.ts`,
shared with the Watchlist).

## MCP server

`backend/src/mcp/` is a stdio MCP server that exposes read-only slices of the
app's data to MCP clients such as Claude Desktop and Claude Code. It imports
the same service modules the REST API uses and talks to Postgres through the
same `query()` helper; it starts no HTTP listener and is not deployed to
Lambda.

Tools: `get_slate`, `get_watchlist`, `get_player_projections`,
`get_player_analytics`, `search_players`, `get_stat_leaders`. All are
read-only; there are no mutation tools.

Build it once, then point your client at the compiled entry:

    cd backend
    npm install
    npm run build

`DATABASE_URL` is read from the repo-root `.env` (the same file the backend
uses). To harden further, create a read-only Postgres role and pass its
connection string via the client's `env` block instead.

Claude Desktop (`%APPDATA%\Claude\claude_desktop_config.json`):

    {
      "mcpServers": {
        "nba-iq": {
          "command": "node",
          "args": ["C:\\Users\\CJ\\code\\fantasy-app\\backend\\dist\\mcp\\index.js"]
        }
      }
    }

Claude Code:

    claude mcp add nba-iq -- node C:\Users\CJ\code\fantasy-app\backend\dist\mcp\index.js

Re-run `npm run build` after changing anything under `backend/src/`; the
clients run the compiled `dist/` output. `npm run mcp:dev` runs the server
from source with tsx for local debugging.

## Deployment

Deployment is fully automated by GitHub Actions. **There is nothing to run by
hand.**

**On every pull request** (`.github/workflows/ci.yml`):

1. Backend job: typecheck source, typecheck tests, run unit and API tests
2. ML job: pytest against the frozen-model test suite
3. Frontend job: typecheck, run unit tests, production build
4. E2E job: typecheck E2E, then the Playwright suite on Chromium
5. If all jobs pass, a **preview deploy** goes out to the shared `dev`
   environment (`serverless deploy --stage dev`, plus a sync to the dev S3 bucket
   and a CloudFront invalidation). Two open PRs share one dev environment, so the
   last push wins.

**On push or merge to `main`:** CI re-runs against the merge commit, and
`.github/workflows/deploy.yml` fires via `workflow_run`, but only if CI concluded
successfully. It then:

1. Builds and deploys the backend with `serverless deploy --stage prod`
2. Builds the frontend and syncs it to the prod S3 bucket, giving content-hashed
   `assets/` a one-year immutable cache and root files `no-cache`, then
   invalidates CloudFront
3. Runs a smoke test against `/api/health` and the frontend root

**On a 6-hour cron** (`.github/workflows/scraper.yml`): `python
scraper/run_scraper.py` refreshes stats in Postgres. The same workflow runs two
lighter lanes, `--injuries-only` and `--odds-only`, on their own schedules. It
can also be triggered manually with `workflow_dispatch`, which runs the full
scrape.

**On a daily cron** (`.github/workflows/predictions.yml`): publishes a fresh
prediction run to the store once the season is underway; a no-op in the
offseason.

The historical season backfill is **not** part of that cron. `stats.nba.com`
blocks CI IP ranges and throttles hard, so it is a one-time job run locally from
a residential connection: `python run_scraper.py --backfill-history`. It is
resumable — seasons already in the database are skipped — so an interrupted run
can simply be started again.

The **NBA 2K sync** is also opt-in and not on the cron, because 2K ratings change
a handful of times a season rather than every six hours:

```bash
cd scraper
python run_scraper.py --sync-2k                              # current rosters (~672 cards)
python run_scraper.py --sync-2k --team-types curr,class,allt # everything (~1,889 cards)
```

It defaults to current players only. Each card is written in its own
transaction, with its attributes and badges replaced wholesale, so re-running is
idempotent and a card is never left half-updated. Cards 2K no longer lists for a
synced roster type are pruned.

AWS access uses **OIDC role assumption**, so no long-lived AWS keys live in the
repo or in CI.

**Database migrations are deliberately not automated.** Every schema change gets
applied by hand in the Neon SQL editor with human review. For code regressions,
Lambda's built-in versioning covers fast rollback.

`backend/deploy.ps1` exists only as an emergency escape hatch, for something like
a GitHub Actions outage. Prefer the pipeline. If you do need it, deploy to an
unused stage so it doesn't fight the workflow over Lambda environment variables.

## Project Layout

```
nba-iq/
├── .env.example                  # env template (backend + scraper + seed)
├── AGENTS.md                     # contributor/AI-agent style guide
├── TECHNICAL.md                  # this file
├── .github/
│   ├── deploy-iam-policy.json    # IAM policy for the OIDC deploy role
│   └── workflows/
│       ├── ci.yml                # PR gate + dev preview deploy
│       ├── deploy.yml            # prod deploy on push to main
│       ├── scraper.yml           # 6-hour scraper cron
│       └── predictions.yml       # daily prediction-publish cron
├── db/
│   ├── schema.sql                # full PostgreSQL schema
│   ├── migrations/               # 001 through 014, sequential SQL migrations
│   └── seed.ts                   # sample players/teams/games
├── scraper/                      # players, teams, games, injuries, truth layer
├── ml/                           # availability/minutes/production models
├── backend/
│   ├── package.json
│   ├── serverless.yml            # Lambda + HTTP API definition
│   ├── deploy.ps1                # emergency manual deploy only
│   ├── src/
│   │   ├── app.ts                # Express app: middleware + router mounting
│   │   ├── db.ts                 # pg pool + parameterized query() helper
│   │   ├── middleware/           # auth, admin, rateLimit
│   │   ├── mcp/                  # stdio MCP server (read-only tools over the same services)
│   │   ├── routes/               # one file per resource
│   │   └── services/             # business logic
│   └── tests/                    # Vitest unit + Supertest API tests
└── frontend/
    ├── package.json
    ├── vite.config.ts            # React + Tailwind plugins, /api dev proxy
    ├── src/
    │   ├── App.tsx               # routes
    │   ├── api/                  # client.ts + response caches
    │   ├── components/           # grouped by feature
    │   ├── hooks/
    │   ├── pages/
    │   └── types/
    ├── test/                     # Vitest unit tests
    └── e2e/                      # Playwright specs, page objects, fixtures
```
