import type {
  WatchlistGame,
  WatchlistPlayer,
  WatchlistPositionFilter,
  WatchlistResponse,
} from '../../src/types';

const BASELINE = {
  window_games: 15,
  min_games: 5,
  notable_min_delta: 4,
  label: 'his own recent form',
  definition: 'per-game averages over his last 15 games played before this date, requiring at least 5',
};

function shift(from: string, days: number): string {
  return new Date(Date.parse(`${from}T00:00:00Z`) + days * 86_400_000).toISOString().slice(0, 10);
}

function game(date: string, opponent: string, minutes: number, pts: number, score: number): WatchlistGame {
  return {
    game_date: date,
    nba_game_id: `00226001${date.slice(-2)}`,
    opponent_team_abbr: opponent,
    preseason: false,
    minutes_p50: minutes,
    proj_pts: pts,
    impact: Number((score * 4).toFixed(2)),
    score,
  };
}

function busyGuard(from: string, days: number): WatchlistPlayer {
  const games =
    days === 1
      ? [game(from, 'UTA', 27, 14.1, 0.14)]
      : [
          game(from, 'UTA', 27, 14.1, 0.14),
          game(shift(from, 2), 'GSW', 27, 14.4, 0.21),
          game(shift(from, 4), 'SAC', 27, 13.7, 0.18),
          game(shift(from, 5), 'UTA', 27, 13.8, 0.19),
        ].slice(0, days >= 7 ? 4 : 2);
  const total = games.reduce((sum, g) => sum + Number(g.score), 0);
  return {
    nba_player_id: '1629630',
    name: 'Windowed Guard',
    name_is_placeholder: false,
    team_abbr: 'MEM',
    position: 'PG/SG',
    opponent_team_abbr: games[0].opponent_team_abbr,
    nba_game_id: games[0].nba_game_id,
    game_date: games[0].game_date,
    preseason: false,
    games_count: games.length,
    games,
    score: Number(total.toFixed(3)),
    score_per_game: Number((total / games.length).toFixed(3)),
    upside: 0.352,
    drivers: [{ stat: 'pts', delta: 2.4, scaled: 1.474 }],
    relevance: 0.518,
    impact: Number(games.reduce((sum, g) => sum + Number(g.impact), 0).toFixed(2)),
    impact_percentile: 85.6,
    prob_active: 0.686,
    minutes: { usual: 28.5, projected: 27.1, delta: -1.4 },
    points: { usual: 18.1, projected: 20.4, delta: 2.3 },
    totals: { pts: Number(games.reduce((sum, g) => sum + Number(g.proj_pts), 0).toFixed(1)) },
    baseline_games: 15,
    reasons: ['ROLE_INCREASE'],
    evidence: {},
  };
}

function restedForward(from: string, days: number): WatchlistPlayer {
  const games =
    days === 1
      ? [game(from, 'NYK', 30, 19.4, 0.24)]
      : [game(from, 'NYK', 30, 19.4, 0.24), game(shift(from, 3), 'PHI', 31, 20.1, 0.26)];
  const total = games.reduce((sum, g) => sum + Number(g.score), 0);
  return {
    nba_player_id: '1641705',
    name: 'Rested Forward',
    name_is_placeholder: false,
    team_abbr: 'BOS',
    position: 'SF/PF',
    opponent_team_abbr: games[0].opponent_team_abbr,
    nba_game_id: games[0].nba_game_id,
    game_date: games[0].game_date,
    preseason: false,
    games_count: games.length,
    games,
    score: Number(total.toFixed(3)),
    score_per_game: Number((total / games.length).toFixed(3)),
    upside: 0.61,
    drivers: [{ stat: 'minutes', delta: 4.8, scaled: 1.9 }],
    relevance: 0.62,
    impact: Number(games.reduce((sum, g) => sum + Number(g.impact), 0).toFixed(2)),
    impact_percentile: 91.2,
    prob_active: 0.94,
    minutes: { usual: 25.3, projected: 30.5, delta: 5.2 },
    points: { usual: 14.2, projected: 19.8, delta: 5.6 },
    totals: { pts: Number(games.reduce((sum, g) => sum + Number(g.proj_pts), 0).toFixed(1)) },
    baseline_games: 15,
    reasons: ['ROLE_INCREASE', 'SHOT_VOLUME_SURGE'],
    evidence: { fga_usual: 8.1, fga_projected: 11.4, fga_delta: 3.3 },
  };
}

const POSITION_OPTIONS: WatchlistPositionFilter[] = ['G', 'F', 'C', 'PG', 'SG', 'SF', 'PF'];

function matches(player: WatchlistPlayer, filter: string | null): boolean {
  if (!filter) return true;
  const positions = (player.position ?? '').split('/');
  if (filter === 'G') return positions.some((p) => p === 'PG' || p === 'SG');
  if (filter === 'F') return positions.some((p) => p === 'SF' || p === 'PF');
  if (filter === 'C') return positions.includes('C');
  return positions.includes(filter);
}

export function watchlistFixture(params: URLSearchParams): WatchlistResponse {
  const from = params.get('date') ?? '2026-10-20';
  const days = Number(params.get('days') ?? 1);
  const position = params.get('position');
  const multi = days > 1;

  const players = [busyGuard(from, days), restedForward(from, days)]
    .filter((player) => matches(player, position))
    .sort((a, b) => Number(b.score) - Number(a.score));

  return {
    date: from,
    window: { from, to: shift(from, days - 1), days },
    run: { model_version: '20260818', predicted_at: '2026-08-18T02:28:43.726Z' },
    pool: multi
      ? {
          key: 'slate',
          label: "Each night's slate",
          definition:
            "every player the run projects for a date, across all of that date's games; each night in the window is scored against its own slate",
          sample_size: 1838,
        }
      : {
          key: 'slate',
          label: "Tonight's slate",
          definition: "every player the run projects for this date, across all of the date's games",
          sample_size: 104,
        },
    baseline: BASELINE,
    position: (position as WatchlistPositionFilter | null) ?? null,
    position_options: POSITION_OPTIONS,
    position_coverage: { known: 510, unknown: 2 },
    players,
  };
}
