import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import {
  getMyRoster, getPlayers, addToRoster, dropFromRoster, getTeamAnalysis, getStartSit, getStreamers,
} from '../api/client';
import type { Player, RosterPlayer, Streamer, TeamAnalysis } from '../types';
import { getTeamLogoUrl } from '../utils/teamLogos';
import { IconSearch, IconPlus, IconTrash, IconRefresh, IconChevronUp, IconChevronDown } from '../components/icons';
import { SkeletonLines, SkeletonTable } from '../components/Skeleton';
import { PreferencesPrompt } from '../components/PreferencesPrompt';
import { Toast, type ToastVariant } from '../components/Toast';
import { computeRosterAverages, formatAvg, AVG_CATEGORIES } from '../components/TeamAverages';
import {
  getCachedAnalysis,
  setCachedAnalysis,
  invalidateAIClientCaches,
} from '../api/clientCaches';
import { getCached, setCached, CACHE_KEYS } from '../api/resourceCache';
import { WeeklyOutlookCard } from '../components/fantasy/WeeklyOutlookCard';
import { useWeeklyOutlook } from '../hooks/useWeeklyOutlook';
import { LineupCard } from '../components/fantasy/LineupCard';
import { StreamingPickupsCard } from '../components/fantasy/StreamingPickupsCard';
import { TradeCheckCard } from '../components/fantasy/TradeCheckCard';
import { useRosterResource } from '../hooks/useRosterResource';
import { useTradeCheck } from '../hooks/useTradeCheck';

const CAT_COLORS: Record<string, string> = {
  strong: 'text-success',
  average: 'text-warning',
  weak: 'text-error',
};

const ROSTER_COLUMNS: Array<{ key: keyof RosterPlayer | null; label: string; full: string }> = [
  { key: 'name',                     label: 'Player', full: 'Player Name' },
  { key: 'position',                 label: 'Pos',    full: 'Position' },
  { key: 'team',                     label: 'Team',   full: 'Team' },
  { key: 'points_per_game',          label: 'PTS',    full: 'Points Per Game' },
  { key: 'rebounds_per_game',        label: 'REB',    full: 'Rebounds Per Game' },
  { key: 'assists_per_game',         label: 'AST',    full: 'Assists Per Game' },
  { key: 'steals_per_game',          label: 'STL',    full: 'Steals Per Game' },
  { key: 'blocks_per_game',          label: 'BLK',    full: 'Blocks Per Game' },
  { key: 'field_goal_percentage',    label: 'FG%',    full: 'Field Goal %' },
  { key: 'free_throw_percentage',    label: 'FT%',    full: 'Free Throw %' },
  { key: 'three_pointers_made',      label: '3PM',    full: '3-Pointers Made Per Game' },
  { key: 'turnovers_per_game',       label: 'TO',     full: 'Turnovers Per Game' },
];

const NUMERIC_ROSTER_KEYS = new Set<keyof RosterPlayer>([
  'points_per_game', 'rebounds_per_game', 'assists_per_game', 'steals_per_game',
  'blocks_per_game', 'field_goal_percentage', 'free_throw_percentage',
  'three_pointers_made', 'turnovers_per_game',
]);

interface FantasyPageProps {
  isLoggedIn: boolean;
}

export const FantasyPage = ({ isLoggedIn }: FantasyPageProps) => {
  const [roster, setRoster] = useState<RosterPlayer[]>(
    () => getCached<RosterPlayer[]>(CACHE_KEYS.roster) ?? []
  );
  const [search, setSearch] = useState('');
  const [searchResults, setSearchResults] = useState<Player[]>([]);
  const [searching, setSearching] = useState(false);
  const [analysis, setAnalysis] = useState<TeamAnalysis | null>(getCachedAnalysis);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [rosterLoading, setRosterLoading] = useState(
    () => getCached<RosterPlayer[]>(CACHE_KEYS.roster) === null
  );
  const [toast, setToast] = useState<{ message: string; variant: ToastVariant } | null>(null);
  const [sortKey, setSortKey] = useState<keyof RosterPlayer>('points_per_game');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [addingStreamerId, setAddingStreamerId] = useState<number | null>(null);
  // each loadAnalysis captures this id and commits only while it is still the latest.
  const analysisRequestIdRef = useRef(0);

  const loadRoster = useCallback(async (): Promise<void> => {
    try {
      const data = await getMyRoster();
      setCached(CACHE_KEYS.roster, data);
      setRoster(data);
    } catch {
      setRoster([]);
    } finally {
      setRosterLoading(false);
    }
  }, []);

  // includes optimistic states, so a tab switch mid-mutation cannot resurrect a dropped player.
  useEffect(() => {
    if (isLoggedIn && !rosterLoading) setCached(CACHE_KEYS.roster, roster);
  }, [roster, rosterLoading, isLoggedIn]);

  const loadAnalysis = useCallback(async (): Promise<void> => {
    const requestId = ++analysisRequestIdRef.current;
    setAnalysisLoading(true);
    try {
      const data = await getTeamAnalysis();
      if (requestId !== analysisRequestIdRef.current) return;
      setAnalysis(data);
      if (!data.stale) {
        setCachedAnalysis(data);
        return;
      }
    } catch {
      if (requestId !== analysisRequestIdRef.current) return;
      setAnalysis(null);
      return;
    } finally {
      if (requestId === analysisRequestIdRef.current) {
        setAnalysisLoading(false);
      }
    }
    // the server handed back the previous roster's analysis, so regenerate quietly.
    try {
      const fresh = await getTeamAnalysis(true);
      if (requestId !== analysisRequestIdRef.current) return;
      setAnalysis(fresh);
      setCachedAnalysis(fresh);
    } catch {
    }
  }, []);

  useEffect(() => { if (isLoggedIn) loadRoster(); }, [isLoggedIn, loadRoster]);

  useEffect(() => {
    if (!isLoggedIn || roster.length === 0) return;
    if (analysis) return; // already showing cached result
    loadAnalysis();
  }, [isLoggedIn, roster.length, analysis, loadAnalysis]);

  useEffect(() => {
    if (!search.trim()) { setSearchResults([]); return; }
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        const results = await getPlayers({ search: search.trim() });
        const rosterIds = new Set(roster.map((r) => r.player_id || r.id));
        setSearchResults(results.filter((p) => !rosterIds.has(p.id)).slice(0, 10));
      } catch {
        setSearchResults([]);
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [search, roster]);

  // add and drop apply optimistically, restoring the previous roster on failure.
  const handleAdd = (player: Player): void => {
    // a negative roster_id marks the optimistic row until the silent reload swaps it.
    const optimistic: RosterPlayer = {
      ...player,
      roster_id: -player.id,
      player_id: player.id,
      added_at: new Date().toISOString(),
    };
    setRoster((prev) => [...prev, optimistic]);
    setSearch('');
    setSearchResults([]);
    analysisRequestIdRef.current++;
    setAnalysis(null);
    invalidateAIClientCaches();
    setToast({ message: `Added ${player.name} to your team`, variant: 'success' });

    void addToRoster(player.id)
      .then(() => loadRoster())
      .catch(() => {
        setRoster((prev) => prev.filter((p) => p.player_id !== player.id));
        setToast({ message: `Couldn't add ${player.name}`, variant: 'error' });
      });
  };

  const handleAddStreamer = (streamer: Streamer): void => {
    setAddingStreamerId(streamer.id);
    void addToRoster(streamer.id)
      .then(() => {
        analysisRequestIdRef.current++;
        setAnalysis(null);
        invalidateAIClientCaches();
        setToast({ message: `Added ${streamer.name} to your team`, variant: 'success' });
        return loadRoster();
      })
      .catch(() => {
        setToast({ message: `Couldn't add ${streamer.name}`, variant: 'error' });
      })
      .finally(() => setAddingStreamerId(null));
  };

  const handleDrop = (playerId: number, playerName: string): void => {
    const previousRoster = roster;
    setRoster((prev) => prev.filter((p) => (p.player_id || p.id) !== playerId));
    analysisRequestIdRef.current++;
    setAnalysis(null);
    invalidateAIClientCaches();
    setToast({ message: `Dropped ${playerName} from your team`, variant: 'success' });

    void dropFromRoster(playerId).catch(() => {
      setRoster(previousRoster);
      setToast({ message: `Couldn't drop ${playerName}`, variant: 'error' });
    });
  };

  const n = (v: unknown): number => Number(v) || 0;

  const handleSort = (key: keyof RosterPlayer): void => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      // strings default to A->Z, numbers to high-to-low.
      setSortDir(NUMERIC_ROSTER_KEYS.has(key) ? 'desc' : 'asc');
    }
  };

  const rosterAverages = useMemo(() => computeRosterAverages(roster), [roster]);

  // keyed on player ids so an optimistic row and its reloaded twin do not trigger two simulations.
  const rosterKey = useMemo(
    () => roster.map((p) => p.player_id || p.id).sort((a, b) => a - b).join(','),
    [roster]
  );
  const decisionsEnabled = isLoggedIn && !rosterLoading && roster.length > 0;
  const { state: outlookState, reload: reloadOutlook } = useWeeklyOutlook(decisionsEnabled, rosterKey);
  const { state: lineupState, reload: reloadLineup } = useRosterResource(
    getStartSit, decisionsEnabled, rosterKey, "Could not load this week's lineup."
  );
  const { state: streamersState, reload: reloadStreamers } = useRosterResource(
    getStreamers, decisionsEnabled, rosterKey, 'Could not load streaming pickups.'
  );
  const { state: tradeState, check: checkTrade } = useTradeCheck();
  const tradeRoster = useMemo(
    () => roster.map((p) => ({ id: p.player_id || p.id, name: p.name })),
    [roster]
  );

  const sortedRoster = useMemo(() => {
    return [...roster].sort((a, b) => {
      const aVal = a[sortKey];
      const bVal = b[sortKey];
      if (aVal == null) return 1;
      if (bVal == null) return -1;
      if (NUMERIC_ROSTER_KEYS.has(sortKey)) {
        const diff = Number(aVal) - Number(bVal);
        return sortDir === 'asc' ? diff : -diff;
      }
      return sortDir === 'asc'
        ? String(aVal).localeCompare(String(bVal))
        : String(bVal).localeCompare(String(aVal));
    });
  }, [roster, sortKey, sortDir]);

  const injuryBadge = (status: string | null): JSX.Element | null => {
    if (!status) return null;
    const cls = status === 'Out' ? 'text-error'
      : ['Day-To-Day', 'Day_To_Day', 'Questionable'].includes(status) ? 'text-warning'
      : status === 'Probable' ? 'text-success'
      : 'text-error';
    return <span className={`text-[11px] font-semibold uppercase ml-2 ${cls}`}>{status.replace(/_/g, ' ')}</span>;
  };

  return (
    <div className="max-w-[1400px] mx-auto px-4 py-6 space-y-5">
      {isLoggedIn && <PreferencesPrompt />}
      {isLoggedIn && (
        <div>
          <div>
            <label className="input input-bordered flex items-center gap-2">
              <IconSearch size={16} className="text-muted" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search players to add to your team..."
                className="grow"
              />
              {searching && <span className="text-xs text-muted">Searching</span>}
            </label>

            {searchResults.length > 0 && (
              <div className="mt-2 space-y-1 max-h-[300px] overflow-y-auto">
                {searchResults.map((player) => (
                  <div key={player.id} className="flex items-center justify-between px-3 py-2 border-b border-base-300 hover:bg-base-200">
                    <div className="flex items-center gap-3">
                      <div className="avatar">
                        <div className="w-7 rounded-box">
                          <img
                            src={player.headshot_url || ''}
                            alt=""
                            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                          />
                        </div>
                      </div>
                      <span className="text-sm font-medium">{player.name}</span>
                      <span className="text-xs text-muted">{player.position} · {player.team}</span>
                      <span className="text-xs text-muted">{n(player.points_per_game).toFixed(1)} PPG</span>
                    </div>
                    <button onClick={() => handleAdd(player)} className="btn btn-primary btn-xs gap-1">
                      <IconPlus size={12} /> Add
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      <section>
        <div>
          <div className="pb-2">
            <h1 className="font-display text-3xl font-semibold uppercase tracking-wide">My Roster <span className="font-sans text-base normal-case tracking-normal font-normal text-muted tabular-nums">({roster.length} players)</span></h1>
          </div>

          {!isLoggedIn ? (
            <div className="text-center p-12">
              <p className="font-semibold text-sm mb-1">Sign in to use My Team</p>
              <p className="text-muted text-xs">Use the Sign In button in the top right to manage your roster.</p>
            </div>
          ) : rosterLoading ? (
            <SkeletonTable rows={8} cols={8} label="Loading roster" />
          ) : roster.length === 0 ? (
            <div className="text-center p-12">
              <p className="text-muted text-sm">No players on your roster yet</p>
              <p className="text-faint text-xs mt-1">Use the search bar above to add players</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="table table-sm">
                <thead>
                  <tr>
                    {ROSTER_COLUMNS.map((col) => (
                      <th
                        key={col.label}
                        onClick={col.key ? () => handleSort(col.key!) : undefined}
                        title={col.full}
                        className={col.key ? 'cursor-pointer select-none whitespace-nowrap' : 'whitespace-nowrap'}
                      >
                        <span className="inline-flex items-center gap-1">
                          {col.label}
                          {col.key && sortKey === col.key
                            ? (sortDir === 'asc' ? <IconChevronUp size={12} /> : <IconChevronDown size={12} />)
                            : <IconChevronUp size={12} className="invisible" />}
                        </span>
                      </th>
                    ))}
                    {isLoggedIn && <th />}
                  </tr>
                </thead>
                <tbody>
                  {sortedRoster.map((p) => (
                    <tr key={`player-${p.id}`} className="hover">
                      <td className="font-medium whitespace-nowrap">
                        <span className="flex items-center gap-2">
                          <div className="avatar">
                            <div className="w-6 rounded-box">
                              <img
                                src={p.headshot_url || ''}
                                alt=""
                                onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                              />
                            </div>
                          </div>
                          {p.name}{injuryBadge(p.injury_status)}
                        </span>
                      </td>
                      <td>{p.position}</td>
                      <td>
                        <span className="flex items-center gap-1.5">
                          {(() => {
                            const logo = getTeamLogoUrl(p.team);
                            return logo ? (
                              <img
                                src={logo}
                                alt=""
                                className="w-4 h-4 object-contain"
                                onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                              />
                            ) : null;
                          })()}
                          {p.team}
                        </span>
                      </td>
                      <td>{n(p.points_per_game).toFixed(1)}</td>
                      <td>{n(p.rebounds_per_game).toFixed(1)}</td>
                      <td>{n(p.assists_per_game).toFixed(1)}</td>
                      <td>{n(p.steals_per_game).toFixed(1)}</td>
                      <td>{n(p.blocks_per_game).toFixed(1)}</td>
                      <td>{n(p.field_goal_percentage).toFixed(1)}%</td>
                      <td>{n(p.free_throw_percentage).toFixed(1)}%</td>
                      <td>{n(p.three_pointers_made).toFixed(1)}</td>
                      <td>{n(p.turnovers_per_game).toFixed(1)}</td>
                      {isLoggedIn && (
                        <td>
                          <button
                            onClick={() => handleDrop(p.player_id || p.id, p.name)}
                            className="btn btn-ghost btn-xs gap-1 text-error"
                          >
                            <IconTrash size={12} /> Drop
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                  {/* kept inside <tbody>: daisyUI's <tfoot> css bolds and shrinks
                      its cells, which would make this row stand out wrong. */}
                  <tr className="bg-base-200">
                    <td className="font-medium whitespace-nowrap text-xs">
                      {/* w-6 h-6 matches the avatar circles above, so "AVG" aligns with
                          the names and the row keeps the same height as body rows. */}
                      <span className="flex items-center gap-2">
                        <span className="w-6 h-6 flex-shrink-0" />
                        AVG
                      </span>
                    </td>
                    <td className="text-xs" />
                    <td className="text-xs" />
                    {AVG_CATEGORIES.map((cat) => (
                      <td key={cat.key as string} className="text-xs">
                        {formatAvg(rosterAverages[cat.key as string], cat)}
                      </td>
                    ))}
                    {isLoggedIn && <td />}
                  </tr>
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      {isLoggedIn && roster.length > 0 && (
        <WeeklyOutlookCard state={outlookState} onReload={reloadOutlook} />
      )}

      {isLoggedIn && roster.length > 0 && (
        <LineupCard state={lineupState} onReload={reloadLineup} />
      )}

      {isLoggedIn && roster.length > 0 && (
        <StreamingPickupsCard
          state={streamersState}
          onReload={reloadStreamers}
          onAdd={handleAddStreamer}
          addingId={addingStreamerId}
        />
      )}

      {isLoggedIn && roster.length > 0 && (
        <TradeCheckCard roster={tradeRoster} state={tradeState} onCheck={checkTrade} />
      )}

      {isLoggedIn && roster.length > 0 && (
        <section className="border-t border-base-300 pt-3">
          <div className="pb-2 flex items-center justify-between">
            <h2 className="font-display text-xl font-semibold uppercase tracking-wide">
              Claude&apos;s read on your roster
            </h2>
            <button
              onClick={loadAnalysis}
              disabled={analysisLoading}
              className="btn btn-ghost btn-xs gap-1.5"
            >
              <IconRefresh size={12} />
              {analysisLoading ? 'Reading' : 'Refresh'}
            </button>
          </div>

          {analysisLoading && !analysis ? (
            <div className="space-y-3" role="status" aria-label="Loading roster analysis">
              <p className="text-sm text-muted">Claude is reading your roster. This can take a few seconds.</p>
              <SkeletonLines lines={2} label="Loading category ratings" />
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <SkeletonLines lines={4} label="Loading strengths" />
                <SkeletonLines lines={4} label="Loading weaknesses" />
                <SkeletonLines lines={4} label="Loading suggestions" />
              </div>
            </div>
          ) : analysis?.categories ? (
            <div>
              <ul className="flex flex-wrap gap-x-5 gap-y-1 mb-4 border-y border-base-300 py-2">
                {Object.entries(analysis.categories).map(([cat, rating]) => (
                  <li key={cat} className="text-sm">
                    <span className="font-semibold">{cat}</span>{' '}
                    <span className={`capitalize ${CAT_COLORS[rating] ?? 'text-warning'}`}>{rating}</span>
                  </li>
                ))}
              </ul>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                {[
                  { title: 'Strengths', items: analysis.strengths },
                  { title: 'Weaknesses', items: analysis.weaknesses },
                  { title: 'Suggestions', items: analysis.suggestions },
                ].map((section) => (
                  <div key={section.title}>
                    <h3 className="text-sm font-semibold border-b border-base-300 pb-1 mb-1">{section.title}</h3>
                    <ul className="divide-y divide-base-300">
                      {section.items?.map((item) => (
                        <li key={item} className="py-1.5 text-xs leading-relaxed">{item}</li>
                      ))}
                      {(!section.items || section.items.length === 0) && (
                        <li className="py-1.5 text-xs text-faint">None identified</li>
                      )}
                    </ul>
                  </div>
                ))}
              </div>
            </div>
          ) : !analysisLoading ? (
            <div className="py-6 text-center flex flex-col items-center gap-3">
              <p className="text-sm text-muted">Add your players, then ask Claude to read your team.</p>
              <button onClick={loadAnalysis} className="btn btn-primary btn-sm">Read my team</button>
            </div>
          ) : null}
        </section>
      )}

      {toast && (
        <Toast
          message={toast.message}
          variant={toast.variant}
          onDismiss={() => setToast(null)}
        />
      )}
    </div>
  );
};
