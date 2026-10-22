import { useMemo, useState } from 'react';
import { SkeletonLines } from '../Skeleton';
import { usePropPicks } from '../../hooks/usePropPicks';
import { propSentence } from '../../utils/bettingCopy';
import { bestPickPerMarket, rankPicks } from '../../utils/propPickRanking';

export const PROP_PICKS_INITIAL = 6;
export const PROP_PICKS_STEP = 12;

export const ModelPropsSection = (): JSX.Element => {
  const { picks, loading } = usePropPicks();
  const [shown, setShown] = useState(PROP_PICKS_INITIAL);
  const ranked = useMemo(() => rankPicks(bestPickPerMarket(picks)), [picks]);

  if (loading) return <SkeletonLines lines={4} label="Loading prop picks" />;

  if (picks.length === 0) {
    return <p className="text-sm text-muted">Prop picks appear here once prop odds are connected.</p>;
  }

  const hidden = Math.max(ranked.length - shown, 0);
  const canCollapse = hidden === 0 && ranked.length > PROP_PICKS_INITIAL;

  return (
    <div>
      <ul>
        {ranked.slice(0, shown).map((pick) => (
          <li
            key={`${pick.game_date}-${pick.player_name}-${pick.market}-${pick.side}-${pick.line}-${pick.bookmaker}`}
            className="text-sm py-2 border-t border-base-300 first:border-t-0"
          >
            {propSentence(pick)}
          </li>
        ))}
      </ul>
      {hidden > 0 && (
        <button type="button" className="btn btn-ghost btn-sm mt-2" onClick={() => setShown(shown + PROP_PICKS_STEP)}>
          {`See more (${hidden})`}
        </button>
      )}
      {canCollapse && (
        <button type="button" className="btn btn-ghost btn-sm mt-2" onClick={() => setShown(PROP_PICKS_INITIAL)}>
          Show less
        </button>
      )}
    </div>
  );
};
