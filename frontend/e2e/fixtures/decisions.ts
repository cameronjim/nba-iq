import type {
  RosterPlayer, StartSitResponse, StreamersResponse, TradeCheckResponse,
} from '../../src/types';
import { ALL_STAR, ROLE_PLAYER, UNRANKED_ROOKIE, INJURED, type PlayerFixture } from './players';

function rosterRow(player: PlayerFixture, rosterId: number): RosterPlayer {
  return {
    ...player,
    nba_id: String(9000 + player.id),
    roster_id: rosterId,
    player_id: player.id,
    added_at: '2026-01-10T12:00:00.000Z',
  };
}

export const ROSTER_FIXTURE: RosterPlayer[] = [rosterRow(ALL_STAR, 11), rosterRow(ROLE_PLAYER, 12)];

const RUN = { model_version: 'v-e2e', predicted_at: '2026-01-14T18:00:00.000Z' };
const WINDOW = { from: '2026-01-15', to: '2026-01-21', days: 7 };

export const START_SIT_FIXTURE: StartSitResponse = {
  status: 'ok',
  window: WINDOW,
  slots: 1,
  run: RUN,
  value_basis: 'slate impact',
  days: [
    {
      date: '2026-01-15',
      players: [
        {
          player_id: ALL_STAR.id, nba_player_id: '9001', name: ALL_STAR.name, nba_game_id: 'g1',
          opponent: 'BOS', home: true, impact: 6.1, points_if_plays: 28, minutes_if_plays: 35,
          prob_active: 0.82, start: true,
          sentence: 'Test Allstar · vs BOS · 28 pts, 35 min if he plays · 82% to play',
        },
        {
          player_id: ROLE_PLAYER.id, nba_player_id: '9002', name: ROLE_PLAYER.name, nba_game_id: 'g1',
          opponent: 'LAL', home: false, impact: 1.2, points_if_plays: 12, minutes_if_plays: 26,
          prob_active: 0.95, start: false,
          sentence: 'Test Rolepar · @ LAL · 12 pts, 26 min if he plays · 95% to play',
        },
      ],
      recommendation: 'Start Test Allstar; sit Test Rolepar.',
    },
    { date: '2026-01-16', players: [], recommendation: 'No one on your roster plays.' },
  ],
};

export const STREAMERS_FIXTURE: StreamersResponse = {
  status: 'ok',
  window: WINDOW,
  run: RUN,
  score_basis: 'expected category wins',
  streamers: [
    {
      id: UNRANKED_ROOKIE.id, nba_id: '9003', name: UNRANKED_ROOKIE.name, team: UNRANKED_ROOKIE.team,
      position: UNRANKED_ROOKIE.position, games: 4, score: 1.82,
      drivers: [{ category: 'reb', label: 'REB', value: 0.5 }, { category: 'blk', label: 'BLK', value: 0.4 }],
      basis: 'projection', mean_prob_play: 0.9,
      sentence: 'Pick up Test Rookie: 4 games this week, about +1.8 expected category wins.',
    },
  ],
};

export const TRADE_CHECK_FIXTURE: TradeCheckResponse = {
  status: 'ok',
  window: WINDOW,
  run: RUN,
  seed: 20260930,
  opponent_definition: 'typical opponent',
  give: [{ id: ALL_STAR.id, nba_id: '9001', name: ALL_STAR.name, games: 3 }],
  get: [{ id: INJURED.id, nba_id: '9004', name: INJURED.name, games: 4 }],
  before_expected_wins: 4.1,
  after_expected_wins: 4.7,
  delta_expected_wins: 0.6,
  categories: [
    { category: 'reb', label: 'REB', lower_is_better: false, before: 0.4, after: 0.71, delta: 0.31 },
    { category: 'blk', label: 'BLK', lower_is_better: false, before: 0.35, after: 0.6, delta: 0.25 },
    { category: 'fg3m', label: '3PM', lower_is_better: false, before: 0.6, after: 0.48, delta: -0.12 },
  ],
  verdict: 'This trade helps: +0.6 expected category wins, mainly REB and BLK; you lose some 3PM.',
};
