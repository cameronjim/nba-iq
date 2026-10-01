import { SkeletonBlock } from '../Skeleton';

const ROWS = 8;

export const WatchlistSkeleton = (): JSX.Element => (
  <div role="status" aria-label="Loading watchlist" className="border-t border-base-300">
    {Array.from({ length: ROWS }, (_, row) => (
      <div key={row} className="flex items-center gap-3 px-3 py-3 border-b border-base-300">
        <SkeletonBlock className="h-3 w-6" />
        <div className="grow space-y-1.5">
          <SkeletonBlock className="h-4 w-44" />
          <SkeletonBlock className="h-3 w-32" />
        </div>
        <SkeletonBlock className="h-4 w-10" />
      </div>
    ))}
  </div>
);
