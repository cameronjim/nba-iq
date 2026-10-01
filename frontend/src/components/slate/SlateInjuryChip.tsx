import { formatTimestamp } from '../../utils/analytics';
import type { SlatePlayer } from '../../types';

function injuryTone(bucket: string | null): string {
  switch (bucket) {
    case 'out':
    case 'doubtful':
      return 'text-error';
    // null is the cleared case: a designation the run priced in that has since
    // come off the report, which is good news and reads as such.
    case 'probable':
    case 'available':
    case null:
      return 'text-success';
    default:
      return 'text-warning';
  }
}

// the CURRENT report, which can be newer than the projection. "new" means the
// designation moved after publication, so the projected numbers do not reflect it.
export const InjuryChip = ({ player }: { player: SlatePlayer }): JSX.Element | null => {
  const status = player.injury_status ?? null;
  const changed = player.injury_changed_after_run === true;
  if (status === null && !changed) return null;

  const label = status === null ? 'Cleared' : (player.injury_status_raw ?? status);
  const asOf = formatTimestamp(player.injury_as_of ?? null);
  const detailPart = player.injury_detail ? ` (${player.injury_detail})` : '';
  const title =
    status === null
      ? 'Was on the injury report when this projection was published and has since cleared. The projection does not reflect it.'
      : `Current injury report: ${label}${detailPart}${asOf ? `, as of ${asOf}` : ''}.` +
        (changed
          ? ' Reported after this projection was published, so the numbers do not reflect it.'
          : '');

  return (
    <span
      className={`text-[11px] font-semibold uppercase tracking-wide ${injuryTone(status)}`}
      title={title}
      data-testid="injury-chip"
    >
      {label}
      {changed && <span className="normal-case">&nbsp;· new</span>}
    </span>
  );
};
