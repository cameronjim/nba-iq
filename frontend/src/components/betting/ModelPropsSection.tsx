import { SkeletonLines } from '../Skeleton';
import { usePropPicks } from '../../hooks/usePropPicks';
import { propSentence } from '../../utils/bettingCopy';

export const ModelPropsSection = (): JSX.Element => {
  const { picks, loading } = usePropPicks();

  if (loading) return <SkeletonLines lines={4} label="Loading prop picks" />;

  if (picks.length === 0) {
    return <p className="text-sm text-muted">Prop picks appear here once prop odds are connected.</p>;
  }

  return (
    <ul>
      {picks.map((pick) => (
        <li
          key={`${pick.game_date}-${pick.player_name}-${pick.market}-${pick.side}-${pick.line}-${pick.bookmaker}`}
          className="text-sm py-2 border-t border-base-300 first:border-t-0"
        >
          {propSentence(pick)}
        </li>
      ))}
    </ul>
  );
};
