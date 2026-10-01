import { describe, it, expect } from 'vitest';
import { reasonEvidenceLines, signedStat, slateEvidenceLines } from '../../src/utils/vsUsual';
import type { SlatePlayer } from '../../src/types';

function player(overrides: Partial<SlatePlayer> = {}): SlatePlayer {
  return {
    nba_player_id: '1',
    name: 'Test',
    name_is_placeholder: false,
    team_abbr: 'OKC',
    prob_active: 0.9,
    proj_pts: 10,
    proj_min_p50: 25,
    projected: { reb: 4, ast: 3, stl: 1, blk: 0.5, tov: 1, fg3m: 1 },
    usual_min: 24,
    usual_pts: 9,
    min_vs_usual: 1,
    pts_vs_usual: 1,
    baseline_games: 15,
    impact: 0,
    edge: 0.2,
    vs_usual: {
      minutes: { usual: 24, projected: 25, delta: 1 },
      points: { usual: 9, projected: 10, delta: 1 },
      categories: [{ stat: 'blk', usual: 0.5, projected: 1.2, delta: 0.7 }],
    },
    reasons: [],
    evidence: {},
    spotlight: false,
    slate_spotlight: false,
    ...overrides,
  };
}

describe('signedStat', () => {
  it('signs a rise and keeps a drop negative', () => {
    expect(signedStat(2.25)).toBe('+2.3');
    expect(signedStat(-1)).toBe('-1.0');
  });
});

describe('slateEvidenceLines', () => {
  it('leads with minutes, then points, then the categories the server picked', () => {
    expect(slateEvidenceLines(player())).toEqual([
      'Minutes: 25.0 projected, usually 24.0 (+1.0)',
      'Points if he plays: 10.0 projected, usually 9.0 (+1.0)',
      'BLK: 1.2 projected, usually 0.5 (+0.7)',
    ]);
  });

  it('has only the reason evidence without a usual', () => {
    expect(slateEvidenceLines(player({ vs_usual: null }))).toEqual([]);
  });
});

describe('reasonEvidenceLines', () => {
  it('writes one line per reason that carried evidence', () => {
    const lines = reasonEvidenceLines(
      { pts_recent: 24, pts_recent_delta: 9, pts_sd: 4, days_since_played: 12 },
      15
    );

    expect(lines).toEqual([
      'Absence: 12 days without a game',
      'Recent form: 24.0 points over his last 5, usually 15.0',
    ]);
  });
});
