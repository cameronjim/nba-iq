import {
  PROJECTED_STATS,
  getLatestCompleteRun,
  num,
  round,
  type ProjectedStat,
  type SlateRun,
} from './slate.js';
import { deltaOf, fetchBaselines, hasUsableBaseline, type PlayerBaseline } from './baselines.js';
import {
  fetchGameTeams,
  fetchWindowPredictionRows,
  windowRange,
  type WatchlistWindow,
  type WindowPredictionRow,
} from './watchlist.js';

export const DEFAULT_PROJECTION_DAYS = 7;

export interface WindowProjection {
  nba_player_id: string;
  games: number;
  totals: Record<ProjectedStat, number | null>;
  mean_prob_active: number | null;
  mean_min_p50: number | null;
  min_vs_usual: number | null;
}

export interface WindowProjectionSet {
  run: SlateRun | null;
  window: WatchlistWindow;
  players: Map<string, WindowProjection>;
  scheduled_games: Map<string, number>;
}

function meanOf(values: number[]): number | null {
  if (values.length === 0) return null;
  return values.reduce((s, v) => s + v, 0) / values.length;
}

export function aggregateWindowRows(
  rows: WindowPredictionRow[],
  baselines: Map<string, PlayerBaseline>
): Map<string, WindowProjection> {
  const grouped = new Map<string, WindowPredictionRow[]>();
  for (const row of rows) {
    const id = String(row.nba_player_id);
    const list = grouped.get(id) ?? [];
    list.push(row);
    grouped.set(id, list);
  }

  const out = new Map<string, WindowProjection>();
  for (const [id, games] of grouped) {
    const totals = {} as Record<ProjectedStat, number | null>;
    for (const stat of PROJECTED_STATS) {
      const values = games
        .map((g) => num((g as Record<string, unknown>)[`u_${stat}`]))
        .filter((v): v is number => v !== null);
      totals[stat] = values.length === 0 ? null : round(values.reduce((s, v) => s + v, 0), 2);
    }

    const probs = games.map((g) => num(g.prob_active)).filter((v): v is number => v !== null);
    const minutes = games.map((g) => num(g.proj_min_p50)).filter((v): v is number => v !== null);
    const meanMinutes = meanOf(minutes);
    const baseline = baselines.get(id);
    const usual = hasUsableBaseline(baseline) ? (baseline as PlayerBaseline).avg.minutes : null;

    out.set(id, {
      nba_player_id: id,
      games: new Set(games.map((g) => String(g.nba_game_id))).size,
      totals,
      mean_prob_active: round(meanOf(probs), 3),
      mean_min_p50: round(meanMinutes, 1),
      min_vs_usual: round(deltaOf(meanMinutes, usual), 1),
    });
  }
  return out;
}

export function countTeamGames(
  gameTeams: Map<string, [string | null, string | null]>
): Map<string, number> {
  const counts = new Map<string, number>();
  for (const teams of gameTeams.values()) {
    for (const team of teams) {
      if (team) counts.set(team, (counts.get(team) ?? 0) + 1);
    }
  }
  return counts;
}

export async function fetchWindowProjections(
  date: string,
  days: number = DEFAULT_PROJECTION_DAYS
): Promise<WindowProjectionSet> {
  const window = windowRange(date, days);
  const scheduled = countTeamGames(await fetchGameTeams(window.from, window.to));
  const run = await getLatestCompleteRun();
  if (!run) return { run: null, window, players: new Map(), scheduled_games: scheduled };

  const rows = await fetchWindowPredictionRows(run.id, window.from, window.to);
  const baselines = rows.length > 0 ? await fetchBaselines(window.from) : new Map<string, PlayerBaseline>();

  return {
    run: { model_version: run.model_version, predicted_at: run.predicted_at },
    window,
    players: aggregateWindowRows(rows, baselines),
    scheduled_games: scheduled,
  };
}
