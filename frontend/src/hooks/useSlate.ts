import { useState } from 'react';
import { getSlate } from '../api/client';
import { todayInEastern } from '../utils/dates';
import { useCachedResource } from './useCachedResource';
import type { SlateResponse } from '../types';

export interface UseSlate {
  date: string;
  setDate: (date: string) => void;
  data: SlateResponse | null;
  loading: boolean;
  error: string;
  reload: () => Promise<void>;
}

// always the server's default order (impact), so every caller shares one cached request.
export function useSlate(enabled = true): UseSlate {
  const [date, setDateState] = useState(todayInEastern);

  const { data, loading, error, reload } = useCachedResource<SlateResponse>(
    `slate:${date}:impact`,
    () => getSlate(date),
    { enabled, errorMessage: 'Failed to load the slate' }
  );

  // a cleared date input falls back to today rather than requesting an empty date.
  const setDate = (next: string): void => setDateState(next || todayInEastern());

  return { date, setDate, data, loading, error, reload };
}
