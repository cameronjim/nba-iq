import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import type { Rating2kSummary } from '../../types';
import { useRating2kDetail } from '../../hooks/useRating2kDetail';
import { IconClose } from '../icons';
import { SkeletonBlock, SkeletonLines } from '../Skeleton';
import { PLAYER_IMAGE_FALLBACK } from '../../utils/playerImage';
import { formatStat, toStatNumber } from '../../utils/stats';
import {
  formatAttributeLabel, formatPositions, formatRatingDelta, groupRating2kAttributes,
  ratingBarPercent, tierBadgeClass, tierBarClass,
} from '../../utils/ratings2k';
import { Ratings2kAttribution } from './Ratings2kAttribution';

interface Rating2kModalProps {
  slug: string;
  summary?: Rating2kSummary | null;
  onClose: () => void;
}

// portalled to document.body: daisyUI gives .modal-box a non-none scale, which makes
// any enclosing modal box the containing block for this one's position: fixed, so
// inset: 0 and max-w-3xl would resolve against that box and clip the columns.
export const Rating2kModal = ({ slug, summary, onClose }: Rating2kModalProps): JSX.Element => {
  const { detail, loading, error, notFound, reload } = useRating2kDetail(slug);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const player = detail?.player ?? summary ?? null;
  const groups = groupRating2kAttributes(detail?.attributes ?? []);
  const bio = detail
    ? [detail.player.height, detail.player.weight, detail.player.wingspan].filter(Boolean)
    : [];
  const positions = formatPositions(player?.positions);
  const meta = [player?.team, positions].filter((part) => !!part).join(' · ');
  const archetype = detail
    ? [detail.player.archetype, detail.player.build].filter(Boolean).join(' · ')
    : '';

  const deltaToneClass = (delta: string): string => {
    if (delta.startsWith('+')) return 'text-success';
    if (delta.startsWith('-')) return 'text-error';
    return 'text-faint';
  };

  const renderBody = (): JSX.Element => {
    if (loading) {
      return (
        <div className="flex flex-col gap-5">
          <SkeletonBlock className="h-3 w-40" />
          <div className="grid gap-x-8 gap-y-5 md:grid-cols-2">
            <SkeletonLines lines={8} label="Loading 2K ratings" />
            <SkeletonLines lines={8} label="Loading 2K ratings" />
          </div>
        </div>
      );
    }

    if (error) {
      return (
        <div className="flex flex-col items-center py-10 gap-3 border border-base-300">
          <p className="text-error text-sm">{error}</p>
          <button onClick={reload} className="btn btn-primary btn-sm">Try Again</button>
        </div>
      );
    }

    if (notFound || !detail) {
      return (
        <div className="flex flex-col items-center text-center py-10 gap-1 border border-base-300">
          <p className="font-semibold">No 2K ratings for this player</p>
          <p className="text-sm text-muted">
            This player isn't in the imported 2K roster.
          </p>
        </div>
      );
    }

    return (
      <div className="flex flex-col gap-5">
        {bio.length > 0 && (
          <p className="text-xs text-muted">{bio.join(' · ')}</p>
        )}

        <div className="grid gap-x-8 gap-y-5 md:grid-cols-2">
          {groups.map((group) => (
            <div key={group.label}>
              <h4 className="text-sm font-semibold mb-2">{group.label}</h4>
              <div className="flex flex-col gap-1.5">
                {group.attributes.map((attribute) => {
                  const label = formatAttributeLabel(attribute.attribute_name);
                  return (
                    <div key={attribute.attribute_name} className="flex items-center gap-2">
                      <span className="w-[124px] shrink-0 text-xs text-muted truncate" title={label}>
                        {label}
                      </span>
                      <span className="flex-1 h-2 bg-base-300 overflow-hidden">
                        <span
                          className={`block h-full ${tierBarClass(attribute.value)}`}
                          style={{ width: `${ratingBarPercent(attribute.value)}%` }}
                        />
                      </span>
                      <span className="w-7 shrink-0 text-right text-xs font-semibold tabular-nums">
                        {formatStat(attribute.value, 0)}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        {detail.rating_history.length > 0 && (
          <div>
            <h4 className="text-sm font-semibold mb-2">Overall by Game</h4>
            <div className="flex gap-2 overflow-x-auto pb-1">
              {detail.rating_history.map((entry) => {
                const delta = formatRatingDelta(entry.delta);
                return (
                  <div
                    key={entry.game_version}
                    className="rounded-box border border-base-300 px-3 py-2 text-center min-w-[78px]"
                  >
                    <p className="text-[10px] uppercase tracking-wider text-muted">
                      {entry.game_version}
                    </p>
                    <p className="text-lg font-bold tabular-nums leading-tight">
                      {formatStat(entry.overall, 0)}
                    </p>
                    {delta && (
                      <p className={`text-[10px] tabular-nums ${deltaToneClass(delta)}`}>{delta}</p>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {detail.badges.length > 0 && (
          <div>
            <h4 className="text-sm font-semibold mb-2">Badges</h4>
            <div className="flex flex-wrap gap-1.5">
              {detail.badges.map((badge) => (
                <span key={badge.badge_name} className="badge badge-outline badge-sm">
                  {badge.tier ? `${badge.badge_name} · ${badge.tier}` : badge.badge_name}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  };

  return createPortal(
    <div className="modal modal-open" role="dialog" aria-label="2K ratings">
      {/* scrolls inside the box: 35 attributes are taller than a phone screen */}
      <div className="modal-box max-w-3xl max-h-[85vh] overflow-y-auto">
        <div className="flex items-start justify-between gap-3 mb-4">
          <div className="flex items-center gap-3 min-w-0">
            <div className="avatar flex-shrink-0">
              <div className="w-14 rounded-box bg-base-200">
                <img
                  src={player?.player_image || PLAYER_IMAGE_FALLBACK}
                  alt=""
                  onError={(e) => { (e.target as HTMLImageElement).src = PLAYER_IMAGE_FALLBACK; }}
                />
              </div>
            </div>
            <div className="min-w-0">
              <h3 className="font-display font-semibold text-2xl uppercase tracking-wide truncate" title={player?.name}>
                {player?.name ?? '2K Ratings'}
              </h3>
              {meta && <p className="text-sm text-muted truncate">{meta}</p>}
              {archetype && <p className="text-xs text-muted truncate">{archetype}</p>}
            </div>
          </div>

          <div className="flex items-start gap-2 flex-shrink-0">
            {toStatNumber(player?.overall) !== null && (
              <div className="text-center">
                <span className={`badge badge-lg font-bold text-lg h-9 px-3 ${tierBadgeClass(player?.overall)}`}>
                  {formatStat(player?.overall, 0)}
                </span>
                <p className="text-[10px] uppercase tracking-wider text-muted mt-1">Overall</p>
              </div>
            )}
            <button
              className="btn btn-sm btn-circle btn-ghost"
              onClick={onClose}
              aria-label="Close 2K ratings"
            >
              <IconClose size={14} />
            </button>
          </div>
        </div>

        {renderBody()}

        <div className="mt-5 pt-3 border-t border-base-300">
          <Ratings2kAttribution />
        </div>
      </div>
      <div className="modal-backdrop" onClick={onClose} />
    </div>,
    document.body
  );
};
