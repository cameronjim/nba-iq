import { GoogleMark } from './icons';

interface GoogleSignInButtonProps {
  label: string;
  disabled: boolean;
  onClick: () => void;
}

export const GoogleSignInButton = ({ label, disabled, onClick }: GoogleSignInButtonProps): JSX.Element => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    className="btn btn-outline w-full justify-center gap-3 border-base-300 bg-base-100 font-medium hover:bg-base-200 hover:border-base-content"
  >
    <GoogleMark />
    {label}
  </button>
);
