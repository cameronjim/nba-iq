import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { ScoreboardStrip } from '../components/ScoreboardStrip';
import { SkeletonTable } from '../components/Skeleton';
import { getPlayers, getTeams } from '../api/client';
import { prefetchCached, CACHE_KEYS } from '../api/resourceCache';
import { useCachedResource } from '../hooks/useCachedResource';
import { useSlate } from '../hooks/useSlate';
import { formatStat, toStatNumber, STAT_PLACEHOLDER } from '../utils/stats';
import { formatSlateDate } from '../utils/dates';
import type { Player, SlateGame, SlatePlayer } from '../types';

const PROJECTION_ROWS = 8;
const LEADER_ROWS = 5;
const LEADER_MIN_GAMES = 10;

interface ProjectionRow {
  player: SlatePlayer;
  opponent: string | null;
}

interface Destination {
  title: string;
  to: string;
  description: string;
  needsAuth: boolean;
}

const destinations: Destination[] = [
  { title: 'Stats', to: '/stats', description: 'Per-game averages for every player and team. Sort, filter, and compare.', needsAuth: false },
  { title: 'Projections', to: '/projections', description: "Predicted stat lines for tonight's games, ranked by fantasy impact.", needsAuth: false },
  { title: 'Watchlist', to: '/watchlist', description: 'Players putting up numbers above their own baseline.', needsAuth: false },
  { title: 'History', to: '/history', description: 'Season-by-season stats from past years.', needsAuth: false },
  { title: '2K Ratings', to: '/ratings', description: 'NBA 2K ratings and attribute breakdowns.', needsAuth: false },
  { title: 'My Team', to: '/fantasy', description: 'Your roster with category averages.', needsAuth: true },
  { title: 'Improve Team', to: '/improve', description: "Waiver and trade ideas based on your roster's weak categories.", needsAuth: true },
  { title: 'Betting', to: '/betting', description: "Odds, model picks, and the bets you've logged.", needsAuth: true },
];

function opponentOf(game: SlateGame, player: SlatePlayer): string | null {
  if (!player.team_abbr) return null;
  if (player.team_abbr === game.home_team_abbr) return game.away_team_abbr ? `vs ${game.away_team_abbr}` : null;
  if (player.team_abbr === game.away_team_abbr) return game.home_team_abbr ? `@ ${game.home_team_abbr}` : null;
  return null;
}

function topProjections(games: SlateGame[]): ProjectionRow[] {
  return games
    .flatMap((game) => game.players.map((player) => ({ player, opponent: opponentOf(game, player) })))
    .filter((row) => toStatNumber(row.player.impact) !== null)
    .sort((a, b) => (toStatNumber(b.player.impact) ?? 0) - (toStatNumber(a.player.impact) ?? 0))
    .slice(0, PROJECTION_ROWS);
}

function formatImpact(value: SlatePlayer['impact']): string {
  const n = toStatNumber(value);
  if (n === null) return STAT_PLACEHOLDER;
  return `${n > 0 ? '+' : ''}${n.toFixed(1)}`;
}

const TonightsProjections = (): JSX.Element => {
  const { date, data, loading, error } = useSlate();
  const rows = data ? topProjections(data.games) : [];
  const slateDate = formatSlateDate(data?.date ?? date);

  return (
    <section className="border-t border-base-300 pt-6">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-display text-2xl font-semibold uppercase tracking-wide">Tonight&apos;s projections</h2>
        <Link to="/projections" className="link link-hover text-sm">
          Full slate
        </Link>
      </div>

      {loading && <SkeletonTable rows={PROJECTION_ROWS} cols={7} label="Loading projections" />}

      {!loading && error && <p className="text-sm text-error">{error}. Try again in a moment.</p>}

      {!loading && !error && rows.length === 0 && (
        <p className="text-sm text-muted">No projections for {slateDate}. Check back on a game day.</p>
      )}

      {!loading && !error && rows.length > 0 && (
        <>
          <div className="overflow-x-auto">
            <table className="table table-sm">
              <thead>
                <tr>
                  <th>Player</th>
                  <th>Team</th>
                  <th>Opp</th>
                  <th className="text-right">PTS</th>
                  <th className="text-right">REB</th>
                  <th className="text-right">AST</th>
                  <th className="text-right">Impact</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ player, opponent }) => (
                  <tr key={player.nba_player_id}>
                    <td className="font-medium">{player.name}</td>
                    <td className="text-muted">{player.team_abbr ?? STAT_PLACEHOLDER}</td>
                    <td className="text-muted">{opponent ?? STAT_PLACEHOLDER}</td>
                    <td className="text-right tabular">{formatStat(player.proj_pts_cond)}</td>
                    <td className="text-right tabular">{formatStat(player.projected.reb)}</td>
                    <td className="text-right tabular">{formatStat(player.projected.ast)}</td>
                    <td className="text-right tabular font-semibold">{formatImpact(player.impact)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-faint">
            Model projection for {slateDate}, given that the player plays. Impact sums nine category z-scores; 0 is an
            average night.
          </p>
        </>
      )}
    </section>
  );
};

function leadersOf(players: Player[]): Player[] {
  const qualified = players.filter((p) => p.games_played >= LEADER_MIN_GAMES);
  const pool = qualified.length > 0 ? qualified : players;
  return [...pool].sort((a, b) => b.points_per_game - a.points_per_game).slice(0, LEADER_ROWS);
}

const Leaders = (): JSX.Element => {
  const { data, loading, error } = useCachedResource<Player[]>(CACHE_KEYS.players, () => getPlayers(), {
    errorMessage: 'Could not load player stats',
  });
  const leaders = data ? leadersOf(data) : [];

  return (
    <section className="border-t border-base-300 pt-6">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-display text-2xl font-semibold uppercase tracking-wide">Scoring leaders</h2>
        <Link to="/stats" className="link link-hover text-sm">
          All players
        </Link>
      </div>

      {loading && <SkeletonTable rows={LEADER_ROWS} cols={5} label="Loading leaders" />}

      {!loading && error && <p className="text-sm text-error">{error}. Try again in a moment.</p>}

      {!loading && !error && leaders.length === 0 && (
        <p className="text-sm text-muted">No player stats loaded yet. Check back after the next update.</p>
      )}

      {!loading && !error && leaders.length > 0 && (
        <div className="overflow-x-auto">
          <table className="table table-sm">
            <thead>
              <tr>
                <th>Player</th>
                <th>Team</th>
                <th className="text-right">PPG</th>
                <th className="text-right">RPG</th>
                <th className="text-right">APG</th>
              </tr>
            </thead>
            <tbody>
              {leaders.map((p) => (
                <tr key={p.id}>
                  <td className="font-medium">{p.name}</td>
                  <td className="text-muted">{p.team}</td>
                  <td className="text-right tabular font-semibold">{formatStat(p.points_per_game)}</td>
                  <td className="text-right tabular">{formatStat(p.rebounds_per_game)}</td>
                  <td className="text-right tabular">{formatStat(p.assists_per_game)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
};

export const HomePage = ({ isLoggedIn }: { isLoggedIn: boolean }): JSX.Element => {
  // this page is meant to be read while the stats caches warm, so fire the
  // prefetch immediately rather than waiting on the app-wide warmup delay.
  useEffect(() => {
    prefetchCached(CACHE_KEYS.players, () => getPlayers());
    prefetchCached(CACHE_KEYS.teams, getTeams);
  }, []);

  return (
    <div>
      <ScoreboardStrip />
      <div className="mx-auto max-w-[1100px] space-y-8 px-4 py-8">
        <div>
          <h1 className="font-display text-3xl font-semibold uppercase tracking-wide">NBA IQ</h1>
          <p className="mt-1 text-muted">NBA stats, nightly projections, and your fantasy roster.</p>
        </div>

        {!isLoggedIn && (
          <p className="text-sm">
            <Link to="/login" className="link">
              Sign in
            </Link>{' '}
            to track your fantasy roster, get waiver suggestions, and keep a bet ledger.
          </p>
        )}

        <TonightsProjections />
        <Leaders />

        <section className="border-t border-base-300 pt-6">
          <h2 className="mb-3 font-display text-2xl font-semibold uppercase tracking-wide">Sections</h2>
          <dl className="grid grid-cols-1 gap-x-8 sm:grid-cols-[10rem_1fr]">
            {destinations.map((dest) => (
              <div key={dest.to} className="contents">
                <dt className="pt-2 font-medium sm:border-t sm:border-base-300 sm:py-2">
                  <Link to={dest.to} className="link link-hover">
                    {dest.title}
                  </Link>
                </dt>
                <dd className="pb-2 text-sm text-muted sm:border-t sm:border-base-300 sm:py-2">
                  {dest.description}
                  {dest.needsAuth && !isLoggedIn && <span className="text-faint"> Sign in required.</span>}
                </dd>
              </div>
            ))}
          </dl>
        </section>

        <p className="text-sm text-muted">
          Stats refresh every six hours.{' '}
          <Link to="/about" className="link link-hover">
            How this was built
          </Link>
        </p>
      </div>
    </div>
  );
};
