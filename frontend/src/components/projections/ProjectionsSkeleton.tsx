import { SkeletonBlock } from '../Skeleton';

const ROWS = 6;

export const ProjectionsSkeleton = (): JSX.Element => (
  <div role="status" aria-label="Loading projections" className="border border-base-300">
    {Array.from({ length: ROWS }, (_, row) => (
      <div key={row} className="px-3 py-2 border-b border-base-300 last:border-b-0 space-y-1.5">
        <SkeletonBlock className="h-4 w-4/5" />
        <SkeletonBlock className="h-3 w-1/2" />
        <SkeletonBlock className="h-3 w-12" />
      </div>
    ))}
  </div>
);
