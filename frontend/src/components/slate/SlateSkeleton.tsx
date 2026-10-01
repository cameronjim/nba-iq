import { SkeletonBlock } from '../Skeleton';

const CARDS = 4;
const ROWS = 4;

export const SlateSkeleton = (): JSX.Element => (
  <div
    role="status"
    aria-label="Loading projections"
    className="grid grid-cols-1 md:grid-cols-2 gap-4"
  >
    {Array.from({ length: CARDS }, (_, card) => (
      <div key={card} className="border border-base-300">
        <div className="px-3 py-2 border-b border-base-300 bg-base-200">
          <SkeletonBlock className="h-5 w-24" />
        </div>
        {Array.from({ length: ROWS }, (_, row) => (
          <div key={row} className="px-3 py-2 border-b border-base-300 last:border-b-0 space-y-1.5">
            <div className="flex justify-between gap-4">
              <SkeletonBlock className="h-4 w-36" />
              <SkeletonBlock className="h-4 w-14" />
            </div>
            <SkeletonBlock className="h-3 w-3/4" />
          </div>
        ))}
      </div>
    ))}
  </div>
);
