import axios from 'axios';
import type {
  Player, Team, Game, RosterPlayer, ChatMessage, TeamAnalysis,
  BettingGame, BettingPicksResponse, Bet, NewBet, LedgerSummary, BetStatus,
  PlayerSeasonRow, TeamSeasonRow,
  Rating2kSummary, Rating2kDetail, Rating2kTeamType,
  PlayerAnalytics, PlayerPredictionsResponse, SlateResponse, SlateSort, WatchlistResponse,
  WatchlistPositionFilter, WeeklyOutlookResponse,
} from '../types';

const BASE_URL = import.meta.env.VITE_API_URL
  ? `${import.meta.env.VITE_API_URL}/api`
  : '/api';

const api = axios.create({ baseURL: BASE_URL, timeout: 120000 });

let authToken: string | null = localStorage.getItem('auth_token');

api.interceptors.request.use((config) => {
  if (authToken) config.headers.Authorization = `Bearer ${authToken}`;
  return config;
});

export function setAuthToken(token: string | null): void {
  authToken = token;
  if (token) localStorage.setItem('auth_token', token);
  else localStorage.removeItem('auth_token');
}

export function getAuthToken(): string | null {
  return authToken;
}

export async function login(username: string, password: string): Promise<void> {
  const { data } = await api.post('/auth/login', { username, password });
  setAuthToken(data.token);
}

export async function register(username: string, email: string, password: string): Promise<void> {
  const { data } = await api.post('/auth/register', { username, email, password });
  setAuthToken(data.token);
}

export async function googleSignIn(credential: string): Promise<void> {
  const { data } = await api.post('/auth/google', { credential });
  setAuthToken(data.token);
}

export async function googleSignInWithToken(accessToken: string): Promise<void> {
  const { data } = await api.post('/auth/google', { access_token: accessToken });
  setAuthToken(data.token);
}

export async function changePassword(
  currentPassword: string | null,
  newPassword: string
): Promise<void> {
  const body: { newPassword: string; currentPassword?: string } = { newPassword };
  if (currentPassword) body.currentPassword = currentPassword;
  await api.patch('/auth/change-password', body);
}

export async function forgotPassword(email: string): Promise<{ message: string }> {
  const { data } = await api.post('/auth/forgot-password', { email });
  return data;
}

export async function resetPassword(token: string, newPassword: string): Promise<void> {
  await api.post('/auth/reset-password', { token, newPassword });
}

export interface CurrentUser {
  id: number;
  username: string;
  email: string | null;
  name: string | null;
  phone: string | null;
  has_password: boolean;
  is_admin: boolean;
}

export async function getCurrentUser(): Promise<CurrentUser> {
  const { data } = await api.get('/auth/me');
  return data;
}

export interface ProfileUpdate {
  username?: string;
  name?: string;
  email?: string;
  phone?: string;
}

export async function updateProfile(updates: ProfileUpdate): Promise<CurrentUser> {
  const { data } = await api.patch('/auth/profile', updates);
  return data;
}

export interface BettingPreferences {
  risk_appetite?: 'conservative' | 'balanced' | 'aggressive';
  preferred_markets?: Array<'spread' | 'total' | 'moneyline' | 'parlay'>;
  extra_notes?: string;
}

export interface AIPreferences {
  risk_tolerance?: 'avoid_injured' | 'balanced' | 'high_upside';
  player_age_pref?: 'veterans' | 'balanced' | 'young_upside';
  opportunity_chase?: 'yes' | 'no';
  league_format?: 'h2h_categories' | 'h2h_points' | 'roto' | 'points';
  league_size?: number;
  punt_categories?: string[];
  priority_categories?: string[];
  roster_strategy?: 'stars_scrubs' | 'balanced' | 'streaming';
  trade_activity?: 'active' | 'occasional' | 'set_forget';
  schedule_weight?: 'matters_a_lot' | 'somewhat' | 'ignore';
  rookie_hunger?: 'love_them' | 'mixed' | 'avoid';
  playoff_focus?: 'yes' | 'no';
  bench_philosophy?: 'high_upside_stash' | 'safe_role_players' | 'streaming_slots';
  position_needs?: string[];
  extra_notes?: string;
  betting?: BettingPreferences;
}

export async function getPreferences(): Promise<AIPreferences> {
  const { data } = await api.get('/preferences');
  return data;
}

export async function updatePreferences(prefs: AIPreferences): Promise<AIPreferences> {
  const { data } = await api.patch('/preferences', prefs);
  return data;
}

export interface DataStatus {
  players_updated_at: string | null;
  teams_updated_at: string | null;
  games_updated_at: string | null;
}

export async function getDataStatus(): Promise<DataStatus> {
  const { data } = await api.get('/status');
  return data;
}

export async function getPlayers(params?: { search?: string; team?: string; position?: string }): Promise<Player[]> {
  const { data } = await api.get('/players', { params });
  return data;
}

export async function getPlayer(id: number): Promise<Player> {
  const { data } = await api.get(`/players/${id}`);
  return data;
}

export async function getPlayerAnalytics(id: number): Promise<PlayerAnalytics> {
  const { data } = await api.get<PlayerAnalytics>(`/players/${id}/analytics`);
  return {
    ...data,
    percentiles: data.percentiles ?? [],
    distributions: data.distributions ?? [],
    trends: {
      games: data.trends?.games ?? [],
      rolling: data.trends?.rolling ?? [],
      last10_vs_season: data.trends?.last10_vs_season ?? [],
    },
    prediction: data.prediction ?? null,
  };
}

export interface PlayerPredictionsParams {
  from?: string;
  limit?: number;
}

// omitting `from` asks the server for every predicted game, not for today onwards.
export async function getPlayerPredictions(
  id: number,
  params: PlayerPredictionsParams = {}
): Promise<PlayerPredictionsResponse> {
  const { data } = await api.get<PlayerPredictionsResponse>(`/players/${id}/predictions`, {
    params,
  });
  return {
    ...data,
    run: data.run ?? null,
    stats: data.stats ?? [],
    games: (data.games ?? []).map((game) => ({ ...game, stats: game.stats ?? {} })),
  };
}

// the default sort is omitted so the common request stays one cacheable url.
export function slateParams(date?: string, sort?: SlateSort): Record<string, string> {
  return {
    ...(date ? { date } : {}),
    ...(sort && sort !== 'impact' ? { sort } : {}),
  };
}

export function normalizeSlate(data: Partial<SlateResponse>, sort: SlateSort): SlateResponse {
  return {
    date: data.date ?? '',
    sort: data.sort ?? sort,
    run: data.run ?? null,
    pool: data.pool ?? { key: '', label: '', definition: '', sample_size: 0 },
    baseline: data.baseline ?? EMPTY_BASELINE,
    games: (data.games ?? []).map((game) => ({
      ...game,
      top_edge: game.top_edge ?? null,
      players: (game.players ?? []).map((player) => ({
        ...player,
        edge: player.edge ?? null,
        vs_usual: player.vs_usual ?? null,
        reasons: player.reasons ?? [],
        evidence: player.evidence ?? {},
      })),
    })),
  };
}

export async function getSlate(date?: string, sort: SlateSort = 'impact'): Promise<SlateResponse> {
  const { data } = await api.get<SlateResponse>('/predictions/slate', {
    params: slateParams(date, sort),
  });
  return normalizeSlate(data ?? {}, sort);
}

// pages read an empty `definition` as "no baseline", so the zeroes are never used as thresholds.
const EMPTY_BASELINE = {
  window_games: 0,
  min_games: 0,
  notable_min_delta: 0,
  label: '',
  definition: '',
};

const WATCHLIST_POSITION_OPTIONS: WatchlistPositionFilter[] = [
  'G',
  'F',
  'C',
  'PG',
  'SG',
  'SF',
  'PF',
];

// defaults are omitted rather than sent, so the common request stays one cacheable url.
export function watchlistParams(
  date?: string,
  days?: number,
  position?: WatchlistPositionFilter | null
): Record<string, string | number> {
  return {
    ...(date ? { date } : {}),
    ...(days && days > 1 ? { days } : {}),
    ...(position ? { position } : {}),
  };
}

export function normalizeWatchlist(data: Partial<WatchlistResponse>): WatchlistResponse {
  const date = data.date ?? '';
  const from = data.window?.from ?? date;
  return {
    date,
    window: data.window ?? { from, to: from, days: 1 },
    run: data.run ?? null,
    pool: data.pool ?? { key: '', label: '', definition: '', sample_size: 0 },
    baseline: data.baseline ?? EMPTY_BASELINE,
    position: data.position ?? null,
    position_options: data.position_options ?? WATCHLIST_POSITION_OPTIONS,
    position_coverage: data.position_coverage ?? { known: 0, unknown: 0 },
    players: (data.players ?? []).map((player) => ({
      ...player,
      position: player.position ?? null,
      games_count: player.games_count ?? 1,
      games: player.games ?? [],
      score_per_game: player.score_per_game ?? player.score,
      totals: player.totals ?? {},
      reasons: player.reasons ?? [],
      drivers: player.drivers ?? [],
      evidence: player.evidence ?? {},
    })),
  };
}

export async function getWatchlist(
  date?: string,
  days?: number,
  position?: WatchlistPositionFilter | null
): Promise<WatchlistResponse> {
  const { data } = await api.get<WatchlistResponse>('/watchlist', {
    params: watchlistParams(date, days, position),
  });
  return normalizeWatchlist(data ?? {});
}

export async function getTeams(): Promise<Team[]> {
  const { data } = await api.get('/teams');
  return data;
}

export async function getTeam(id: number): Promise<Team> {
  const { data } = await api.get(`/teams/${id}`);
  return data;
}

export async function getGames(): Promise<Game[]> {
  const { data } = await api.get('/games');
  return data;
}

export async function getLiveGames(): Promise<Game[]> {
  const { data } = await api.get('/games/live');
  return data;
}

export interface HistoryPlayersParams {
  season: string;
  search?: string;
  limit?: number;
  offset?: number;
}

export interface HistoryPlayersResponse {
  season: string;
  total: number;
  players: PlayerSeasonRow[];
}

export interface PlayerCareerResponse {
  nba_player_id: string;
  player_name: string;
  seasons: PlayerSeasonRow[];
}

export async function getHistorySeasons(): Promise<string[]> {
  const { data } = await api.get<{ seasons: string[] }>('/history/seasons');
  return data.seasons ?? [];
}

export async function getHistoryPlayers(params: HistoryPlayersParams): Promise<HistoryPlayersResponse> {
  const { data } = await api.get<HistoryPlayersResponse>('/history/players', { params });
  return { season: data.season, total: data.total ?? 0, players: data.players ?? [] };
}

export async function getPlayerCareer(nbaPlayerId: string): Promise<PlayerCareerResponse> {
  const { data } = await api.get<PlayerCareerResponse>(`/history/players/${nbaPlayerId}/seasons`);
  return { ...data, seasons: data.seasons ?? [] };
}

export async function getHistoryTeams(season: string): Promise<TeamSeasonRow[]> {
  const { data } = await api.get<{ season: string; teams: TeamSeasonRow[] }>('/history/teams', {
    params: { season },
  });
  return data.teams ?? [];
}

export type Ratings2kSort = 'overall' | 'name';

export interface Ratings2kPlayersParams {
  teamType: Rating2kTeamType;
  search?: string;
  limit?: number;
  offset?: number;
  sort?: Ratings2kSort;
}

export interface Ratings2kPlayersResponse {
  total: number;
  players: Rating2kSummary[];
}

export async function getRatings2kPlayers(
  params: Ratings2kPlayersParams
): Promise<Ratings2kPlayersResponse> {
  const { data } = await api.get<Ratings2kPlayersResponse>('/ratings2k/players', { params });
  return { total: data.total ?? 0, players: data.players ?? [] };
}

export async function getRatings2kPlayer(slug: string): Promise<Rating2kDetail | null> {
  try {
    const { data } = await api.get<Rating2kDetail>(
      `/ratings2k/players/${encodeURIComponent(slug)}`
    );
    return {
      player: data.player,
      attributes: data.attributes ?? [],
      badges: data.badges ?? [],
      rating_history: data.rating_history ?? [],
    };
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 404) return null;
    throw err;
  }
}

// 2K publishes no nba ids, so this is a name match and null is a normal answer.
export async function getRatings2kByName(name: string): Promise<Rating2kSummary | null> {
  const { data } = await api.get<{ player: Rating2kSummary | null }>('/ratings2k/by-player-name', {
    params: { name },
  });
  return data.player ?? null;
}

export async function getMyRoster(): Promise<RosterPlayer[]> {
  const { data } = await api.get('/fantasy/roster');
  return data;
}

export async function getWeeklyOutlook(
  start?: string,
  days?: number
): Promise<WeeklyOutlookResponse> {
  const params: Record<string, string | number> = {};
  if (start) params.start = start;
  if (days !== undefined) params.days = days;
  const { data } = await api.get<WeeklyOutlookResponse>('/fantasy/weekly-outlook', { params });
  return {
    ...data,
    categories: data.categories ?? [],
    players: data.players ?? [],
  };
}

export async function addToRoster(playerId: number): Promise<void> {
  await api.post('/fantasy/roster', { player_id: playerId });
}

export async function dropFromRoster(playerId: number): Promise<void> {
  await api.delete(`/fantasy/roster/${playerId}`);
}

export async function chatWithAI(message: string, contextType?: string, history?: ChatMessage[]): Promise<{ reply: string }> {
  const { data } = await api.post('/ai/chat', { message, context_type: contextType, history });
  return data;
}

export async function getTeamAnalysis(refresh?: boolean): Promise<TeamAnalysis> {
  const { data } = await api.get('/ai/team-analysis', { params: refresh ? { refresh: 'true' } : {} });
  return data;
}

export async function getWaiverSuggestions(refresh?: boolean): Promise<{ trade_targets: Array<{name: string; reasoning: string}>; waiver_pickups: Array<{name: string; reasoning: string}>; summary: string; cached?: boolean; cached_at?: string; empty_roster?: boolean; stale?: boolean }> {
  const { data } = await api.get('/ai/waiver-suggestions', { params: refresh ? { refresh: 'true' } : {} });
  return data;
}

export async function getBettingOdds(): Promise<{ games: BettingGame[]; fetched_at: string }> {
  const { data } = await api.get('/betting/odds');
  return data;
}

export async function getBettingPicks(refresh?: boolean): Promise<BettingPicksResponse> {
  const { data } = await api.get('/betting/picks', { params: refresh ? { refresh: 'true' } : {} });
  return data;
}

export async function getBets(): Promise<{ bets: Bet[]; summary: LedgerSummary }> {
  const { data } = await api.get('/betting/bets');
  return data;
}

export async function createBet(bet: NewBet): Promise<Bet> {
  const { data } = await api.post('/betting/bets', bet);
  return data;
}

export async function settleBetStatus(id: number, status: BetStatus): Promise<Bet> {
  const { data } = await api.patch(`/betting/bets/${id}`, { status });
  return data;
}

export async function deleteBet(id: number): Promise<void> {
  await api.delete(`/betting/bets/${id}`);
}

export function trackPageView(path: string, referrer?: string): void {
  api.post('/track/pageview', referrer ? { path, referrer } : { path }).catch(() => {});
}

export interface AdminUser {
  id: number;
  username: string;
  email: string | null;
  name: string | null;
  is_admin: boolean;
  created_at: string;
  has_password: boolean;
  has_google: boolean;
  roster_count: number;
  last_seen: string | null;
}

export interface AdminStats {
  totals: {
    total_users: number;
    new_users_7d: number;
    views_24h: number;
    views_7d: number;
    active_users_24h: number;
  };
  top_paths: Array<{ path: string; views: number }>;
}

export interface AdminPageView {
  id: number;
  path: string;
  referrer: string | null;
  user_agent: string | null;
  created_at: string;
  username: string | null;
}

export async function getAdminUsers(): Promise<AdminUser[]> {
  const { data } = await api.get('/admin/users');
  return data;
}

export async function getAdminStats(): Promise<AdminStats> {
  const { data } = await api.get('/admin/stats');
  return data;
}

export async function getAdminViews(): Promise<AdminPageView[]> {
  const { data } = await api.get('/admin/views');
  return data;
}
