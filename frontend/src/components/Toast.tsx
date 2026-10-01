import { useEffect } from 'react';
import { IconClose } from './icons';

export type ToastVariant = 'success' | 'error';

interface ToastProps {
  message: string;
  variant?: ToastVariant;
  onDismiss: () => void;
  duration?: number;
}

export const Toast = ({ message, variant = 'success', onDismiss, duration = 2500 }: ToastProps): JSX.Element => {
  useEffect(() => {
    const id = setTimeout(onDismiss, duration);
    return () => clearTimeout(id);
  }, [duration, onDismiss]);

  const isError = variant === 'error';

  return (
    <div className="fixed bottom-6 right-6 z-[100]" role={isError ? 'alert' : 'status'}>
      <div className="flex min-w-[260px] max-w-md items-center gap-3 border border-base-300 bg-base-100 px-4 py-3 rounded-box">
        {isError && <span className="text-xs font-semibold uppercase tracking-wide text-error">Error</span>}
        <span className={`flex-1 text-sm ${isError ? '' : 'text-success'}`}>{message}</span>
        <button onClick={onDismiss} className="text-muted hover:text-base-content" aria-label="Dismiss">
          <IconClose size={14} />
        </button>
      </div>
    </div>
  );
};
