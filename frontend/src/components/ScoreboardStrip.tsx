import { useEffect, useState, useCallback, useRef } from 'react';
import { IconChevronLeft, IconChevronRight } from './icons';
import { SkeletonBlock } from './Skeleton';
import { getGames, getLiveGames } from '../api/client';
import type { Game } from '../types';

// module-level so it persists across remounts.
let gamesCache: Game[] = [];
let cacheFetchedAt = 0;

// gap left of the anchor group so it clears the left scroll button.
const LEFT_GAP = 36;

function periodLabel(period: number): string {
  if (period <= 4) return `Q${period}`;
  return `OT${period - 4 > 1 ? period - 4 : ''}`;
}

export const ScoreboardStrip = () => {
  const [games, setGames] = useState<Game[]>(gamesCache);
  const [loading, setLoading] = useState(cacheFetchedAt === 0);
  // without this the 2-minute poll and the live overlay would keep yanking the
  // strip back to today while the user is scrolling through other days.
  const autoScrolledForDay = useRef<string | null>(null);
  const spacerRef = useRef<HTMLDivElement>(null);

  const fetchGames = useCallback(async (): Promise<void> => {
    try {
      const dbGames = await getGames();
      setGames(dbGames);
      gamesCache = dbGames;
      cacheFetchedAt = Date.now();

      getLiveGames().then((liveGames) => {
        if (!liveGames.length) return;
        // dedupe on date as well as matchup: in a playoff series the same two teams
        // play repeatedly, so a live OKC@SAS must not filter out a scheduled one.
        const liveIds = new Set(liveGames.map((g) => g.nba_game_id ?? g.id));
        const liveMatchup = (g: Game) =>
          `${g.game_date}||${(g.home_team ?? '').toLowerCase()}||${(g.away_team ?? '').toLowerCase()}`;
        const liveMatchups = new Set(liveGames.map(liveMatchup));
        setGames((prev) => {
          const merged = [
            ...liveGames,
            ...prev.filter((g) =>
              !liveIds.has(g.nba_game_id ?? g.id) && !liveMatchups.has(liveMatchup(g))
            ),
          ];
          gamesCache = merged;
          return merged;
        });
      }).catch(() => { /* db data is already showing */ });
    } catch {
      setGames([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // 2 min matches the server-side cache ttl.
    if (Date.now() - cacheFetchedAt > 2 * 60_000) {
      fetchGames();
    }
    const pollInterval = setInterval(fetchGames, 2 * 60_000);
    return () => clearInterval(pollInterval);
  }, [fetchGames]);

  const scroll = (direction: 'left' | 'right'): void => {
    const container = document.getElementById('scoreboard-scroll');
    if (!container) return;
    container.scrollBy({ left: direction === 'left' ? -280 : 280, behavior: 'smooth' });
  };

  // computed at render time so it stays correct across midnight.
  const todayIso = (() => {
    const d = new Date();
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd}`;
  })();

  const formatDate = (dateStr: string): string => {
    const d = new Date(dateStr + 'T12:00:00');
    const today = new Date();
    today.setHours(12, 0, 0, 0);
    const diffDays = Math.round((d.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
    if (diffDays === 0) return 'Today';
    if (diffDays === -1) return 'Yesterday';
    if (diffDays === 1) return 'Tomorrow';
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  };

  // puts today in the second slot and sizes the trailing spacer so the strip
  // cannot be scrolled past that anchor into empty space.
  const layoutScoreboard = useCallback((): void => {
    const container = document.getElementById('scoreboard-scroll');
    if (!container) return;
    const groups = Array.from(container.querySelectorAll<HTMLElement>('[data-date]'));
    const todayIdx = groups.findIndex((el) => el.dataset.date === todayIso);
    if (todayIdx === -1) return;
    const todayGroup = groups[todayIdx];
    const anchor = groups[Math.max(0, todayIdx - 1)];
    const hasFuture = todayIdx < groups.length - 1;

    if (spacerRef.current) {
      if (hasFuture) {
        spacerRef.current.style.width = '0px';
      } else {
        const slotsSpan =
          todayGroup.offsetLeft + todayGroup.offsetWidth - anchor.offsetLeft;
        const room = Math.max(0, container.clientWidth - slotsSpan - LEFT_GAP);
        spacerRef.current.style.width = `${room}px`;
      }
    }

    // reading the live rects also flushes the spacer width change above.
    const delta =
      anchor.getBoundingClientRect().left - container.getBoundingClientRect().left;
    container.scrollLeft += delta - LEFT_GAP;
  }, [todayIso]);

  // the double rAF waits until card widths have actually painted before measuring.
  useEffect(() => {
    if (games.length === 0) return;
    if (autoScrolledForDay.current === todayIso) return;
    let raf2 = 0;
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => {
        layoutScoreboard();
        autoScrolledForDay.current = todayIso;
      });
    });
    return () => {
      cancelAnimationFrame(raf1);
      cancelAnimationFrame(raf2);
    };
  }, [games, todayIso, layoutScoreboard]);

  useEffect(() => {
    let raf = 0;
    const onResize = (): void => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(layoutScoreboard);
    };
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      cancelAnimationFrame(raf);
    };
  }, [layoutScoreboard]);

  const grouped = games.reduce<Record<string, Game[]>>((acc, g) => {
    const date = g.game_date?.split('T')[0] || 'Unknown';
    if (!acc[date]) acc[date] = [];
    acc[date].push(g);
    return acc;
  }, {});

  // today is always present so the auto-scroll has a stable anchor even off-season.
  if (!grouped[todayIso]) grouped[todayIso] = [];

  const sortedDates = Object.keys(grouped).sort((a, b) => a.localeCompare(b));

  return (
    <div className="bg-base-200 border-b border-base-300 relative">
      <button
        onClick={() => scroll('left')}
        className="btn btn-ghost btn-xs absolute left-1 top-1/2 -translate-y-1/2 z-10"
        aria-label="Scroll scores left"
      >
        <IconChevronLeft size={16} />
      </button>

      <div
        id="scoreboard-scroll"
        className="flex gap-3 overflow-x-auto py-3 px-10 no-scrollbar"
      >
        {loading && (
          <div role="status" aria-label="Loading scores" className="flex gap-3">
            {Array.from({ length: 5 }, (_, i) => (
              <SkeletonBlock key={i} className="h-[74px] w-[210px] flex-shrink-0" />
            ))}
          </div>
        )}
        {!loading && sortedDates.map((date) => (
          <div key={date} data-date={date} className="flex items-center gap-3 flex-shrink-0">
            <div className="flex-shrink-0 text-center px-2">
              <div className="font-display text-sm font-semibold uppercase tracking-wide">{formatDate(date)}</div>
              <div className="text-[10px] text-faint tabular">{date}</div>
            </div>

            {grouped[date].length === 0 && (
              <div className="flex-shrink-0 min-w-[200px] border border-base-300 px-3 py-4 text-center">
                <p className="text-xs text-muted">No games scheduled</p>
              </div>
            )}

            {grouped[date].map((game) => {
              const isLive = game.status.toLowerCase() === 'in progress';
              const isFinal = game.status.toLowerCase() === 'final';
              const homeWon = isFinal && game.home_score != null && game.away_score != null && game.home_score > game.away_score;
              const awayWon = isFinal && game.home_score != null && game.away_score != null && game.away_score > game.home_score;

              return (
                <div
                  key={game.id}
                  className={`flex-shrink-0 min-w-[210px] border bg-base-100 ${isLive ? 'border-success' : 'border-base-300'}`}
                >
                  <div className="flex flex-col gap-1.5 p-3">
                    <div className="flex items-center gap-2">
                      {isLive ? (
                        <span className="badge badge-success badge-sm">
                          {game.period
                            ? `${periodLabel(game.period)}${game.game_clock ? ` ${game.game_clock}` : ''}`
                            : 'LIVE'}
                        </span>
                      ) : (
                        <span className={`text-[10px] font-bold uppercase tracking-wider ${isFinal ? 'text-faint' : 'text-muted'}`}>
                          {isFinal ? 'FINAL' : game.status.toUpperCase()}
                        </span>
                      )}
                    </div>
                    <div className="space-y-0.5">
                      <div className="flex items-center justify-between gap-4">
                        <span className={`text-sm ${awayWon ? 'font-bold' : 'text-muted'}`}>{game.away_team}</span>
                        <span className={`text-sm tabular ${awayWon ? 'font-bold' : ''}`}>{game.away_score ?? '-'}</span>
                      </div>
                      <div className="flex items-center justify-between gap-4">
                        <span className={`text-sm ${homeWon ? 'font-bold' : 'text-muted'}`}>{game.home_team}</span>
                        <span className={`text-sm tabular ${homeWon ? 'font-bold' : ''}`}>{game.home_score ?? '-'}</span>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}

            <div className="w-px h-12 bg-base-300 flex-shrink-0" />
          </div>
        ))}

        {/* trailing scroll room, width set in layoutScoreboard. */}
        <div ref={spacerRef} aria-hidden className="flex-shrink-0" style={{ width: 0 }} />
      </div>

      <button
        onClick={() => scroll('right')}
        className="btn btn-ghost btn-xs absolute right-1 top-1/2 -translate-y-1/2 z-10"
        aria-label="Scroll scores right"
      >
        <IconChevronRight size={16} />
      </button>
    </div>
  );
};
