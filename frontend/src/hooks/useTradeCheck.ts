import { useCallback, useRef, useState } from 'react';
import { isAxiosError } from 'axios';
import { checkTrade } from '../api/client';
import type { TradeCheckResponse } from '../types';

export type TradeCheckState =
  | { status: 'idle' }
  | { status: 'checking' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: TradeCheckResponse };

interface UseTradeCheck {
  state: TradeCheckState;
  check: (give: number[], get: number[]) => void;
  reset: () => void;
}

const GENERIC_ERROR = 'Could not check this trade.';

function messageOf(error: unknown): string {
  // a 400 carries a plain validation sentence from the server, which is safe to show.
  if (isAxiosError(error) && error.response?.status === 400) {
    const body: unknown = error.response.data;
    if (typeof body === 'object' && body !== null && typeof (body as { error?: unknown }).error === 'string') {
      const text = (body as { error: string }).error;
      return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
    }
  }
  return GENERIC_ERROR;
}

export function useTradeCheck(): UseTradeCheck {
  const [state, setState] = useState<TradeCheckState>({ status: 'idle' });
  const requestIdRef = useRef(0);

  const check = useCallback((give: number[], get: number[]): void => {
    const requestId = ++requestIdRef.current;
    setState({ status: 'checking' });
    checkTrade(give, get)
      .then((data) => {
        if (requestId === requestIdRef.current) setState({ status: 'ready', data });
      })
      .catch((error: unknown) => {
        if (requestId === requestIdRef.current) setState({ status: 'error', message: messageOf(error) });
      });
  }, []);

  const reset = useCallback((): void => {
    requestIdRef.current++;
    setState({ status: 'idle' });
  }, []);

  return { state, check, reset };
}
