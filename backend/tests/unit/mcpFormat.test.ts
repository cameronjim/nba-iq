import { describe, it, expect } from 'vitest';
import {
  fmt,
  pct,
  signed,
  formatSlate,
  formatWatchlist,
  formatProjections,
  formatAnalytics,
  formatPlayersList,
  formatStatLeaders,
} from '../../src/mcp/format.js';
import type { SlateResponse, SlateGame, SlatePlayer } from '../../src/services/slate.js';
import type {
  WatchlistResponse,
  WatchlistPlayer,
  WatchlistGame,
} from '../../src/services/watchlist.js';
import type {
  PlayerPredictionsResponse,
  UpcomingGamePrediction,
} from '../../src/services/playerPredictions.js';
import type { PlayerAnalytics } from '../../src/services/analytics.js';
import type { PlayerWithScore } from '../../src/services/fantasyScore.js';

const NO_JUNK = /\b(undefined|null|NaN)\b/;

function slatePlayer(overrides: Partial<SlatePlayer> = {}): SlatePlayer {
  return {
    nba_player_id: '1',
    name: 'Nikola Jokic',
    name_is_placeholder: false,
    team_abbr: 'DEN',
    prob_active: 0.98,
    proj_pts: 27.9,
    proj_pts_cond: 28.5,
    proj_min_p50: 34.1,
    projected: { reb: 12.1, ast: 9.4, stl: 1.3, blk: 0.8, tov: 3.2, fg3m: 1.1 },
    usual_min: 33.6,
    usual_pts: 26.7,
    min_vs_usual: 0.5,
    pts_vs_usual: 1.2,
    baseline_games: 15,
    impact: 8.42,
    edge: 0.2,
    vs_usual: null,
    reasons: [],
    evidence: {},
    spotlight: false,
    slate_spotlight: false,
    injury_status: null,
    injury_status_raw: null,
    injury_detail: null,
    injury_as_of: null,
    injury_changed_after_run: false,
    ...overrides,
  };
}

function slateGame(overrides: Partial<SlateGame> = {}): SlateGame {
  return {
    nba_game_id: 'g1',
    game_status: 'scheduled',
    home_team_id: '2',
    home_team_abbr: 'BOS',
    away_team_id: '1',
    away_team_abbr: 'DEN',
    preseason: false,
    top_impact: 8.42,
    top_edge: 0.2,
    players: [slatePlayer()],
    ...overrides,
  };
}

function slateResponse(overrides: Partial<SlateResponse> = {}): SlateResponse {
  return {
    date: '2026-08-26',
    sort: 'impact',
    run: {
      model_version: 'v3.2.1',
      predicted_at: '2026-08-26T11:02:00Z',
      information_as_of: '2026-08-26T11:00:00Z',
      covers_from: '2026-08-26',
      covers_to: '2026-09-01',
    },
    covered: true,
    pool: { key: 'slate', label: "Tonight's slate", definition: 'every player projected', sample_size: 212 },
    baseline: {
      window_games: 15,
      min_games: 5,
      notable_min_delta: 4,
      label: 'his own recent form',
      definition: 'per-game averages over his last 15 games played before this date, requiring at least 5',
      season_types: ['Regular Season'],
      includes_postseason: false,
    },
    games: [slateGame()],
    ...overrides,
  };
}

describe('fmt/pct/signed', () => {
  it('fmt renders — for null and undefined, and passes through numbers with a suffix', () => {
    // act / assert
    expect(fmt(null)).toBe('—');
    expect(fmt(undefined)).toBe('—');
    expect(fmt(0)).toBe('0');
    expect(fmt(4.2, ' pts')).toBe('4.2 pts');
  });

  it('pct renders a rounded percentage or — for null', () => {
    // act / assert
    expect(pct(0.981)).toBe('98%');
    expect(pct(null)).toBe('—');
  });

  it('signed prefixes positive and zero values with +, keeps negatives as-is', () => {
    // act / assert
    expect(signed(4.2)).toBe('+4.2');
    expect(signed(-1.3)).toBe('-1.3');
    expect(signed(0)).toBe('+0');
    expect(signed(null)).toBe('—');
  });
});

describe('formatSlate', () => {
  it('renders the game header, a ranked player line, and never emits undefined/null/NaN', () => {
    // arrange
    const slate = slateResponse();

    // act
    const text = formatSlate(slate, 5);

    // assert
    expect(text).toContain('DEN @ BOS (scheduled)');
    expect(text).toContain('1. Nikola Jokic (DEN) impact 8.42');
    expect(NO_JUNK.test(text)).toBe(false);
  });

  it('adds an injury suffix with changed after run and a spotlight marker', () => {
    // arrange
    const player = slatePlayer({
      injury_status: 'Questionable',
      injury_detail: 'ankle',
      injury_changed_after_run: true,
      slate_spotlight: true,
    });
    const slate = slateResponse({ games: [slateGame({ players: [player] })] });

    // act
    const text = formatSlate(slate, 5);

    // assert
    expect(text).toContain('Questionable: ankle, changed after run');
    expect(text).toContain('*slate spotlight*');
  });

  it('names the reasons a row departs from his usual, and stays quiet when none fired', () => {
    // arrange
    const flagged = slatePlayer({ reasons: ['ROLE_INCREASE', 'TEAMMATE_ABSENCE'] });

    // act
    const withReasons = formatSlate(slateResponse({ games: [slateGame({ players: [flagged] })] }), 5);
    const without = formatSlate(slateResponse(), 5);

    // assert
    expect(withReasons).toContain('why: ROLE_INCREASE, TEAMMATE_ABSENCE');
    expect(without).not.toContain('why:');
  });

  it('renders the no-run notice and marks each game as having no projections', () => {
    // arrange
    const slate = slateResponse({ run: null, games: [slateGame({ players: [] })] });

    // act
    const text = formatSlate(slate, 5);

    // assert
    expect(text).toContain('No completed prediction run; games listed without projections.');
    expect(text).toContain('(no projections)');
  });

  it('renders the no-games line when nothing is scheduled', () => {
    // arrange
    const slate = slateResponse({ games: [] });

    // act
    const text = formatSlate(slate, 5);

    // assert
    expect(text).toContain('No NBA games scheduled for 2026-08-26.');
  });

  it('respects players_per_game', () => {
    // arrange
    const players = [
      slatePlayer({ nba_player_id: '1', name: 'A' }),
      slatePlayer({ nba_player_id: '2', name: 'B' }),
      slatePlayer({ nba_player_id: '3', name: 'C' }),
    ];
    const slate = slateResponse({ games: [slateGame({ players })] });

    // act
    const text = formatSlate(slate, 2);

    // assert
    expect(text).toContain('1. A');
    expect(text).toContain('2. B');
    expect(text).not.toContain('3. C');
  });
});

function watchlistGame(overrides: Partial<WatchlistGame> = {}): WatchlistGame {
  return {
    game_date: '2026-08-26',
    nba_game_id: 'g1',
    opponent_team_abbr: 'LAL',
    preseason: false,
    minutes_p50: 34.0,
    proj_pts: 21.3,
    impact: 5.1,
    score: 0.61,
    ...overrides,
  };
}

function watchlistPlayer(overrides: Partial<WatchlistPlayer> = {}): WatchlistPlayer {
  return {
    nba_player_id: '10',
    name: 'Deni Avdija',
    name_is_placeholder: false,
    team_abbr: 'POR',
    position: 'SF/PF',
    game_date: '2026-08-26',
    nba_game_id: 'g1',
    opponent_team_abbr: 'LAL',
    preseason: false,
    games_count: 3,
    games: [
      watchlistGame({ game_date: '2026-08-26', opponent_team_abbr: 'LAL' }),
      watchlistGame({ game_date: '2026-08-27', opponent_team_abbr: 'GSW' }),
      watchlistGame({ game_date: '2026-08-28', opponent_team_abbr: 'SAC' }),
    ],
    score: 1.84,
    score_per_game: 0.61,
    upside: 0.7,
    drivers: [],
    relevance: 0.8,
    impact: 5.1,
    impact_percentile: 88.5,
    prob_active: 0.91,
    minutes: { usual: 28.1, projected: 34.0, delta: 5.9 },
    points: { usual: 16.2, projected: 21.0, delta: 4.8 },
    totals: {},
    baseline_games: 15,
    reasons: ['ROLE_INCREASE', 'TEAMMATE_ABSENCE'],
    evidence: {
      teammate_out: 'Jerami Grant',
      teammate_out_minutes: 31.2,
      teammate_out_prob_active: 0.1,
    },
    ...overrides,
  };
}

function watchlistResponse(overrides: Partial<WatchlistResponse> = {}): WatchlistResponse {
  return {
    date: '2026-08-26',
    window: { from: '2026-08-26', to: '2026-08-28', days: 3 },
    run: { model_version: 'v3.2.1', predicted_at: '2026-08-26T11:02:00Z' },
    pool: { key: 'slate', label: "Each night's slate", definition: 'every player projected', sample_size: 640 },
    baseline: {
      window_games: 15,
      min_games: 5,
      notable_min_delta: 4,
      label: 'his own recent form',
      definition: 'per-game averages',
      season_types: ['Regular Season'],
      includes_postseason: false,
    },
    position: 'G',
    position_options: ['G', 'F', 'C', 'PG', 'SG', 'SF', 'PF'],
    position_coverage: { known: 180, unknown: 12 },
    players: [watchlistPlayer()],
    ...overrides,
  };
}

describe('formatWatchlist', () => {
  it('renders reasons and every present evidence key phrasing', () => {
    // arrange
    const player = watchlistPlayer({
      reasons: ['SHOT_VOLUME_SURGE', 'RETURNING_FROM_ABSENCE', 'HOT_STREAK', 'TEAMMATE_ABSENCE'],
      evidence: {
        fga_usual: 12.1,
        fga_projected: 15.0,
        fga_delta: 2.9,
        days_since_played: 10,
        last_played_date: '2026-08-16',
        pts_recent: 20.0,
        pts_sd: 4.0,
        pts_recent_delta: 5.0,
        teammate_out: 'Jerami Grant',
        teammate_out_minutes: 31.2,
        teammate_out_prob_active: 0.1,
      },
    });
    const response = watchlistResponse({ players: [player] });

    // act
    const text = formatWatchlist(response);

    // assert
    expect(text).toContain('reasons: SHOT_VOLUME_SURGE, RETURNING_FROM_ABSENCE, HOT_STREAK, TEAMMATE_ABSENCE');
    expect(text).toContain('fga 12.1 usual -> 15 proj (+2.9)');
    expect(text).toContain('last played 2026-08-16 (10 days ago)');
    expect(text).toContain('last-5 pts 20 vs usual (+5, sd 4)');
    expect(text).toContain('teammate out: Jerami Grant (31.2 usual min, 10% active)');
  });

  it('caps the games line at 5 entries with a (+N more) suffix', () => {
    // arrange
    const games = Array.from({ length: 7 }, (_, i) =>
      watchlistGame({ game_date: `2026-08-${20 + i}`, opponent_team_abbr: `T${i}` })
    );
    const player = watchlistPlayer({ games_count: 7, games });
    const response = watchlistResponse({ players: [player] });

    // act
    const text = formatWatchlist(response);

    // assert
    expect(text).toContain('(+2 more)');
    expect(text).not.toContain('T6');
  });

  it('renders the empty-players message while still printing the header', () => {
    // arrange
    const response = watchlistResponse({ players: [] });

    // act
    const text = formatWatchlist(response);

    // assert
    expect(text).toContain('Watchlist 2026-08-26 to 2026-08-28');
    expect(text).toContain('No watchlist candidates for this window.');
  });

  it('renders vs ??? when the opponent is null', () => {
    // arrange
    const games = [
      watchlistGame({ game_date: '2026-08-26', opponent_team_abbr: null }),
      watchlistGame({ game_date: '2026-08-27', opponent_team_abbr: 'GSW' }),
    ];
    const player = watchlistPlayer({ games_count: 2, games });
    const response = watchlistResponse({ players: [player] });

    // act
    const text = formatWatchlist(response);

    // assert
    expect(text).toContain('vs ???');
  });
});

function upcomingGame(overrides: Partial<UpcomingGamePrediction> = {}): UpcomingGamePrediction {
  return {
    nba_game_id: 'g1',
    game_date: '2026-08-27',
    opponent_abbr: 'BOS',
    is_home: true,
    game_status: 'scheduled',
    prob_active: 0.97,
    prob_active_model: 0.97,
    stats: {
      minutes: { expected: null, p10: null, p50: 34.5, p90: null, unconditional: null },
      pts: { expected: 27.9, p10: 17.5, p50: null, p90: 38.0, unconditional: 27.3 },
      reb: { expected: 12.1, p10: null, p50: null, p90: null, unconditional: null },
    },
    ...overrides,
  };
}

function projectionsPayload(
  overrides: Partial<Omit<PlayerPredictionsResponse, 'player_id' | 'nba_player_id'>> = {}
): Omit<PlayerPredictionsResponse, 'player_id' | 'nba_player_id'> {
  return {
    run: {
      id: 1,
      model_version: 'v3.2.1',
      feature_version: null,
      predicted_at: '2026-08-26T11:02:00Z',
      forecast_cutoff_at: null,
      horizon: '14d',
    },
    stats: ['minutes', 'pts', 'reb'],
    games: [upcomingGame()],
    ...overrides,
  };
}

describe('formatProjections', () => {
  const player = { id: 431, nba_id: '203999', name: 'Nikola Jokic', team: 'DEN', position: 'C' };

  it('orders stats and includes the pts p10/p90/uncond parenthetical', () => {
    // arrange
    const payload = projectionsPayload();

    // act
    const text = formatProjections(player, payload);

    // assert
    const statLineIndex = text.indexOf('pts 27.9 exp');
    expect(statLineIndex).toBeGreaterThan(-1);
    expect(text).toContain('pts 27.9 exp (p10 17.5 / p90 38, uncond 27.3)');
    expect(text.indexOf('pts')).toBeLessThan(text.indexOf('reb'));
  });

  it('skips stats whose line is entirely null', () => {
    // arrange
    const payload = projectionsPayload({
      games: [
        upcomingGame({
          stats: {
            minutes: { expected: null, p10: null, p50: 34.5, p90: null, unconditional: null },
            pts: { expected: null, p10: null, p50: null, p90: null, unconditional: null },
            reb: { expected: 12.1, p10: null, p50: null, p90: null, unconditional: null },
          },
        }),
      ],
    });

    // act
    const text = formatProjections(player, payload);

    // assert
    expect(text).not.toContain('pts');
    expect(text).toContain('reb 12.1');
  });

  it('renders "opponent unknown" when is_home is null', () => {
    // arrange
    const payload = projectionsPayload({ games: [upcomingGame({ is_home: null, opponent_abbr: null })] });

    // act
    const text = formatProjections(player, payload);

    // assert
    expect(text).toContain('opponent unknown');
  });

  it('renders the no-run message when there is no completed run or no upcoming games', () => {
    // act / assert
    expect(formatProjections(player, projectionsPayload({ run: null }))).toContain(
      'No completed prediction run covers upcoming games for Nikola Jokic.'
    );
    expect(formatProjections(player, projectionsPayload({ games: [] }))).toContain(
      'No completed prediction run covers upcoming games for Nikola Jokic.'
    );
  });
});

function playerAnalytics(overrides: Partial<PlayerAnalytics> = {}): PlayerAnalytics {
  return {
    player: {
      id: 431,
      nba_id: '203999',
      name: 'Nikola Jokic',
      team: 'DEN',
      position: 'C',
      headshot_url: null,
      injury_status: null,
      injury_detail: null,
    },
    as_of: { logs: '2026-04-11T00:00:00.000Z', distributions: '2026-04-12T00:00:00.000Z' },
    pool: { key: 'rotation', label: 'Rotation players', definition: 'GP >= 15 and MPG >= 12 this season', sample_size: 247 },
    percentiles: [
      { stat: 'pts', value: 26.1, percentile: 97 },
      { stat: 'reb', value: 12.4, percentile: 99 },
      { stat: 'ast', value: 9.0, percentile: 99 },
      { stat: 'stl', value: 1.3, percentile: 85 },
      { stat: 'blk', value: 0.7, percentile: 61 },
      { stat: 'fg3m', value: 1.2, percentile: 58 },
      { stat: 'tov', value: 3.1, percentile: 8 },
      { stat: 'fg_impact', value: 1.42, percentile: 99 },
      { stat: 'ft_impact', value: 0.31, percentile: 78 },
      { stat: 'minutes', value: 34.2, percentile: 91 },
    ],
    distributions: [],
    trends: {
      games: [
        {
          game_date: '2026-04-11',
          opponent_team_abbr: 'MEM',
          is_home: true,
          minutes: 36,
          pts: 31,
          reb: 14,
          ast: 11,
          stl: 2,
          blk: 1,
          tov: 4,
          fg3m: 2,
          fga: 20,
          fg3a: 5,
          fgm: 12,
          ftm: 5,
          fta: 6,
        },
      ],
      rolling: [],
      last10_vs_season: [
        { stat: 'pts', last10: 29.4, season: 26.1, delta: 3.3, z: 1.1 },
        { stat: 'reb', last10: 12.5, season: 12.4, delta: 0.1, z: 0.05 },
      ],
    },
    prediction: {
      as_of: '2026-08-26T11:02:00Z',
      model_version: 'v3.2.1',
      game_date: '2026-08-27',
      prob_active: 0.98,
      projected: {
        minutes: { p10: 28.0, p50: 34.5, p90: 39.5 },
        pts: { p10: 17.5, p50: 27.9, p90: 38.0 },
        reb: null,
        ast: null,
        stl: null,
        blk: null,
        tov: null,
        fg3m: null,
      },
      conditional: true,
      unconditional_pts: 27.3,
      summary: '98% to play, 34.5 min (28.0-39.5), 27.9 pts (17.5-38.0) if he plays, 27.3 pts averaged over the schedule.',
    },
    ...overrides,
  };
}

describe('formatAnalytics', () => {
  it('renders all 10 percentile stats', () => {
    // arrange
    const analytics = playerAnalytics();

    // act
    const text = formatAnalytics(analytics);

    // assert
    for (const stat of ['pts', 'reb', 'ast', 'stl', 'blk', 'fg3m', 'tov', 'fg_impact', 'ft_impact', 'minutes']) {
      expect(text).toContain(`${stat} `);
    }
  });

  it('filters last-10-vs-season to |z| >= 0.5 and reports no notable deviations otherwise', () => {
    // arrange
    const withDeviation = playerAnalytics();
    const withoutDeviation = playerAnalytics({
      trends: {
        ...playerAnalytics().trends,
        last10_vs_season: [{ stat: 'pts', last10: 26.2, season: 26.1, delta: 0.1, z: 0.1 }],
      },
    });

    // act
    const deviationText = formatAnalytics(withDeviation);
    const noDeviationText = formatAnalytics(withoutDeviation);

    // assert
    expect(deviationText).toContain('pts 29.4 vs 26.1 (+3.3, z 1.1)');
    expect(deviationText).not.toContain('reb 12.5 vs 12.4');
    expect(noDeviationText).toContain('no notable deviations');
  });

  it('renders no-prediction-available text when prediction is null', () => {
    // arrange
    const analytics = playerAnalytics({ prediction: null });

    // act
    const text = formatAnalytics(analytics);

    // assert
    expect(text).toContain('Next game: no prediction available.');
  });

  it('slices the game log to the last 10 games', () => {
    // arrange
    const games = Array.from({ length: 15 }, (_, i) => ({
      game_date: `2026-03-${i + 1 < 10 ? '0' : ''}${i + 1}`,
      opponent_team_abbr: `T${i}`,
      is_home: true,
      minutes: 30,
      pts: 20,
      reb: 10,
      ast: 5,
      stl: 1,
      blk: 1,
      tov: 2,
      fg3m: 1,
      fga: 15,
      fg3a: 3,
      fgm: 8,
      ftm: 4,
      fta: 5,
    }));
    const analytics = playerAnalytics({ trends: { ...playerAnalytics().trends, games } });

    // act
    const text = formatAnalytics(analytics);

    // assert
    expect(text).not.toContain('T0)');
    expect(text).toContain('T14');
  });
});

function playerWithScore(overrides: Partial<PlayerWithScore> = {}): PlayerWithScore {
  return {
    id: 431,
    nba_id: '203999',
    name: 'Nikola Jokic',
    team: 'DEN',
    position: 'C',
    points_per_game: 26.1,
    rebounds_per_game: 12.4,
    assists_per_game: 9.0,
    steals_per_game: 1.3,
    blocks_per_game: 0.7,
    field_goal_percentage: 58.0,
    free_throw_percentage: 80.0,
    three_point_percentage: 35.0,
    three_pointers_made: 1.2,
    turnovers_per_game: 3.1,
    minutes_per_game: 34.2,
    games_played: 68,
    injury_status: null,
    injury_detail: null,
    headshot_url: null,
    fantasy_score: 58.3,
    fantasy_rank: 1,
    ...overrides,
  };
}

describe('formatPlayersList', () => {
  it('renders unranked players below the GP/MPG thresholds', () => {
    // arrange
    const players = [playerWithScore({ fantasy_rank: null, fantasy_score: null })];

    // act
    const text = formatPlayersList(players, {});

    // assert
    expect(text).toContain('unranked.');
    expect(text).toContain('— fpts');
  });

  it('renders the empty-result message', () => {
    // act
    const text = formatPlayersList([], { position: 'G' });

    // assert
    expect(text).toContain('No players match.');
  });
});

describe('formatStatLeaders', () => {
  it('includes the tov note only when the stat is tov', () => {
    // arrange
    const distribution = {
      stat: 'pts' as const,
      pool: { key: 'rotation', label: 'Rotation players', definition: 'GP >= 15 and MPG >= 12 this season', sample_size: 247 },
      mean: 12.4,
      stddev: 6.1,
      buckets: [],
      players: [{ id: 12, name: 'Shai Gilgeous-Alexander', value: 32.8, percentile: 99.8 }],
    };

    // act
    const pointsText = formatStatLeaders(distribution, 10);
    const tovText = formatStatLeaders({ ...distribution, stat: 'tov' }, 10);

    // assert
    expect(pointsText).not.toContain('lower is better');
    expect(tovText).toContain('lower is better');
  });
});
