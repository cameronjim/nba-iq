import { useState } from 'react';
import { SkeletonLines } from '../Skeleton';
import { GameRow } from './GameRow';
import type { BettingGame } from '../../types';

interface GamesListProps {
  games: BettingGame[];
  loading: boolean;
  error: string;
  onRetry: () => void;
}

const VISIBLE_GAMES = 6;

export const GamesList = ({ games, loading, error, onRetry }: GamesListProps): JSX.Element => {
  const [expanded, setExpanded] = useState(false);

  if (loading) {
    return <SkeletonLines lines={4} label="Loading tonight's games" />;
  }

  if (error) {
    return (
      <p className="text-sm">
        <span className="text-error">Couldn't load the odds right now.</span>{' '}
        <button onClick={onRetry} className="btn btn-ghost btn-xs">Try again</button>
      </p>
    );
  }

  if (games.length === 0) {
    return <p className="text-sm text-muted">No games with posted odds in the next two days.</p>;
  }

  const visible = expanded ? games : games.slice(0, VISIBLE_GAMES);
  const hiddenCount = games.length - VISIBLE_GAMES;

  return (
    <div className="space-y-2">
      <ul>
        {visible.map((game) => (
          <GameRow key={game.espn_event_id} game={game} />
        ))}
      </ul>
      {hiddenCount > 0 && (
        <button onClick={() => setExpanded(!expanded)} className="btn btn-ghost btn-xs">
          {expanded ? 'See less' : `See ${hiddenCount} more`}
        </button>
      )}
    </div>
  );
};
