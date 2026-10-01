import { describe, it, expect } from 'vitest';
import {
  candidatePicks,
  clvPoints,
  dropSupersededLines,
  forecastKey,
  gradePick,
  outcomeFromRow,
  selectPicks,
  summarizePicks,
  type PlayerGameForecast,
  type PropPick,
  type PropSnapshot,
  type SettleRow,
  type SummaryRow,
} from '../../src/services/propPicks.js';

function snapshot(over: Partial<PropSnapshot> = {}): PropSnapshot {
  return {
    nba_player_id: '201939',
    player_name: 'Stephen Curry',
    nba_game_id: '0022600001',
    game_date: '2026-10-21',
    bookmaker: 'draftkings',
    market: 'pts',
    line: 24.5,
    over_price: -110,
    under_price: -110,
    captured_at: '2026-10-21T18:00:00.000Z',
    ...over,
  };
}

function pick(over: Partial<PropPick> = {}): PropPick {
  return {
    nba_player_id: '201939',
    player_name: null,
    nba_game_id: '0022600001',
    game_date: '2026-10-21',
    market: 'pts',
    line: 24.5,
    side: 'over',
    bookmaker: 'draftkings',
    price: -110,
    implied_prob: 0.5238,
    implied_prob_novig: 0.5,
    model_prob: 0.6,
    model_prob_plays: 0.6,
    prob_active: 0.9,
    void_rule: 'dnp_void',
    ev: 0.05,
    kelly_fraction: 0.01,
    edge: 0.1,
    ...over,
  };
}

function summaryRow(over: Partial<SummaryRow> = {}): SummaryRow {
  return {
    market: 'pts',
    result: 'win',
    price: 100,
    line: 24.5,
    ev: 0.05,
    model_prob: 0.55,
    closing_price: null,
    closing_line: null,
    ...over,
  };
}

describe('candidatePicks', () => {
  it('prices both sides against the stored quantiles and the no-vig market', () => {
    // arrange
    const forecasts = new Map<string, PlayerGameForecast>([
      [
        forecastKey('201939', '0022600001'),
        { prob_active: 0.95, stats: { pts: { expected: 28, p10: 20, p50: 28, p90: 36 } } },
      ],
    ]);

    // act
    const picks = candidatePicks([snapshot()], forecasts);

    // assert
    expect(picks.map((p) => p.side)).toEqual(['over', 'under']);
    expect(picks[0].model_prob).toBeCloseTo(0.675, 10);
    expect(picks[0].implied_prob_novig).toBeCloseTo(0.5, 10);
    expect(picks[0].edge).toBeCloseTo(0.175, 10);
    expect(picks[0].ev).toBeGreaterThan(0);
    expect(picks[1].ev).toBeLessThan(0);
  });

  it('skips a player-game the run did not forecast and a side with no price', () => {
    // arrange
    const forecasts = new Map<string, PlayerGameForecast>([
      [
        forecastKey('201939', '0022600001'),
        { prob_active: 0.95, stats: { pts: { expected: 28, p10: 20, p50: 28, p90: 36 } } },
      ],
    ]);
    const snaps = [snapshot({ under_price: null }), snapshot({ nba_player_id: '999' })];

    // act
    const picks = candidatePicks(snaps, forecasts);

    // assert
    expect(picks).toHaveLength(1);
    expect(picks[0].side).toBe('over');
    expect(picks[0].implied_prob_novig).toBeNull();
  });
});

describe('selectPicks', () => {
  it('keeps picks above both the EV and prob_active thresholds, best EV first', () => {
    // arrange
    const candidates = [
      pick({ ev: 0.03, line: 20.5 }),
      pick({ ev: 0.02, line: 21.5 }),
      pick({ ev: 0.1, prob_active: 0.6, line: 22.5 }),
      pick({ ev: 0.08, line: 23.5 }),
    ];

    // act
    const kept = selectPicks(candidates);

    // assert
    expect(kept.map((p) => p.line)).toEqual([23.5, 20.5]);
  });
});

describe('dropSupersededLines', () => {
  it('drops a line the book stopped re-posting after it moved', () => {
    // arrange
    const snaps = [
      snapshot({ line: 24.5, captured_at: '2026-10-21T12:00:00.000Z' }),
      snapshot({ line: 25.5, captured_at: '2026-10-21T18:00:00.000Z' }),
      snapshot({ line: 26.5, captured_at: '2026-10-21T17:55:00.000Z' }),
    ];

    // act
    const kept = dropSupersededLines(snaps);

    // assert
    expect(kept.map((s) => s.line)).toEqual([25.5, 26.5]);
  });
});

describe('gradePick', () => {
  const played = (pts: number | null) => ({ played: true, stats: { pts, reb: 7, ast: 5 } });

  it('grades an over win and an over loss', () => {
    // act
    const win = gradePick(pick(), played(30));
    const loss = gradePick(pick(), played(20));

    // assert
    expect(win).toEqual({ result: 'win', actual: 30 });
    expect(loss).toEqual({ result: 'loss', actual: 20 });
  });

  it('grades an under and a push on an integer line', () => {
    // act
    const under = gradePick(pick({ side: 'under' }), played(20));
    const push = gradePick(pick({ line: 25 }), played(25));

    // assert
    expect(under?.result).toBe('win');
    expect(push).toEqual({ result: 'push', actual: 25 });
  });

  it('sums PRA from its three components', () => {
    // act
    const graded = gradePick(pick({ market: 'pra', line: 39.5 }), played(28));

    // assert
    expect(graded).toEqual({ result: 'win', actual: 40 });
  });

  it('voids a DNP under dnp_void and grades it a loss under dnp_loss', () => {
    // arrange
    const dnp = { played: false, stats: {} };

    // act
    const voided = gradePick(pick(), dnp);
    const lost = gradePick(pick({ void_rule: 'dnp_loss' }), dnp);

    // assert
    expect(voided).toEqual({ result: 'void', actual: null });
    expect(lost).toEqual({ result: 'loss', actual: null });
  });

  it('leaves a pick ungraded when he played but the stat is missing', () => {
    // act + assert
    expect(gradePick(pick(), played(null))).toBeNull();
  });
});

describe('outcomeFromRow', () => {
  function row(over: Partial<SettleRow>): SettleRow {
    return {
      id: 1, market: 'pts', line: 24.5, side: 'over', void_rule: 'dnp_void',
      has_log: true, minutes: 30, pts: 25, reb: 5, ast: 4, fg3m: 3, stl: 1, blk: 0, tov: 2,
      closing_line: null, closing_price: null,
      ...over,
    };
  }

  it('treats a missing log row or zero minutes as did not play', () => {
    // act
    const noLog = outcomeFromRow(row({ has_log: false, minutes: null, pts: null }));
    const zero = outcomeFromRow(row({ minutes: 0 }));
    const playedRow = outcomeFromRow(row({}));

    // assert
    expect(noLog.played).toBe(false);
    expect(zero.played).toBe(false);
    expect(playedRow.played).toBe(true);
  });
});

describe('clvPoints', () => {
  it('is closing implied minus surfaced implied in percentage points', () => {
    // act
    const clv = clvPoints({ price: 100, line: 24.5 }, { price: -120, line: 24.5 });

    // assert
    expect(clv).toBeCloseTo((120 / 220 - 0.5) * 100, 10);
  });

  it('is undefined when the line moved or there is no close', () => {
    // act
    const moved = clvPoints({ price: 100, line: 24.5 }, { price: -120, line: 25.5 });
    const none = clvPoints({ price: 100, line: 24.5 }, { price: null, line: null });

    // assert
    expect(moved).toBeNull();
    expect(none).toBeNull();
  });
});

describe('summarizePicks', () => {
  it('counts results, hit rate, roi, calibration and clv overall and by market', () => {
    // arrange
    const rows = [
      summaryRow({ result: 'win', closing_price: -120, closing_line: 24.5 }),
      summaryRow({ result: 'loss', model_prob: 0.45 }),
      summaryRow({ result: 'void' }),
      summaryRow({ result: null }),
      summaryRow({ market: 'reb', result: 'push', line: 8 }),
    ];

    // act
    const [all, pts, reb] = summarizePicks(rows);

    // assert
    expect(all.market).toBe('all');
    expect(all.picks).toBe(5);
    expect(all.settled).toBe(4);
    expect(pts).toMatchObject({ market: 'pts', wins: 1, losses: 1, voids: 1, hit_rate: 0.5, roi: 0 });
    expect(pts.avg_model_prob).toBeCloseTo(0.5, 10);
    expect(pts.calibration_gap_points).toBeCloseTo(0, 10);
    expect(pts.clv_count).toBe(1);
    expect(pts.avg_clv_points).toBeCloseTo(4.55, 2);
    expect(reb).toMatchObject({ market: 'reb', pushes: 1, hit_rate: 0 });
  });

  it('returns nothing for an empty ledger', () => {
    // act + assert
    expect(summarizePicks([])).toEqual([]);
  });
});
