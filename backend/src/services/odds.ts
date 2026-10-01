import crypto from 'crypto';
import { etIsoDate } from './dates.js';
import { americanToImpliedProb } from './oddsMath.js';


const ODDS_CACHE_TTL = 10 * 60_000;

const FUTURE_WINDOW_DAYS = 2;

export interface SpreadMarket {
  home_line: number;
  away_line: number;
  home_price: number | null;
  away_price: number | null;
  home_implied: number | null;
  away_implied: number | null;
}

export interface TotalMarket {
  line: number;
  over_price: number | null;
  under_price: number | null;
  over_implied: number | null;
  under_implied: number | null;
}

export interface MoneylineMarket {
  home: number;
  away: number;
  home_implied: number;
  away_implied: number;
}

export interface BettingGame {
  espn_event_id: string;
  home_team: string;
  away_team: string;
  home_abbrev: string;
  away_abbrev: string;
  game_date: string; // YYYY-MM-DD in ET
  tipoff: string;    // e.g. "6/10 - 8:30 PM EDT"
  provider: string;
  markets: {
    spread?: SpreadMarket;
    total?: TotalMarket;
    moneyline?: MoneylineMarket;
  };
}

interface EspnPriceNode {
  close?: { line?: string; odds?: string };
}

export interface EspnOddsNode {
  provider?: { name?: string };
  details?: string;     // "NY -2.5" | "EVEN"
  overUnder?: number;
  spread?: number;      // home-relative
  homeTeamOdds?: { moneyLine?: number };
  awayTeamOdds?: { moneyLine?: number };
  moneyline?: { home?: EspnPriceNode; away?: EspnPriceNode };
  pointSpread?: { home?: EspnPriceNode; away?: EspnPriceNode };
  total?: { over?: EspnPriceNode; under?: EspnPriceNode };
}

export interface EspnEvent {
  id: string;
  date: string;
  status: { type: { name: string; detail?: string; shortDetail?: string } };
  competitions: Array<{
    competitors: Array<{
      homeAway: 'home' | 'away';
      team: { displayName: string; abbreviation?: string };
    }>;
    odds?: EspnOddsNode[];
  }>;
}

function parseAmerican(odds: string | undefined): number | undefined {
  if (!odds) return undefined;
  const trimmed = odds.trim();
  if (trimmed.toUpperCase() === 'EVEN') return 100;
  const n = parseInt(trimmed, 10);
  return Number.isFinite(n) ? n : undefined;
}

function parseLine(line: string | undefined): number | undefined {
  if (!line) return undefined;
  const n = parseFloat(line.replace(/^[ou]/i, ''));
  return Number.isFinite(n) ? n : undefined;
}

export function parseSpreadDetails(
  details: string | undefined,
  homeAbbrev: string,
  awayAbbrev: string
): number | undefined {
  if (!details) return undefined;
  if (details.trim().toUpperCase() === 'EVEN') return 0;
  const match = details.trim().match(/^([A-Z]{2,4})\s+(-?\d+(?:\.\d+)?)$/);
  if (!match) return undefined;
  const [, abbrev, lineStr] = match;
  const line = parseFloat(lineStr);
  if (abbrev === homeAbbrev) return line;
  if (abbrev === awayAbbrev) return -line;
  return undefined;
}

function impliedOrNull(price: number | undefined): number | null {
  return price == null ? null : americanToImpliedProb(price);
}

function parseSpreadMarket(odds: EspnOddsNode, homeAbbrev: string, awayAbbrev: string): SpreadMarket | undefined {
  const homeLine = parseLine(odds.pointSpread?.home?.close?.line);
  const homePrice = parseAmerican(odds.pointSpread?.home?.close?.odds);
  const awayPrice = parseAmerican(odds.pointSpread?.away?.close?.odds);

  const line =
    homeLine ??
    (typeof odds.spread === 'number' ? odds.spread : undefined) ??
    parseSpreadDetails(odds.details, homeAbbrev, awayAbbrev);
  if (line == null) return undefined;

  return {
    home_line: line,
    away_line: -line,
    home_price: homePrice ?? null,
    away_price: awayPrice ?? null,
    home_implied: impliedOrNull(homePrice),
    away_implied: impliedOrNull(awayPrice),
  };
}

function parseTotalMarket(odds: EspnOddsNode): TotalMarket | undefined {
  const line =
    parseLine(odds.total?.over?.close?.line) ??
    (typeof odds.overUnder === 'number' ? odds.overUnder : undefined);
  if (line == null) return undefined;

  const overPrice = parseAmerican(odds.total?.over?.close?.odds);
  const underPrice = parseAmerican(odds.total?.under?.close?.odds);
  return {
    line,
    over_price: overPrice ?? null,
    under_price: underPrice ?? null,
    over_implied: impliedOrNull(overPrice),
    under_implied: impliedOrNull(underPrice),
  };
}

function parseMoneylineMarket(odds: EspnOddsNode): MoneylineMarket | undefined {
  const home =
    parseAmerican(odds.moneyline?.home?.close?.odds) ?? odds.homeTeamOdds?.moneyLine;
  const away =
    parseAmerican(odds.moneyline?.away?.close?.odds) ?? odds.awayTeamOdds?.moneyLine;
  if (typeof home !== 'number' || typeof away !== 'number') return undefined;

  return {
    home,
    away,
    home_implied: americanToImpliedProb(home),
    away_implied: americanToImpliedProb(away),
  };
}

export function parseEventOdds(event: EspnEvent): BettingGame | null {
  const competition = event.competitions[0];
  const home = competition?.competitors.find((c) => c.homeAway === 'home');
  const away = competition?.competitors.find((c) => c.homeAway === 'away');
  if (!home || !away) return null;

  const homeAbbrev = home.team.abbreviation ?? '';
  const awayAbbrev = away.team.abbreviation ?? '';

  const game: BettingGame = {
    espn_event_id: event.id,
    home_team: home.team.displayName,
    away_team: away.team.displayName,
    home_abbrev: homeAbbrev,
    away_abbrev: awayAbbrev,
    // espn dates are utc; convert to the ET calendar date to match the games table
    game_date: new Date(event.date).toLocaleDateString('en-CA', { timeZone: 'America/New_York' }),
    tipoff: event.status.type.shortDetail?.trim() || event.status.type.detail?.trim() || 'Scheduled',
    provider: '',
    markets: {},
  };

  const odds = competition?.odds?.[0];
  if (!odds) return game;

  game.provider = odds.provider?.name ?? '';
  const spread = parseSpreadMarket(odds, homeAbbrev, awayAbbrev);
  const total = parseTotalMarket(odds);
  const moneyline = parseMoneylineMarket(odds);
  if (spread) game.markets.spread = spread;
  if (total) game.markets.total = total;
  if (moneyline) game.markets.moneyline = moneyline;
  return game;
}

let oddsCache: { data: BettingGame[]; fetchedAt: number } = { data: [], fetchedAt: 0 };

const SCOREBOARD_URL = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard';

// espn rejects date ranges with 400, so each day needs its own request
export function scoreboardUrls(days: number): string[] {
  return Array.from({ length: days }, (_, i) => {
    const date = etIsoDate(i).replace(/-/g, '');
    return `${SCOREBOARD_URL}?dates=${date}`;
  });
}

async function fetchScoreboardDay(url: string): Promise<EspnEvent[]> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 10_000);
  let resp: globalThis.Response;
  try {
    resp = await fetch(url, { signal: controller.signal });
  } finally {
    clearTimeout(timeoutId);
  }

  if (!resp.ok) {
    const err = new Error('ESPN API unavailable') as Error & { espnStatus: number };
    err.espnStatus = resp.status;
    throw err;
  }

  const data = (await resp.json()) as { events?: EspnEvent[] };
  return data.events ?? [];
}

export async function getUpcomingOdds(): Promise<BettingGame[]> {
  if (Date.now() - oddsCache.fetchedAt < ODDS_CACHE_TTL && oddsCache.data.length > 0) {
    return oddsCache.data;
  }

  const results = await Promise.allSettled(
    scoreboardUrls(FUTURE_WINDOW_DAYS + 1).map(fetchScoreboardDay)
  );

  const failures = results.filter((r): r is PromiseRejectedResult => r.status === 'rejected');
  if (failures.length === results.length) {
    throw failures[0].reason;
  }

  const seen = new Set<string>();
  const games: BettingGame[] = [];
  for (const result of results) {
    if (result.status !== 'fulfilled') continue;
    for (const event of result.value) {
      if (event.status?.type?.name !== 'STATUS_SCHEDULED' || seen.has(event.id)) continue;
      seen.add(event.id);
      const game = parseEventOdds(event);
      if (game) games.push(game);
    }
  }

  if (failures.length === 0 && games.length > 0) {
    oddsCache = { data: games, fetchedAt: Date.now() };
  }
  return games;
}

export function computeOddsHash(games: BettingGame[]): string {
  const parts = games
    .map((g) => {
      const s = g.markets.spread;
      const t = g.markets.total;
      const m = g.markets.moneyline;
      return [
        g.espn_event_id,
        s ? `${s.home_line}@${s.home_price ?? 'n'}/${s.away_price ?? 'n'}` : '-',
        t ? `${t.line}@${t.over_price ?? 'n'}/${t.under_price ?? 'n'}` : '-',
        m ? `${m.home}/${m.away}` : '-',
      ].join(':');
    })
    .sort()
    .join('|');
  return crypto.createHash('md5').update(parts).digest('hex');
}
