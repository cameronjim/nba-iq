import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { Search, Plus, Trash2, RefreshCw, ChevronUp, ChevronDown } from 'lucide-react';
import { getMyRoster, getPlayers, addToRoster, dropFromRoster, getTeamAnalysis } from '../api/client';
import type { Player, RosterPlayer, TeamAnalysis } from '../types';
import { getTeamLogoUrl } from '../utils/teamLogos';
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

const CAT_COLORS: Record<string, string> = {
  strong: 'badge-success',
  average: 'badge-warning',
  weak: 'badge-error',
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
  const { state: outlookState, reload: reloadOutlook } = useWeeklyOutlook(
    isLoggedIn && !rosterLoading && roster.length > 0,
    rosterKey
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
    const cls = status === 'Out' ? 'badge-error'
      : ['Day-To-Day', 'Day_To_Day', 'Questionable'].includes(status) ? 'badge-warning'
      : status === 'Probable' ? 'badge-success'
      : 'badge-error';
    return <span className={`badge badge-xs ml-2 ${cls}`}>{status.replace(/_/g, ' ')}</span>;
  };

  return (
    <div className="max-w-[1400px] mx-auto px-4 py-6 space-y-5">
      {isLoggedIn && <PreferencesPrompt />}
      {isLoggedIn && (
        <div className="card bg-base-200">
          <div className="card-body p-4">
            <label className="input input-bordered flex items-center gap-2">
              <Search size={16} className="opacity-50" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search players to add to your team..."
                className="grow"
              />
              {searching && <span className="loading loading-spinner loading-xs" />}
            </label>

            {searchResults.length > 0 && (
              <div className="mt-2 space-y-1 max-h-[300px] overflow-y-auto">
                {searchResults.map((player) => (
                  <div key={player.id} className="flex items-center justify-between px-3 py-2 rounded-lg hover:bg-base-300 transition-colors">
                    <div className="flex items-center gap-3">
                      <div className="avatar">
                        <div className="w-7 rounded-full">
                          <img
                            src={player.headshot_url || ''}
                            alt=""
                            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                          />
                        </div>
                      </div>
                      <span className="text-sm font-medium">{player.name}</span>
                      <span className="text-xs opacity-50">{player.position} · {player.team}</span>
                      <span className="text-xs opacity-70">{n(player.points_per_game).toFixed(1)} PPG</span>
                    </div>
                    <button onClick={() => handleAdd(player)} className="btn btn-primary btn-xs gap-1">
                      <Plus size={12} /> Add
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      <div className="card bg-base-200 overflow-hidden">
        <div className="card-body p-0">
          <div className="px-4 py-3 border-b border-base-300">
            <h3 className="font-semibold text-sm">My Roster ({roster.length} players)</h3>
          </div>

          {!isLoggedIn ? (
            <div className="text-center p-12">
              <p className="font-semibold text-sm mb-1">Sign in to use My Team</p>
              <p className="opacity-40 text-xs">Use the Sign In button in the top right to manage your roster.</p>
            </div>
          ) : rosterLoading ? (
            <div className="flex justify-center p-8">
              <span className="loading loading-spinner loading-lg" />
            </div>
          ) : roster.length === 0 ? (
            <div className="text-center p-12">
              <p className="opacity-40 text-sm">No players on your roster yet</p>
              <p className="opacity-25 text-xs mt-1">Use the search bar above to add players</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="table table-zebra table-sm">
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
                            ? (sortDir === 'asc' ? <ChevronUp size={12} /> : <ChevronDown size={12} />)
                            : <ChevronUp size={12} className="invisible" />}
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
                            <div className="w-6 rounded-full">
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
                            className="btn btn-error btn-xs gap-1"
                          >
                            <Trash2 size={12} /> Drop
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                  {/* kept inside <tbody>: daisyUI's <tfoot> css bolds and shrinks
                      its cells, which would make this row stand out wrong. */}
                  <tr className="bg-base-300/40">
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
      </div>

      {isLoggedIn && roster.length > 0 && (
        <WeeklyOutlookCard state={outlookState} onReload={reloadOutlook} />
      )}

      {isLoggedIn && roster.length > 0 && (
        <div className="card bg-base-200 overflow-hidden">
          <div className="card-body p-0">
            <div className="px-4 py-3 border-b border-base-300 flex items-center justify-between">
              <h3 className="font-semibold text-sm">9-Category Analysis</h3>
              <button
                onClick={loadAnalysis}
                disabled={analysisLoading}
                className="btn btn-ghost btn-xs gap-1.5"
              >
                <RefreshCw size={12} className={analysisLoading ? 'animate-spin' : ''} />
                {analysisLoading ? 'Analyzing...' : 'Refresh'}
              </button>
            </div>

            {analysisLoading && !analysis ? (
              <div className="flex flex-col items-center p-8 gap-3">
                <span className="loading loading-spinner loading-lg" />
                <p className="text-sm opacity-50">AI is analyzing your roster...</p>
              </div>
            ) : analysis?.categories ? (
              <div className="p-4">
                <div className="flex flex-wrap gap-2 mb-4">
                  {Object.entries(analysis.categories).map(([cat, rating]) => (
                    <span key={cat} className={`badge badge-lg ${CAT_COLORS[rating] ?? 'badge-warning'}`}>
                      {cat}
                      <span className="ml-1 opacity-70 capitalize text-xs">· {rating}</span>
                    </span>
                  ))}
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  {[
                    { title: 'Strengths', items: analysis.strengths, cls: 'text-success' },
                    { title: 'Weaknesses', items: analysis.weaknesses, cls: 'text-error' },
                    { title: 'Suggestions', items: analysis.suggestions, cls: 'text-info' },
                  ].map((section) => (
                    <div key={section.title} className="space-y-2">
                      <p className={`text-xs font-bold uppercase tracking-wider ${section.cls}`}>{section.title}</p>
                      <div className="space-y-1.5">
                        {section.items?.map((item, i) => (
                          <div key={i} className="flex items-start gap-2 px-3 py-2 rounded-lg bg-base-300">
                            <span className={`w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0 ${section.cls} bg-current`} />
                            <span className="text-xs leading-relaxed opacity-80">{item}</span>
                          </div>
                        ))}
                        {(!section.items || section.items.length === 0) && (
                          <p className="text-xs opacity-25 italic px-3">None identified</p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : !analysisLoading ? (
              <div className="p-6 text-center flex flex-col items-center gap-3">
                <p className="text-sm opacity-40">Add your players, then analyze your team</p>
                <button onClick={loadAnalysis} className="btn btn-primary btn-sm">Analyze My Team</button>
              </div>
            ) : null}
          </div>
        </div>
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
