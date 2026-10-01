import { IconChevronRight } from '../icons';
import { ReasonBadge } from '../watchlist/ReasonBadge';
import { toStatNumber } from '../../utils/stats';
import { compactVsUsual, showsVsUsual, slateEvidenceLines } from '../../utils/vsUsual';
import type { SlatePlayer } from '../../types';

export const SlateReasons = ({ player }: { player: SlatePlayer }): JSX.Element | null => {
  if (player.reasons.length === 0) return null;
  return (
    <span className="flex flex-wrap gap-1" data-testid={`reasons-${player.nba_player_id}`}>
      {player.reasons.map((reason) => (
        <ReasonBadge key={reason} reason={reason} />
      ))}
    </span>
  );
};

// collapsed it is one line; opened it lists the same evidence the Watchlist shows.
export const SlateVsUsual = ({ player }: { player: SlatePlayer }): JSX.Element | null => {
  if (!showsVsUsual(player)) return null;
  const compact = compactVsUsual(player);
  const lines = slateEvidenceLines(player);
  if (compact === null && lines.length === 0) return null;

  const minutes = toStatNumber(player.vs_usual?.minutes.delta);
  const tone = minutes === null || minutes === 0 ? '' : minutes > 0 ? 'text-success' : 'text-warning';

  return (
    <details className="group text-[11px]" data-testid={`vs-usual-${player.nba_player_id}`}>
      <summary
        className="list-none cursor-pointer inline-flex items-center gap-0.5 tabular-nums"
        title="Show why this differs from his usual"
      >
        <IconChevronRight size={11} className="shrink-0 group-open:rotate-90" />
        <span className="text-muted">vs usual:</span>{' '}
        <span className={tone}>{compact ?? 'see why'}</span>
      </summary>
      {lines.length > 0 && (
        <ul className="flex flex-col gap-0.5 mt-1 pl-4">
          {lines.map((line) => (
            <li key={line} className="text-muted">
              {line}
            </li>
          ))}
        </ul>
      )}
    </details>
  );
};
