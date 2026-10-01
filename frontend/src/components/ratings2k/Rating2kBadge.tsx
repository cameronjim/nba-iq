import { useState } from 'react';
import { useRating2kByName } from '../../hooks/useRating2kByName';
import { formatStat } from '../../utils/stats';
import { tierBadgeClass } from '../../utils/ratings2k';
import { Rating2kModal } from './Rating2kModal';

interface Rating2kBadgeProps {
  playerName: string;
}

// coverage is name-based and incomplete, so a miss must render nothing at all.
export const Rating2kBadge = ({ playerName }: Rating2kBadgeProps): JSX.Element | null => {
  const { rating, unavailable } = useRating2kByName(playerName);
  const [open, setOpen] = useState(false);

  if (unavailable || !rating) return null;

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className={`badge badge-sm font-semibold ${tierBadgeClass(rating.overall)}`}
        title="View the full 2K attribute breakdown"
      >
        2K {formatStat(rating.overall, 0)}
      </button>

      {open && (
        <Rating2kModal slug={rating.slug} summary={rating} onClose={() => setOpen(false)} />
      )}
    </>
  );
};
