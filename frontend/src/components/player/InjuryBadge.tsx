import { injuryLabel, type InjuryTone } from '../../utils/injuryLabel';

const TONE_CLASS: Record<InjuryTone, string> = {
  error: 'text-error',
  warning: 'text-warning',
  success: 'text-success',
  muted: 'text-muted',
};

interface InjuryBadgeProps {
  status: string | null;
  detail?: string | null;
  showDetail?: boolean;
}

export const InjuryBadge = ({ status, detail, showDetail = false }: InjuryBadgeProps): JSX.Element | null => {
  const label = injuryLabel(status, detail);
  if (!label) return null;
  return (
    <>
      <span
        className={`inline-block flex-shrink-0 border border-base-300 rounded-sm px-1 text-[11px] leading-4 font-semibold uppercase whitespace-nowrap ${TONE_CLASS[label.tone]}`}
        title={label.detail ?? undefined}
        data-testid="injury-badge"
      >
        {label.short}
      </span>
      {showDetail && label.detail && (
        // basis-full forces its own line inside a flex-wrap parent; block does it elsewhere.
        <span
          className="block basis-full text-faint text-xs font-normal truncate max-w-[260px]"
          data-testid="injury-detail"
        >
          {label.detail}
        </span>
      )}
    </>
  );
};
