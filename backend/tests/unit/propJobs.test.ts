import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  refreshPropPicks,
  settlePropPicks,
  type RefreshResult,
  type SettleResult,
} from '../../src/services/propPicks.js';
import { handler } from '../../src/jobs/propJobs.js';

vi.mock('../../src/services/propPicks.js', () => ({
  refreshPropPicks: vi.fn(),
  settlePropPicks: vi.fn(),
}));

const refreshMock = vi.mocked(refreshPropPicks);
const settleMock = vi.mocked(settlePropPicks);

const REFRESH_RESULT: RefreshResult = {
  run: { id: 7, model_version: 'v3', predicted_at: null, information_as_of: null },
  snapshots: 40,
  candidates: 12,
  recorded: 3,
  picks: [],
};

const SETTLE_RESULT: SettleResult = {
  examined: 5,
  settled: 4,
  by_result: { win: 2, loss: 1, push: 1, void: 0 },
};

describe('prop jobs handler', () => {
  beforeEach(() => {
    refreshMock.mockReset();
    settleMock.mockReset();
  });

  it('runs refresh before settle', async () => {
    // arrange
    const order: string[] = [];
    refreshMock.mockImplementation(async () => {
      order.push('refresh');
      return REFRESH_RESULT;
    });
    settleMock.mockImplementation(async () => {
      order.push('settle');
      return SETTLE_RESULT;
    });

    // act
    await handler({});

    // assert
    expect(order).toEqual(['refresh', 'settle']);
  });

  it('returns both results with the pick list reduced to a count', async () => {
    // arrange
    refreshMock.mockResolvedValue(REFRESH_RESULT);
    settleMock.mockResolvedValue(SETTLE_RESULT);

    // act
    const summary = await handler({});

    // assert
    expect(summary).toEqual({
      refresh: {
        ok: true,
        result: { run: REFRESH_RESULT.run, snapshots: 40, candidates: 12, recorded: 3, picks: 0 },
      },
      settle: { ok: true, result: SETTLE_RESULT },
    });
  });

  it('still settles and reports the error when refresh fails', async () => {
    // arrange
    refreshMock.mockRejectedValue(new Error('no snapshots table'));
    settleMock.mockResolvedValue(SETTLE_RESULT);

    // act
    const summary = await handler({});

    // assert
    expect(settleMock).toHaveBeenCalledTimes(1);
    expect(summary.refresh).toEqual({ ok: false, error: 'no snapshots table' });
    expect(summary.settle).toEqual({ ok: true, result: SETTLE_RESULT });
  });

  it('reports a settle failure without discarding the refresh result', async () => {
    // arrange
    refreshMock.mockResolvedValue(REFRESH_RESULT);
    settleMock.mockRejectedValue(new Error('connection reset'));

    // act
    const summary = await handler({});

    // assert
    expect(summary.refresh.ok).toBe(true);
    expect(summary.settle).toEqual({ ok: false, error: 'connection reset' });
  });
});
