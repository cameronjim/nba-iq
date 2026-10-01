import { query } from '../db.js';
import { joinWords } from './decisionText.js';
import {
  PROJECTED_STATS,
  getLatestCompleteRun,
  impactScores,
  num,
  resolvePlayerName,
  round,
  rowsOrEmpty,
  toIsoDay,
  type ImpactInput,
  type RunSummary,
} from './slate.js';
import {
  fetchGameTeams,
  fetchWindowPredictionRows,
  shiftIsoDate,
  windowRange,
  type WatchlistWindow,
  type WindowPredictionRow,
} from './watchlist.js';

export const DEFAULT_START_SIT_DAYS = 7;
export const DEFAULT_STARTING_SLOTS = 10;
export const MAX_STARTING_SLOTS = 20;

export const START_SIT_VALUE_BASIS =
  "slate impact: the summed 9-category z-score of his unconditional projection for that game, against every player the run projects that day";

export type StartSitStatus = 'ok' | 'empty_roster' | 'no_run' | 'no_games';

export interface StartSitRosterPlayer {
  player_id: number;
  nba_player_id: string | null;
  name: string;
  team: string | null;
}

export interface StartSitEntry {
  player_id: number;
  nba_player_id: string;
  name: string;
  nba_game_id: string;
  opponent: string | null;
  home: boolean | null;
  impact: number | null;
  points_if_plays: number | null;
  minutes_if_plays: number | null;
  prob_active: number | null;
  start: boolean;
  sentence: string;
}

export interface StartSitDay {
  date: string;
  players: StartSitEntry[];
  recommendation: string;
}

export interface StartSitResponse {
  status: StartSitStatus;
  window: WatchlistWindow;
  slots: number;
  run: RunSummary | null;
  value_basis: string;
  days: StartSitDay[];
}

export function parseStartingSlots(raw: unknown): number | null {
  if (raw === undefined || raw === null || raw === '') return DEFAULT_STARTING_SLOTS;
  if (typeof raw !== 'string' && typeof raw !== 'number') return null;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1 || value > MAX_STARTING_SLOTS) return null;
  return value;
}

function windowDates(window: WatchlistWindow): string[] {
  return Array.from({ length: window.days }, (_, i) => shiftIsoDate(window.from, i));
}

function matchupText(opponent: string | null, home: boolean | null): string | null {
  if (opponent === null) return null;
  return home === false ? `@ ${opponent}` : `vs ${opponent}`;
}

export function startSitSentence(
  entry: Pick<StartSitEntry, 'name' | 'opponent' | 'home' | 'points_if_plays' | 'minutes_if_plays' | 'prob_active'>
): string {
  const line: string[] = [];
  if (entry.points_if_plays !== null) line.push(`${Math.round(entry.points_if_plays)} pts`);
  if (entry.minutes_if_plays !== null) line.push(`${Math.round(entry.minutes_if_plays)} min`);
  const parts = [
    entry.name,
    matchupText(entry.opponent, entry.home),
    line.length > 0 ? `${line.join(', ')} if he plays` : null,
    entry.prob_active === null ? null : `${Math.round(entry.prob_active * 100)}% to play`,
  ];
  return parts.filter((part): part is string => part !== null).join(' · ');
}

export function startSitRecommendation(players: readonly StartSitEntry[], slots: number): string {
  if (players.length === 0) return 'No one on your roster plays.';
  if (players.length <= slots) {
    const who = players.length === 1 ? 'your 1 player' : `all ${players.length} players`;
    return `Start ${who} with a game; they fit in your ${slots} slots.`;
  }
  const sat = players.filter((p) => !p.start).map((p) => p.name);
  const started = slots === 1 ? players[0].name : `these ${slots}`;
  return `Start ${started}; sit ${joinWords(sat)}.`;
}

function impactInputOf(row: WindowPredictionRow): ImpactInput {
  const entry = {} as ImpactInput;
  for (const stat of PROJECTED_STATS) entry[stat] = num((row as Record<string, unknown>)[`u_${stat}`]);
  return entry;
}

function byImpactDesc(a: StartSitEntry, b: StartSitEntry): number {
  if (a.impact === null && b.impact !== null) return 1;
  if (b.impact === null && a.impact !== null) return -1;
  return (b.impact ?? 0) - (a.impact ?? 0) || a.name.localeCompare(b.name) || a.player_id - b.player_id;
}

// each day is scored against that day's whole slate, so a player's value is comparable within the day.
export function buildStartSitDays(
  roster: readonly StartSitRosterPlayer[],
  rows: readonly WindowPredictionRow[],
  gameTeams: ReadonlyMap<string, [string | null, string | null]>,
  window: WatchlistWindow,
  slots: number
): StartSitDay[] {
  const rosterByNbaId = new Map<string, StartSitRosterPlayer>();
  for (const player of roster) if (player.nba_player_id) rosterByNbaId.set(player.nba_player_id, player);

  const byDate = new Map<string, WindowPredictionRow[]>();
  for (const row of rows) {
    const date = toIsoDay(row.game_date);
    if (date === null) continue;
    const list = byDate.get(date) ?? [];
    list.push(row);
    byDate.set(date, list);
  }

  return windowDates(window).map((date): StartSitDay => {
    const slate = byDate.get(date) ?? [];
    const impacts = impactScores(slate.map(impactInputOf));
    const seen = new Set<string>();
    const entries: StartSitEntry[] = [];
    slate.forEach((row, i) => {
      const nbaId = String(row.nba_player_id);
      const player = rosterByNbaId.get(nbaId);
      if (!player || seen.has(nbaId)) return;
      seen.add(nbaId);
      const gameId = String(row.nba_game_id);
      const teams = gameTeams.get(gameId);
      const team = player.team ?? (row.team_abbr === null || row.team_abbr === undefined ? null : String(row.team_abbr));
      let opponent: string | null = null;
      let home: boolean | null = null;
      if (team && teams) {
        if (teams[0] === team) {
          opponent = teams[1];
          home = true;
        } else if (teams[1] === team) {
          opponent = teams[0];
          home = false;
        }
      }
      const base = {
        player_id: player.player_id,
        nba_player_id: nbaId,
        name: player.name,
        nba_game_id: gameId,
        opponent,
        home,
        impact: impacts[i] ?? null,
        points_if_plays: round(num(row.c_pts), 1),
        minutes_if_plays: round(num(row.proj_min_p50), 1),
        prob_active: round(num(row.prob_active), 3),
        start: true,
      };
      entries.push({ ...base, sentence: startSitSentence(base) });
    });

    entries.sort(byImpactDesc);
    entries.forEach((entry, i) => {
      entry.start = i < slots;
    });
    return { date, players: entries, recommendation: startSitRecommendation(entries, slots) };
  });
}

interface RosterRow {
  player_id: unknown;
  nba_id: unknown;
  name: unknown;
  team: unknown;
}

async function fetchRosterWithTeams(userId: number): Promise<StartSitRosterPlayer[]> {
  const rows = await rowsOrEmpty<RosterRow>(() =>
    query(
      `SELECT mr.player_id, p.nba_id, p.name, p.team
       FROM my_roster mr
       JOIN players p ON p.id = mr.player_id
       WHERE mr.user_id = $1
       ORDER BY p.name`,
      [userId]
    )
  );
  return rows.map((row) => {
    const nbaId = row.nba_id === null || row.nba_id === undefined ? null : String(row.nba_id);
    return {
      player_id: Number(row.player_id),
      nba_player_id: nbaId,
      name: resolvePlayerName(row.name, nbaId ?? String(row.player_id)).name,
      team: row.team === null || row.team === undefined ? null : String(row.team),
    };
  });
}

export async function getStartSit(
  userId: number,
  start: string,
  days: number = DEFAULT_START_SIT_DAYS,
  slots: number = DEFAULT_STARTING_SLOTS
): Promise<StartSitResponse> {
  const window = windowRange(start, days);
  const emptyDays = (): StartSitDay[] =>
    windowDates(window).map((date) => ({ date, players: [], recommendation: startSitRecommendation([], slots) }));
  const base = (status: StartSitStatus, run: RunSummary | null): StartSitResponse => ({
    status,
    window,
    slots,
    run,
    value_basis: START_SIT_VALUE_BASIS,
    days: emptyDays(),
  });

  const roster = await fetchRosterWithTeams(userId);
  if (roster.length === 0) return base('empty_roster', null);

  const run = await getLatestCompleteRun();
  if (!run) return base('no_run', null);
  const runSummary: RunSummary = { model_version: run.model_version, predicted_at: run.predicted_at };

  const rows = await fetchWindowPredictionRows(run.id, window.from, window.to);
  const gameTeams = await fetchGameTeams(window.from, window.to);
  const dayList = buildStartSitDays(roster, rows, gameTeams, window, slots);
  if (dayList.every((day) => day.players.length === 0)) return base('no_games', runSummary);

  return { ...base('ok', runSummary), days: dayList };
}
