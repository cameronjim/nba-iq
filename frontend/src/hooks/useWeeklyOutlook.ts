import { useCallback, useEffect, useRef, useState } from 'react';
import { getWeeklyOutlook } from '../api/client';
import type { WeeklyOutlookResponse } from '../types';

export type WeeklyOutlookState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: WeeklyOutlookResponse };

interface UseWeeklyOutlook {
  state: WeeklyOutlookState;
  reload: () => void;
}

// rosterKey changes whenever the roster does, so an add or drop re-simulates the week.
export function useWeeklyOutlook(enabled: boolean, rosterKey: string): UseWeeklyOutlook {
  const [state, setState] = useState<WeeklyOutlookState>({ status: 'idle' });
  const requestIdRef = useRef(0);

  const load = useCallback(async (): Promise<void> => {
    const requestId = ++requestIdRef.current;
    setState({ status: 'loading' });
    try {
      const data = await getWeeklyOutlook();
      if (requestId === requestIdRef.current) setState({ status: 'ready', data });
    } catch {
      if (requestId === requestIdRef.current) {
        setState({ status: 'error', message: 'Could not load the weekly outlook.' });
      }
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      requestIdRef.current++;
      setState({ status: 'idle' });
      return;
    }
    void load();
  }, [enabled, rosterKey, load]);

  const reload = useCallback((): void => {
    void load();
  }, [load]);

  return { state, reload };
}
