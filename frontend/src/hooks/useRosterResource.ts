import { useCallback, useEffect, useRef, useState } from 'react';

export type RosterResourceState<T> =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: T };

interface UseRosterResource<T> {
  state: RosterResourceState<T>;
  reload: () => void;
}

// rosterKey changes whenever the roster does, so an add or drop refetches.
export function useRosterResource<T>(
  fetcher: () => Promise<T>,
  enabled: boolean,
  rosterKey: string,
  errorMessage: string
): UseRosterResource<T> {
  const [state, setState] = useState<RosterResourceState<T>>({ status: 'idle' });
  const requestIdRef = useRef(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(async (): Promise<void> => {
    const requestId = ++requestIdRef.current;
    setState({ status: 'loading' });
    try {
      const data = await fetcherRef.current();
      if (requestId === requestIdRef.current) setState({ status: 'ready', data });
    } catch {
      if (requestId === requestIdRef.current) setState({ status: 'error', message: errorMessage });
    }
  }, [errorMessage]);

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
