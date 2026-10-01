import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { IconClose } from '../icons';
import { PlayerCareerSection } from './PlayerCareerSection';

interface PlayerCareerModalProps {
  playerName: string;
  nbaPlayerId: string;
  onClose: () => void;
}

// portalled to document.body for the same reason Rating2kModal is: daisyUI's non-none
// scale on .modal-box would make an enclosing modal the containing block and squeeze it.
export const PlayerCareerModal = ({
  playerName,
  nbaPlayerId,
  onClose,
}: PlayerCareerModalProps): JSX.Element => {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  return createPortal(
    <div className="modal modal-open" role="dialog" aria-label="Season history">
      <div className="modal-box max-w-3xl">
        <div className="flex items-start justify-between gap-3 mb-4">
          <div className="min-w-0">
            <h3 className="font-display font-semibold text-2xl uppercase tracking-wide truncate" title={playerName}>
              {playerName}
            </h3>
            <p className="text-sm text-muted">Every season on record</p>
          </div>
          <button
            className="btn btn-sm btn-circle btn-ghost"
            onClick={onClose}
            aria-label="Close season history"
          >
            <IconClose size={14} />
          </button>
        </div>

        <PlayerCareerSection
          nbaPlayerId={nbaPlayerId}
          emptyMessage="No season history for this player."
        />
      </div>
      <div className="modal-backdrop" onClick={onClose} />
    </div>,
    document.body
  );
};
