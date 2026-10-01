import type { Page, Route } from '@playwright/test';
import { ALL_PLAYERS, type PlayerFixture } from './players';
import { slateFixture } from './slate';
import { watchlistFixture } from './watchlist';
import type {
  Team, Game, TeamAnalysis,
  BettingGame, PropPicksResponse, Bet, LedgerSummary,
  PlayerAnalytics, PlayerPredictionsResponse, SlateResponse, WatchlistResponse,
  RosterPlayer, StartSitResponse, StreamersResponse, TradeCheckResponse,
} from '../../src/types';

export interface WaiverSuggestionsResponse {
  trade_targets: Array<{ name: string; reasoning: string }>;
  waiver_pickups: Array<{ name: string; reasoning: string }>;
  summary: string;
  empty_roster?: boolean;
  cached?: boolean;
  cached_at?: string;
}

export interface MockOptions {
  players?: PlayerFixture[];
  teams?: Team[];
  games?: Game[];
  playerAnalytics?: PlayerAnalytics;
  playerPredictions?: PlayerPredictionsResponse;
  rosterRequiresAuth?: boolean;
  roster?: RosterPlayer[];
  startSit?: StartSitResponse;
  streamers?: StreamersResponse;
  tradeCheck?: TradeCheckResponse;
  teamAnalysis?: TeamAnalysis;
  waiverSuggestions?: WaiverSuggestionsResponse;
  bettingOdds?: BettingGame[];
  propPicks?: PropPicksResponse;
  bets?: { bets: Bet[]; summary: LedgerSummary };
  watchlist?: WatchlistResponse | ((params: URLSearchParams) => WatchlistResponse);
  slate?: SlateResponse | ((params: URLSearchParams) => SlateResponse);
  custom?: Array<{ url: RegExp | string; handler: (route: Route) => Promise<void> | void }>;
}

export async function mockApi(page: Page, opts: MockOptions = {}): Promise<void> {
  const players = opts.players ?? ALL_PLAYERS;
  const teams = opts.teams ?? [];
  const games = opts.games ?? [];

  // aborting third-party origins keeps the google login script from racing login page assertions.
  await page.route(/https:\/\/(accounts\.google\.com|cdn\.nba\.com|.*\.nba\.com)/, (route) => {
    route.abort();
  });

  for (const { url, handler } of opts.custom ?? []) {
    await page.route(url, handler);
  }

  await page.route('**/api/players*', (route) => {
    const url = new URL(route.request().url());
    const search = url.searchParams.get('search')?.toLowerCase();
    const filtered = search
      ? players.filter((p) => p.name.toLowerCase().includes(search))
      : players;
    route.fulfill({ json: filtered });
  });

  // these two must stay after the players list route: `**/api/players*` stops at the next slash.
  await page.route('**/api/players/*/analytics', (route) => {
    if (!opts.playerAnalytics) {
      route.fulfill({ status: 404, json: { error: 'Not found' } });
      return;
    }
    route.fulfill({ json: opts.playerAnalytics });
  });

  await page.route('**/api/players/*/predictions*', (route) => {
    const fallback: PlayerPredictionsResponse = {
      player_id: 0,
      nba_player_id: null,
      run: null,
      stats: [],
      games: [],
    };
    route.fulfill({ json: opts.playerPredictions ?? fallback });
  });

  await page.route('**/api/watchlist*', (route) => {
    const params = new URL(route.request().url()).searchParams;
    const payload =
      typeof opts.watchlist === 'function'
        ? opts.watchlist(params)
        : opts.watchlist ?? watchlistFixture(params);
    route.fulfill({ json: payload });
  });

  await page.route('**/api/predictions/slate*', (route) => {
    const params = new URL(route.request().url()).searchParams;
    const payload =
      typeof opts.slate === 'function' ? opts.slate(params) : opts.slate ?? slateFixture(params);
    route.fulfill({ json: payload });
  });

  await page.route('**/api/teams', (route) => route.fulfill({ json: teams }));
  await page.route('**/api/games**', (route) => route.fulfill({ json: games }));

  await page.route('**/api/fantasy/**', (route) => {
    if (opts.rosterRequiresAuth) {
      route.fulfill({ status: 401, json: { error: 'Unauthorized' } });
      return;
    }
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/fantasy/roster') && opts.roster) {
      route.fulfill({ json: opts.roster });
      return;
    }
    if (path.endsWith('/fantasy/start-sit') && opts.startSit) {
      route.fulfill({ json: opts.startSit });
      return;
    }
    if (path.endsWith('/fantasy/streamers') && opts.streamers) {
      route.fulfill({ json: opts.streamers });
      return;
    }
    if (path.endsWith('/fantasy/trade-check') && opts.tradeCheck) {
      route.fulfill({ json: opts.tradeCheck });
      return;
    }
    route.fulfill({ json: [] });
  });

  await page.route('**/api/ai/team-analysis', (route) => {
    const fallback: TeamAnalysis = {
      categories: {},
      strengths: [],
      weaknesses: [],
      suggestions: [],
    };
    route.fulfill({ json: opts.teamAnalysis ?? fallback });
  });

  await page.route('**/api/ai/waiver-suggestions*', (route) => {
    const fallback: WaiverSuggestionsResponse = {
      trade_targets: [],
      waiver_pickups: [],
      summary: '',
      empty_roster: true,
    };
    route.fulfill({ json: opts.waiverSuggestions ?? fallback });
  });

  await page.route('**/api/history/**', (route) => {
    if (new URL(route.request().url()).pathname.endsWith('/history/seasons')) {
      route.fulfill({ json: { seasons: [] } });
      return;
    }
    route.fulfill({ status: 404, json: { error: 'Not found' } });
  });

  await page.route('**/api/ratings2k/by-player-name*', (route) =>
    route.fulfill({ json: { player: null } }),
  );

  await page.route('**/api/ratings2k/players*', (route) =>
    route.fulfill({ json: { total: 0, players: [] } }),
  );

  await page.route('**/api/auth/me', (route) =>
    route.fulfill({ status: 401, json: { error: 'Unauthorized' } }),
  );

  await page.route('**/api/track/pageview', (route) =>
    route.fulfill({ status: 204, body: '' }),
  );

  await page.route('**/api/betting/odds', (route) =>
    route.fulfill({ json: { games: opts.bettingOdds ?? [], fetched_at: '2026-05-24T12:00:00Z' } }),
  );

  await page.route('**/api/betting/props', (route) =>
    route.fulfill({ json: opts.propPicks ?? { run: null, picks: [] } }),
  );

  await page.route('**/api/betting/bets**', (route) => {
    const method = route.request().method();
    if (method === 'POST') {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      route.fulfill({
        status: 201,
        json: {
          id: 1, nba_game_id: null, home_team: null, away_team: null, game_date: null,
          selection: null, line: null, american_odds: null, description: null,
          status: 'pending', created_at: '2026-05-24T12:00:00Z', settled_at: null,
          ...body,
        },
      });
      return;
    }
    if (method === 'PATCH') {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      route.fulfill({
        json: {
          id: 1, market: 'custom', nba_game_id: null, home_team: null, away_team: null,
          game_date: null, selection: null, line: null, american_odds: null,
          description: 'mock bet', status: body.status,
          created_at: '2026-05-24T12:00:00Z', settled_at: '2026-05-24T13:00:00Z',
        },
      });
      return;
    }
    if (method === 'DELETE') {
      route.fulfill({ status: 204, body: '' });
      return;
    }
    const emptyLedger = {
      bets: [],
      summary: { wins: 0, losses: 0, pushes: 0, pending: 0, net: 0 },
    };
    route.fulfill({ json: opts.bets ?? emptyLedger });
  });

  await page.route('**/api/preferences', (route) => {
    if (route.request().method() === 'PATCH') {
      route.fulfill({ json: route.request().postDataJSON() ?? {} });
      return;
    }
    route.fulfill({ json: {} });
  });
}
