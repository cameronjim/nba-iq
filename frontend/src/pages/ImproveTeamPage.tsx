import { useEffect, useState, useCallback } from 'react';
import { getWaiverSuggestions } from '../api/client';
import { ChatBox } from '../components/ChatBox';
import { IconRefresh } from '../components/icons';
import { SkeletonLines } from '../components/Skeleton';
import { PreferencesPrompt } from '../components/PreferencesPrompt';
import { getCachedSuggestions, setCachedSuggestions } from '../api/clientCaches';

interface Suggestion {
  name: string;
  reasoning: string;
}

interface ImproveTeamPageProps {
  isLoggedIn: boolean;
}

const SuggestionList = ({
  title,
  items,
  empty,
}: {
  title: string;
  items: Suggestion[];
  empty: string;
}): JSX.Element => (
  <section className="border-t border-base-300 pt-3">
    <h2 className="font-display text-xl font-semibold uppercase tracking-wide mb-1">{title}</h2>
    {items.length === 0 ? (
      <p className="text-sm text-muted py-2">{empty}</p>
    ) : (
      <ul className="divide-y divide-base-300">
        {items.map((item) => (
          <li key={item.name} className="py-2.5">
            <div className="font-semibold text-sm mb-0.5">{item.name}</div>
            <p className="text-xs text-muted leading-relaxed">{item.reasoning}</p>
          </li>
        ))}
      </ul>
    )}
  </section>
);

export const ImproveTeamPage = ({ isLoggedIn }: ImproveTeamPageProps) => {
  const initial = getCachedSuggestions();
  const [tradeTargets, setTradeTargets] = useState<Suggestion[]>(initial?.trade_targets ?? []);
  const [waiverPickups, setWaiverPickups] = useState<Suggestion[]>(initial?.waiver_pickups ?? []);
  const [summary, setSummary] = useState(initial?.summary ?? '');
  const [cachedAt, setCachedAt] = useState<string | null>(initial?.cached_at ?? null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [emptyRoster, setEmptyRoster] = useState(false);

  const loadSuggestions = useCallback(async (refresh = false): Promise<void> => {
    type SuggestionsResponse = Awaited<ReturnType<typeof getWaiverSuggestions>>;
    const apply = (d: SuggestionsResponse): void => {
      setTradeTargets(d.trade_targets || []);
      setWaiverPickups(d.waiver_pickups || []);
      setSummary(d.summary || '');
      setCachedAt(d.cached_at || null);
      setEmptyRoster(!!d.empty_roster);
    };

    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError('');
    let data: SuggestionsResponse;
    try {
      data = await getWaiverSuggestions(refresh);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load suggestions');
      setLoading(false);
      setRefreshing(false);
      return;
    }
    apply(data);
    // never cache the empty-roster response, or a stale one about to be replaced.
    if (!data.empty_roster && !data.stale) {
      setCachedSuggestions(data);
    }
    setLoading(false);
    if (!refresh && data.stale) {
      // the server handed back expired suggestions, so regenerate behind the scenes.
      setRefreshing(true);
      try {
        const fresh = await getWaiverSuggestions(true);
        apply(fresh);
        if (!fresh.empty_roster) setCachedSuggestions(fresh);
      } catch {
      }
    }
    setRefreshing(false);
  }, []);

  useEffect(() => {
    if (!isLoggedIn) return;
    if (initial) return;
    loadSuggestions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoggedIn]);

  const formatCacheTime = (iso: string): string => {
    const d = new Date(iso);
    return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  };

  return (
    <div className="max-w-[1400px] mx-auto px-4 py-6 space-y-5">
      <h1 className="font-display text-3xl font-semibold uppercase tracking-wide">Improve Team</h1>
      {isLoggedIn && <PreferencesPrompt />}
      {isLoggedIn ? (
        <>
          <div className="flex items-center justify-between">
            <div>
              {cachedAt && (
                <span className="text-xs text-muted">Last updated {formatCacheTime(cachedAt)}</span>
              )}
            </div>
            <button
              onClick={() => loadSuggestions(true)}
              disabled={refreshing || loading}
              className="btn btn-ghost btn-xs gap-1.5"
            >
              <IconRefresh size={12} />
              {refreshing ? 'Refreshing' : 'Refresh suggestions'}
            </button>
          </div>

          {loading ? (
            <div className="space-y-5" role="status" aria-label="Loading suggestions">
              <p className="text-sm text-muted">Claude is reading your roster. This can take a few seconds.</p>
              <SkeletonLines lines={3} label="Loading roster summary" />
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                <SkeletonLines lines={5} label="Loading trade targets" />
                <SkeletonLines lines={5} label="Loading waiver pickups" />
              </div>
            </div>
          ) : error ? (
            <div className="border border-base-300 flex flex-col items-center py-16 gap-4">
              <p className="text-error text-sm">{error}</p>
              <button onClick={() => loadSuggestions()} className="btn btn-primary btn-sm">Try again</button>
            </div>
          ) : emptyRoster ? (
            <div className="border border-base-300 flex flex-col items-center py-16 gap-2 text-center">
              <p className="font-semibold text-sm">Add players to your team first</p>
              <p className="text-xs text-muted max-w-xs">
                Go to <span className="font-medium">My Team</span> and add a few players. Trade and waiver suggestions are built around your roster&apos;s weak categories.
              </p>
            </div>
          ) : (
            <>
              {summary && (
                <section className="border-t border-base-300 pt-3">
                  <h2 className="font-display text-xl font-semibold uppercase tracking-wide mb-1">Claude&apos;s read on your roster</h2>
                  <p className="text-sm leading-relaxed">{summary}</p>
                </section>
              )}

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-8 gap-y-5">
                <SuggestionList
                  title="Trade targets"
                  items={tradeTargets}
                  empty="No trade targets found. Add players to your roster first."
                />
                <SuggestionList
                  title="Waiver pickups"
                  items={waiverPickups}
                  empty="No waiver suggestions found. Add players to your roster first."
                />
              </div>
            </>
          )}
        </>
      ) : (
        <div className="border border-base-300 flex flex-col items-center py-16 gap-2 text-center">
          <p className="font-semibold">Sign in to get suggestions</p>
          <p className="text-sm text-muted">Use the Sign In button in the top right to get trade targets, waiver pickups, and to ask Claude questions.</p>
        </div>
      )}

      <ChatBox contextType="waiver" isLoggedIn={isLoggedIn} />
    </div>
  );
};
