import type { SlatePlayer, SlateResponse, SlateSort } from '../../src/types';

const BASELINE = {
  window_games: 15,
  min_games: 5,
  notable_min_delta: 4,
  label: 'his own recent form',
  definition: 'per-game averages over his last 15 games played before this date, requiring at least 5',
};

function player(overrides: Partial<SlatePlayer>): SlatePlayer {
  return {
    nba_player_id: '0',
    name: 'Player',
    name_is_placeholder: false,
    team_abbr: 'OKC',
    prob_active: 0.95,
    proj_pts: 10,
    proj_pts_cond: 10.5,
    proj_min_p50: 28,
    projected: { reb: 4, ast: 3, stl: 1, blk: 0.5, tov: 1.5, fg3m: 1.2 },
    usual_min: 28,
    usual_pts: 10,
    min_vs_usual: 0,
    pts_vs_usual: 0,
    baseline_games: 15,
    impact: 0,
    edge: 0,
    vs_usual: {
      minutes: { usual: 28, projected: 28, delta: 0 },
      points: { usual: 10, projected: 10, delta: 0 },
      categories: [],
    },
    reasons: [],
    evidence: {},
    spotlight: false,
    slate_spotlight: false,
    ...overrides,
  };
}

const STAR = player({
  nba_player_id: '1628983',
  name: 'Steady Star',
  proj_pts: 31.2,
  proj_pts_cond: 32.8,
  proj_min_p50: 35,
  impact: 9.4,
  edge: 0.1,
  spotlight: true,
  slate_spotlight: true,
});

const RISER = player({
  nba_player_id: '1631096',
  name: 'Bench Riser',
  proj_pts: 14.8,
  proj_pts_cond: 15.6,
  proj_min_p50: 31,
  usual_min: 21,
  min_vs_usual: 10,
  impact: 1.2,
  edge: 1.8,
  vs_usual: {
    minutes: { usual: 21, projected: 31, delta: 10 },
    points: { usual: 9.5, projected: 15.6, delta: 6.1 },
    categories: [
      { stat: 'reb', usual: 4.1, projected: 6.3, delta: 2.2 },
      { stat: 'ast', usual: 1.8, projected: 3, delta: 1.2 },
    ],
  },
  reasons: ['ROLE_INCREASE', 'TEAMMATE_ABSENCE'],
  evidence: { teammate_out: 'Hurt Starter', teammate_out_minutes: 33.4, teammate_out_prob_active: 0.05 },
});

// impact puts the star first; edge puts the riser first, which is the point of the toggle.
export function slateFixture(params: URLSearchParams): SlateResponse {
  const sort: SlateSort = params.get('sort') === 'edge' ? 'edge' : 'impact';
  return {
    date: params.get('date') ?? '2026-02-04',
    sort,
    run: {
      model_version: 'v1-decomposed',
      predicted_at: '2026-02-04T11:00:00Z',
      information_as_of: '2026-02-04T10:45:00Z',
      covers_from: '2026-02-04',
      covers_to: '2026-02-10',
    },
    covered: true,
    pool: {
      key: 'slate',
      label: "Tonight's slate",
      definition: "every player the run projects for this date, across all of the date's games",
      sample_size: 2,
    },
    baseline: BASELINE,
    games: [
      {
        nba_game_id: '0022500555',
        game_status: 'Scheduled',
        home_team_id: '1610612760',
        home_team_abbr: 'OKC',
        away_team_id: '1610612747',
        away_team_abbr: 'LAL',
        preseason: false,
        top_impact: 9.4,
        top_edge: 1.8,
        players: sort === 'edge' ? [RISER, STAR] : [STAR, RISER],
      },
      {
        nba_game_id: '0012500101',
        game_status: 'Scheduled',
        home_team_id: '1610612744',
        home_team_abbr: 'GSW',
        away_team_id: '1610612756',
        away_team_abbr: 'PHX',
        preseason: true,
        top_impact: null,
        top_edge: null,
        players: [],
      },
    ],
  };
}

// the date sits past the week the latest run looks ahead, so only the schedule is known.
export function uncoveredSlateFixture(params: URLSearchParams): SlateResponse {
  const base = slateFixture(params);
  return {
    ...base,
    date: '2026-10-20',
    covered: false,
    run: base.run && { ...base.run, covers_from: '2026-10-01', covers_to: '2026-10-07' },
    pool: { ...base.pool, sample_size: 0 },
    games: base.games.map((game) => ({ ...game, top_impact: null, top_edge: null, players: [] })),
  };
}
