import {
  refreshPropPicks,
  settlePropPicks,
  type RefreshResult,
  type SettleResult,
} from '../services/propPicks.js';

export type JobOutcome<T> = { ok: true; result: T } | { ok: false; error: string };

export type RefreshCounts = Omit<RefreshResult, 'picks'> & { picks: number };

export interface PropJobsSummary {
  refresh: JobOutcome<RefreshCounts>;
  settle: JobOutcome<SettleResult>;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function log(entry: Record<string, unknown>): void {
  if (process.env.VITEST) return;
  console.info(JSON.stringify({ event: 'prop_jobs', ...entry }));
}

async function runJob<T>(name: string, job: () => Promise<T>): Promise<JobOutcome<T>> {
  try {
    const result = await job();
    log({ job: name, ok: true, result });
    return { ok: true, result };
  } catch (err) {
    const error = errorMessage(err);
    log({ job: name, ok: false, error });
    return { ok: false, error };
  }
}

async function refreshCounts(): Promise<RefreshCounts> {
  const { picks, ...counts } = await refreshPropPicks();
  return { ...counts, picks: picks.length };
}

export async function handler(_event?: unknown): Promise<PropJobsSummary> {
  const refresh = await runJob('refresh', refreshCounts);
  const settle = await runJob('settle', settlePropPicks);
  return { refresh, settle };
}
