import { ProjectionDetails } from './ProjectionDetails';
import { ROW_SEPARATOR, type ProjectionRowModel, type Tone } from '../../utils/projectionRow';

// tailwind only emits classes it can see as literals.
const TONE_CLASS: Record<Tone, string> = {
  error: 'text-error',
  warning: 'text-warning',
  success: 'text-success',
};

interface LinePart {
  key: string;
  text: string;
  className: string;
}

export const ProjectionRow = ({ row }: { row: ProjectionRowModel }): JSX.Element => {
  const parts: LinePart[] = [];
  if (row.matchup) parts.push({ key: 'matchup', text: row.matchup, className: 'text-muted' });
  if (row.line) parts.push({ key: 'line', text: row.line, className: '' });
  if (row.chance) {
    parts.push({
      key: 'chance',
      text: row.chance,
      className: row.chanceTone ? TONE_CLASS[row.chanceTone] : '',
    });
  }

  return (
    <li
      className="flex flex-col gap-1 py-2 px-3 border-b border-base-300 last:border-b-0"
      data-testid={`row-${row.id}`}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <p className="text-sm tabular-nums min-w-0" data-testid={`line-${row.id}`}>
          <span
            className={row.nameIsPlaceholder ? 'font-mono text-xs italic text-muted' : 'font-semibold'}
            title={row.nameIsPlaceholder ? 'Not on a roster yet, so this is his NBA id' : undefined}
          >
            {row.name}
          </span>
          {parts.map((part) => (
            <span key={part.key}>
              <span className="text-faint">{ROW_SEPARATOR}</span>
              <span className={part.className}>{part.text}</span>
            </span>
          ))}
        </p>
        {row.preseason && (
          <span
            className="text-[11px] text-muted"
            title="The model learned from regular-season games, so preseason minutes run high."
          >
            Preseason
          </span>
        )}
        {row.injury && (
          <span
            className={`text-xs font-semibold ${TONE_CLASS[row.injury.tone]}`}
            title={row.injury.description}
            data-testid="injury-chip"
          >
            {row.injury.label}
          </span>
        )}
      </div>
      {row.note && (
        <p className="text-xs text-muted" data-testid={`note-${row.id}`}>
          {row.note}
        </p>
      )}
      <ProjectionDetails row={row} />
    </li>
  );
};
