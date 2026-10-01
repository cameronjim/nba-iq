import type { TeamAnalysis } from '../types';

// invalidate on roster or prefs mutations: the server keys its own cache by roster and prefs hash.

interface CacheEntry<T> {
  data: T;
  fetchedAt: number;
}

interface Suggestions {
  trade_targets: Array<{ name: string; reasoning: string }>;
  waiver_pickups: Array<{ name: string; reasoning: string }>;
  summary: string;
  cached?: boolean;
  cached_at?: string;
}

let analysis: CacheEntry<TeamAnalysis> | null = null;
let suggestions: CacheEntry<Suggestions> | null = null;

const TTL_MS = 30 * 60_000;

function isFresh<T>(entry: CacheEntry<T> | null): boolean {
  return !!entry && Date.now() - entry.fetchedAt < TTL_MS;
}

export function getCachedAnalysis(): TeamAnalysis | null {
  return isFresh(analysis) ? analysis!.data : null;
}

export function setCachedAnalysis(data: TeamAnalysis): void {
  analysis = { data, fetchedAt: Date.now() };
}

export function getCachedSuggestions(): Suggestions | null {
  return isFresh(suggestions) ? suggestions!.data : null;
}

export function setCachedSuggestions(data: Suggestions): void {
  suggestions = { data, fetchedAt: Date.now() };
}

export function invalidateAIClientCaches(): void {
  analysis = null;
  suggestions = null;
}
