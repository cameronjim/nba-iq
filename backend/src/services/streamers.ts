import { query } from '../db.js';
import {
  CATEGORY_LABELS,
  rankStreamers,
  rankingPools,
  type CandidateBasis,
  type RankedCandidate,
  type RankingPlayer,
} from './candidateRanking.js';
import { formatSignedWins, windowPhrase } from './decisionText.js';
import { getRankedPlayers } from './fantasyScore.js';
import type { ImpactCategory, RunSummary } from './slate.js';
import { windowRange, type WatchlistWindow } from './watchlist.js';
import { fetchWindowProjections } from './windowProjections.js';

export const DEFAULT_STREAMER_DAYS = 7;

export const STREAMER_SCORE_BASIS =
  'expected 9-category matchup wins he adds to your roster over the window, from his projected window totals; ' +
  'the totals sum every game he plays, so a four-game week counts about twice a two-game week';

export type StreamersStatus = 'ok' | 'empty_roster' | 'no_candidates';

export interface StreamerDriver {
  category: ImpactCategory;
  label: string;
  value: number;
}

export interface Streamer {
  id: number;
  nba_id: string | null;
  name: string;
  team: string | null;
  position: string | null;
  games: number | null;
  score: number;
  drivers: StreamerDriver[];
  basis: CandidateBasis;
  mean_prob_play: number | null;
  sentence: string;
}

export interface StreamersResponse {
  status: StreamersStatus;
  window: WatchlistWindow;
  run: RunSummary | null;
  score_basis: string;
  streamers: Streamer[];
}

function gamesText(games: number | null, days: number): string {
  const when = windowPhrase(days);
  if (games === null) return `schedule unknown ${when}`;
  return `${games} ${games === 1 ? 'game' : 'games'} ${when}`;
}

export function streamerSentence(name: string, games: number | null, score: number, days: number): string {
  return `Pick up ${name}: ${gamesText(games, days)}, about ${formatSignedWins(score)} expected category wins.`;
}

export function toStreamer(candidate: RankedCandidate, days: number): Streamer {
  return {
    id: candidate.id,
    nba_id: candidate.nba_id,
    name: candidate.name,
    team: candidate.team,
    position: candidate.position,
    games: candidate.projected_games,
    score: candidate.score,
    drivers: candidate.drivers.map((d) => ({ ...d, label: CATEGORY_LABELS[d.category] })),
    basis: candidate.basis,
    mean_prob_play: candidate.mean_prob_play,
    sentence: streamerSentence(candidate.name, candidate.projected_games, candidate.score, days),
  };
}

type PlayerRow = Record<string, unknown>;

function toRankingPlayer(row: PlayerRow): RankingPlayer {
  const n = (key: string): number => Number(row[key]) || 0;
  const text = (key: string): string | null =>
    row[key] === null || row[key] === undefined || row[key] === '' ? null : String(row[key]);
  return {
    id: Number(row.id),
    nba_id: text('nba_id'),
    name: String(row.name ?? ''),
    team: text('team'),
    position: text('position'),
    points_per_game: n('points_per_game'),
    rebounds_per_game: n('rebounds_per_game'),
    assists_per_game: n('assists_per_game'),
    steals_per_game: n('steals_per_game'),
    blocks_per_game: n('blocks_per_game'),
    three_pointers_made: n('three_pointers_made'),
    turnovers_per_game: n('turnovers_per_game'),
    field_goal_percentage: n('field_goal_percentage'),
    free_throw_percentage: n('free_throw_percentage'),
  };
}

async function fetchRankingRoster(userId: number): Promise<RankingPlayer[]> {
  const result = await query(
    `SELECT p.id, p.nba_id, p.name, p.team, p.position,
            p.points_per_game, p.rebounds_per_game, p.assists_per_game, p.steals_per_game, p.blocks_per_game,
            p.field_goal_percentage, p.free_throw_percentage, p.three_pointers_made,
            p.turnovers_per_game
     FROM my_roster mr
     JOIN players p ON mr.player_id = p.id
     WHERE mr.user_id = $1
     ORDER BY p.name`,
    [userId]
  );
  return (result.rows as PlayerRow[]).map(toRankingPlayer);
}

export async function getStreamers(
  userId: number,
  start: string,
  days: number = DEFAULT_STREAMER_DAYS
): Promise<StreamersResponse> {
  const roster = await fetchRankingRoster(userId);
  if (roster.length === 0) {
    return {
      status: 'empty_roster',
      window: windowRange(start, days),
      run: null,
      score_basis: STREAMER_SCORE_BASIS,
      streamers: [],
    };
  }

  const projections = await fetchWindowProjections(start, days);
  const ranked = await getRankedPlayers();
  const pools = rankingPools(ranked, new Set(roster.map((p) => p.id)));
  const candidates = rankStreamers(roster, pools.waiver_pool, projections.players, {
    scheduledGames: projections.scheduled_games,
    population: [...roster, ...pools.trade_pool, ...pools.waiver_pool],
    rosteredPool: pools.trade_pool,
  });
  return {
    status: candidates.length === 0 ? 'no_candidates' : 'ok',
    window: projections.window,
    run: projections.run,
    score_basis: STREAMER_SCORE_BASIS,
    streamers: candidates.map((c) => toStreamer(c, days)),
  };
}
