import { describe, it, expect } from 'vitest';
import { toProjectionRow } from '../../src/utils/projectionRow';
import type { SlateGame, SlatePlayer, WatchlistPlayer } from '../../src/types';

function slatePlayer(overrides: Partial<SlatePlayer> = {}): SlatePlayer {
  return {
    nba_player_id: '201939',
    name: 'Stephen Curry',
    name_is_placeholder: false,
    team_abbr: 'GSW',
    prob_active: 0.82,
    proj_pts: 16.4,
    proj_pts_cond: 20.2,
    proj_min_p50: 24.6,
    projected: { reb: 4.6, ast: 6.1, stl: 1.2, blk: 0.3, tov: 2.8, fg3m: 4.4 },
    usual_min: 28,
    usual_pts: 22,
    min_vs_usual: -3.4,
    pts_vs_usual: -1.8,
    baseline_games: 15,
    impact: 6.2,
    edge: 0.4,
    vs_usual: {
      minutes: { usual: 28, projected: 24.6, delta: -3.4 },
      points: { usual: 22, projected: 20.2, delta: -1.8 },
      categories: [],
    },
    reasons: [],
    evidence: {},
    spotlight: false,
    slate_spotlight: false,
    ...overrides,
  };
}

function game(overrides: Partial<SlateGame> = {}): SlateGame {
  return {
    nba_game_id: '0022500555',
    game_status: 'Scheduled',
    home_team_id: '1610612744',
    home_team_abbr: 'GSW',
    away_team_id: '1610612747',
    away_team_abbr: 'LAL',
    preseason: false,
    top_impact: 6.2,
    top_edge: 0.4,
    players: [],
    ...overrides,
  };
}

function tonight(player: SlatePlayer, slateGame: SlateGame = game()) {
  return toProjectionRow({ kind: 'tonight', player, game: slateGame });
}

function weekPlayer(overrides: Partial<WatchlistPlayer> = {}): WatchlistPlayer {
  return {
    nba_player_id: '1629630',
    name: 'Ja Morant',
    name_is_placeholder: false,
    team_abbr: 'MEM',
    position: 'PG',
    game_date: '2026-10-20',
    nba_game_id: '0022600120',
    opponent_team_abbr: 'UTA',
    preseason: false,
    games_count: 3,
    games: [
      { game_date: '2026-10-20', nba_game_id: '0022600120', opponent_team_abbr: 'UTA', preseason: false, minutes_p50: 30, proj_pts: 20, impact: 2, score: 0.4 },
      { game_date: '2026-10-22', nba_game_id: '0022600133', opponent_team_abbr: 'GSW', preseason: false, minutes_p50: 31, proj_pts: 22, impact: 2, score: 0.3 },
      { game_date: '2026-10-24', nba_game_id: '0022600150', opponent_team_abbr: 'SAC', preseason: false, minutes_p50: 29, proj_pts: 18, impact: 2, score: 0.2 },
    ],
    score: 0.9,
    score_per_game: 0.3,
    upside: 0.5,
    drivers: [],
    relevance: 0.6,
    impact: 6,
    impact_percentile: 80,
    prob_active: 0.9,
    minutes: { usual: 26, projected: 30, delta: 4 },
    points: { usual: 19, projected: 23.4, delta: 4.4 },
    totals: {},
    baseline_games: 15,
    reasons: [],
    evidence: {},
    ...overrides,
  };
}

describe('toProjectionRow, tonight', () => {
  it('writes the first line in plain words, rounded to whole numbers', () => {
    // act
    const row = tonight(slatePlayer());

    // assert
    expect(row.sentence).toBe('Stephen Curry · GSW vs LAL · 20 pts, 25 min if he plays · 82% to play');
  });

  it('marks the road team with an at sign', () => {
    // act
    const row = tonight(slatePlayer({ team_abbr: 'LAL', name: 'LeBron James' }));

    // assert
    expect(row.matchup).toBe('LAL @ GSW');
  });

  it('says nothing on the second line when no reason fired', () => {
    // act + assert
    expect(tonight(slatePlayer()).note).toBeNull();
  });

  it('pairs a role increase with the teammate who is out', () => {
    // arrange
    const player = slatePlayer({
      reasons: ['ROLE_INCREASE', 'TEAMMATE_ABSENCE'],
      evidence: { teammate_out: 'Jimmy Butler', teammate_out_minutes: 33, teammate_out_prob_active: 0.02 },
    });

    // act
    const row = tonight(player);

    // assert
    expect(row.note).toBe('Up from his usual 28 min: Jimmy Butler is out.');
  });

  it('says a role increase on its own without a teammate', () => {
    // act
    const row = tonight(slatePlayer({ reasons: ['ROLE_INCREASE'] }));

    // assert
    expect(row.note).toBe('Up from his usual 28 min.');
  });

  it('says a teammate absence on its own, hedged when he might still play', () => {
    // arrange
    const player = slatePlayer({
      reasons: ['TEAMMATE_ABSENCE'],
      evidence: { teammate_out: 'Draymond Green', teammate_out_minutes: 30, teammate_out_prob_active: 0.3 },
    });

    // act
    const row = tonight(player);

    // assert
    expect(row.note).toBe('Draymond Green is unlikely to play, which opens up minutes.');
  });

  it('says how long a returning player has been out', () => {
    // arrange
    const player = slatePlayer({
      reasons: ['RETURNING_FROM_ABSENCE'],
      evidence: { days_since_played: 12, last_played_date: '2026-01-23' },
    });

    // act
    const row = tonight(player);

    // assert
    expect(row.note).toBe('Back after 12 days without a game.');
  });

  it('turns the injury status into plain words', () => {
    // arrange
    const words = (status: string, raw: string): string | undefined =>
      tonight(slatePlayer({ injury_status: status, injury_status_raw: raw })).injury?.label;

    // act + assert
    expect(words('out', 'OUT')).toBe('Out');
    expect(words('questionable', 'GTD')).toBe('Questionable');
    expect(words('probable', 'PROB')).toBe('Probable');
    expect(words('doubtful', 'D')).toBe('Doubtful');
  });

  it('flags a designation that moved after the numbers were published', () => {
    // arrange
    const player = slatePlayer({
      injury_status: 'out',
      injury_status_raw: 'Out',
      injury_detail: 'Left ankle',
      injury_changed_after_run: true,
    });

    // act
    const row = tonight(player);

    // assert
    expect(row.injury?.label).toBe('Out (new)');
    expect(row.injury?.tone).toBe('error');
    expect(row.evidence.at(-1)).toContain('so they do not reflect it');
  });

  it('calls a cleared designation cleared, and a healthy player has no chip', () => {
    // act + assert
    expect(tonight(slatePlayer({ injury_status: null, injury_changed_after_run: true })).injury?.label).toBe(
      'Cleared'
    );
    expect(tonight(slatePlayer()).injury).toBeNull();
  });

  it('tags a preseason game', () => {
    // act + assert
    expect(tonight(slatePlayer(), game({ preseason: true })).preseason).toBe(true);
    expect(tonight(slatePlayer()).preseason).toBe(false);
  });

  it('keeps the category line and the sits sentence for the details', () => {
    // act
    const row = tonight(slatePlayer());

    // assert
    expect(row.categories).toBe('4.6 REB · 6.1 AST · 1.2 STL · 0.3 BLK · 4.4 3PM · 2.8 TO');
    expect(row.sitsSentence).toBe('16.4 points averaged over the chance he sits.');
  });

  it('drops the chance and the sits sentence when availability is not modelled', () => {
    // act
    const row = tonight(slatePlayer({ prob_active: null }));

    // assert
    expect(row.chance).toBeNull();
    expect(row.sitsSentence).toBeNull();
    expect(row.sentence).toBe('Stephen Curry · GSW vs LAL · 20 pts, 25 min if he plays');
  });
});

describe('toProjectionRow, next 7 days', () => {
  it('counts the games and writes per-game numbers', () => {
    // act
    const row = toProjectionRow({ kind: 'week', player: weekPlayer() });

    // assert
    expect(row.sentence).toBe('Ja Morant · MEM, 3 games · 23 pts, 30 min a game if he plays · 90% to play');
    expect(row.sitsSentence).toBe('20.0 points a game averaged over the chance he sits.');
    expect(row.games).toHaveLength(3);
  });

  it('names the opponent for a single game and keeps no breakdown', () => {
    // arrange
    const player = weekPlayer({ games_count: 1, games: weekPlayer().games.slice(0, 1) });

    // act
    const row = toProjectionRow({ kind: 'week', player });

    // assert
    expect(row.matchup).toBe('MEM vs UTA');
    expect(row.games).toEqual([]);
  });

  it('uses the same reason sentence as tonight', () => {
    // act
    const row = toProjectionRow({ kind: 'week', player: weekPlayer({ reasons: ['ROLE_INCREASE'] }) });

    // assert
    expect(row.note).toBe('Up from his usual 26 min.');
  });

  it('tags the row when any game in the window is preseason', () => {
    // arrange
    const games = weekPlayer().games.map((g, i) => ({ ...g, preseason: i === 2 }));

    // act + assert
    expect(toProjectionRow({ kind: 'week', player: weekPlayer({ games }) }).preseason).toBe(true);
  });
});
