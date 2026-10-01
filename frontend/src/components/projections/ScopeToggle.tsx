import { SegmentedFilter } from '../SegmentedFilter';
import type { ProjectionScope } from '../../hooks/useProjections';

const SCOPE_OPTIONS: Array<{ value: ProjectionScope; label: string }> = [
  { value: 'tonight', label: 'Tonight' },
  { value: 'week', label: 'Next 7 days' },
];

export const ScopeToggle = ({
  scope,
  onChange,
}: {
  scope: ProjectionScope;
  onChange: (scope: ProjectionScope) => void;
}): JSX.Element => (
  <SegmentedFilter
    options={SCOPE_OPTIONS}
    value={scope}
    onChange={onChange}
    ariaLabel="Show projections for"
  />
);
