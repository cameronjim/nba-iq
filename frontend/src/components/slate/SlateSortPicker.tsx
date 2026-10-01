import { SegmentedFilter } from '../SegmentedFilter';
import type { SlateSort } from '../../types';

const SORT_OPTIONS: Array<{ value: SlateSort; label: string }> = [
  { value: 'impact', label: 'Impact' },
  { value: 'edge', label: 'Edge vs usual' },
];

export const SlateSortPicker = ({
  sort,
  onChange,
}: {
  sort: SlateSort;
  onChange: (sort: SlateSort) => void;
}): JSX.Element => (
  <SegmentedFilter options={SORT_OPTIONS} value={sort} onChange={onChange} ariaLabel="Sort players by" />
);
