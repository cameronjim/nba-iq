import type { WatchlistReason } from '../../types';

// tailwind only emits classes it can see as literals, so each badge class is spelled out.
export const REASON_META: Record<
  WatchlistReason,
  { label: string; badgeClass: string; description: string }
> = {
  ROLE_INCREASE: {
    label: 'Role increase',
    badgeClass: 'badge-success',
    description: 'Projected for at least 4 more minutes than usual.',
  },
  SHOT_VOLUME_SURGE: {
    label: 'Shot volume',
    badgeClass: 'badge-primary',
    description: 'Projected to take more shots than usual.',
  },
  RETURNING_FROM_ABSENCE: {
    label: 'Just back',
    badgeClass: 'badge-info',
    description: 'Expected back after a week or more out.',
  },
  HOT_STREAK: {
    label: 'Hot streak',
    badgeClass: 'badge-warning',
    description: 'Scoring well above his usual over his last 5 games.',
  },
  TEAMMATE_ABSENCE: {
    label: 'Teammate out',
    badgeClass: 'badge-accent',
    description: 'A teammate who usually plays 28+ minutes is unlikely to play.',
  },
};

export const REASON_ORDER: WatchlistReason[] = [
  'ROLE_INCREASE',
  'SHOT_VOLUME_SURGE',
  'RETURNING_FROM_ABSENCE',
  'HOT_STREAK',
  'TEAMMATE_ABSENCE',
];

export const ReasonBadge = ({ reason }: { reason: WatchlistReason }): JSX.Element => {
  const meta = REASON_META[reason];
  // an unknown code from a newer server still renders, just without styling.
  if (!meta) return <span className="badge badge-ghost badge-sm">{reason}</span>;
  return (
    <span className={`badge badge-sm ${meta.badgeClass}`} title={meta.description}>
      {meta.label}
    </span>
  );
};
