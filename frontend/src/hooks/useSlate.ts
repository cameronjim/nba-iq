import { useState } from 'react';
import { getSlate } from '../api/client';
import { todayInEastern } from '../utils/dates';
import { useCachedResource } from './useCachedResource';
import type { SlateResponse, SlateSort } from '../types';

export interface UseSlate {
  date: string;
  setDate: (date: string) => void;
  sort: SlateSort;
  setSort: (sort: SlateSort) => void;
  data: SlateResponse | null;
  loading: boolean;
  error: string;
  reload: () => Promise<void>;
}

export function useSlate(): UseSlate {
  const [date, setDateState] = useState(todayInEastern);
  const [sort, setSort] = useState<SlateSort>('impact');

  const { data, loading, error, reload } = useCachedResource<SlateResponse>(
    `slate:${date}:${sort}`,
    () => getSlate(date, sort),
    { errorMessage: 'Failed to load the slate' }
  );

  // a cleared date input falls back to today rather than requesting an empty date.
  const setDate = (next: string): void => setDateState(next || todayInEastern());

  return { date, setDate, sort, setSort, data, loading, error, reload };
}
