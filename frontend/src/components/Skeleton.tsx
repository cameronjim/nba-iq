interface SkeletonBlockProps {
  className?: string;
}

export const SkeletonBlock = ({ className = 'h-4 w-full' }: SkeletonBlockProps): JSX.Element => (
  <div className={`skeleton ${className}`} aria-hidden="true" />
);

interface SkeletonLinesProps {
  lines?: number;
  label?: string;
}

export const SkeletonLines = ({ lines = 3, label = 'Loading' }: SkeletonLinesProps): JSX.Element => (
  <div role="status" aria-label={label} className="space-y-2">
    {Array.from({ length: lines }, (_, i) => (
      <SkeletonBlock key={i} className={`h-3.5 ${i === lines - 1 ? 'w-2/3' : 'w-full'}`} />
    ))}
  </div>
);

interface SkeletonTableProps {
  rows?: number;
  cols?: number;
  label?: string;
}

export const SkeletonTable = ({ rows = 8, cols = 6, label = 'Loading' }: SkeletonTableProps): JSX.Element => (
  <div role="status" aria-label={label} className="border border-base-300">
    <div className="flex gap-4 px-3 py-2.5 border-b border-base-300 bg-base-200">
      {Array.from({ length: cols }, (_, c) => (
        <SkeletonBlock key={c} className={`h-3 ${c === 0 ? 'w-32' : 'w-10'}`} />
      ))}
    </div>
    {Array.from({ length: rows }, (_, r) => (
      <div key={r} className="flex gap-4 px-3 py-3 border-b border-base-300 last:border-b-0">
        {Array.from({ length: cols }, (_, c) => (
          <SkeletonBlock key={c} className={`h-3 ${c === 0 ? 'w-32' : 'w-10'}`} />
        ))}
      </div>
    ))}
  </div>
);
