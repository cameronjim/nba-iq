import { useCallback, useEffect, useState } from 'react';
import {
  getPropPicksSummary, refreshPropPicks, settlePropPicks,
} from '../api/client';
import type { PropMarketSummary } from '../types';
import { refreshSentence, settleSentence } from '../utils/propJobs';

export type PropJob = 'refresh' | 'settle';

export type JobStatus =
  | { state: 'idle' }
  | { state: 'running'; job: PropJob }
  | { state: 'done'; job: PropJob; message: string }
  | { state: 'failed'; job: PropJob; message: string };

export type SummaryState =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'ready'; markets: PropMarketSummary[] };

interface UsePropJobs {
  job: JobStatus;
  summary: SummaryState;
  run: (job: PropJob) => Promise<void>;
}

export function usePropJobs(): UsePropJobs {
  const [job, setJob] = useState<JobStatus>({ state: 'idle' });
  const [summary, setSummary] = useState<SummaryState>({ status: 'loading' });

  const loadSummary = useCallback(async (): Promise<void> => {
    try {
      const { markets } = await getPropPicksSummary();
      setSummary({ status: 'ready', markets });
    } catch {
      setSummary({ status: 'error' });
    }
  }, []);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  const run = useCallback(async (which: PropJob): Promise<void> => {
    setJob({ state: 'running', job: which });
    try {
      const message = which === 'refresh'
        ? refreshSentence(await refreshPropPicks())
        : settleSentence(await settlePropPicks());
      setJob({ state: 'done', job: which, message });
      await loadSummary();
    } catch {
      const message = which === 'refresh'
        ? 'Could not refresh prop picks. Try again.'
        : 'Could not settle prop picks. Try again.';
      setJob({ state: 'failed', job: which, message });
    }
  }, [loadSummary]);

  return { job, summary, run };
}
