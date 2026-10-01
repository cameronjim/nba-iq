import { useState } from 'react';
import { Link, useSearchParams, useNavigate } from 'react-router-dom';
import { resetPassword } from '../api/client';

function validatePassword(pwd: string): string | null {
  if (pwd.length < 8) return 'At least 8 characters required';
  if (!/[A-Z]/.test(pwd)) return 'Must include at least one uppercase letter';
  if (!/[0-9!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?`~]/.test(pwd)) return 'Must include at least one number or symbol';
  return null;
}

export const ResetPasswordPage = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = searchParams.get('token') ?? '';

  const [newPassword, setNewPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);

  const pwValidationError = newPassword ? validatePassword(newPassword) : null;

  if (!token) {
    return (
      <div className="max-w-sm mx-auto px-4 py-10">
        <h1 className="font-display text-3xl font-semibold uppercase tracking-wide">Invalid reset link</h1>
        <p className="text-sm text-muted mt-2 mb-6">
          This link is missing its token. Request a new reset link to continue.
        </p>
        <Link to="/forgot-password" className="btn btn-primary btn-sm">
          Request new link
        </Link>
      </div>
    );
  }

  const handleSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    setError('');

    const pwErr = validatePassword(newPassword);
    if (pwErr) {
      setError(pwErr);
      return;
    }
    if (newPassword !== confirm) {
      setError('Passwords do not match');
      return;
    }

    setLoading(true);
    try {
      await resetPassword(token, newPassword);
      setSuccess(true);
      setTimeout(() => navigate('/login', { replace: true }), 2500);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error;
      setError(msg ?? 'Failed to reset password');
    } finally {
      setLoading(false);
    }
  };

  if (success) {
    return (
      <div className="max-w-sm mx-auto px-4 py-10">
        <h1 className="font-display text-3xl font-semibold uppercase tracking-wide">Password updated</h1>
        <p className="text-sm text-muted mt-2 mb-6">
          You'll be redirected to sign in.
        </p>
        <Link to="/login" className="btn btn-primary btn-sm">
          Sign in now
        </Link>
      </div>
    );
  }

  return (
    <div className="max-w-sm mx-auto px-4 py-10">
      <h1 className="font-display text-3xl font-semibold uppercase tracking-wide">Reset Password</h1>
      <p className="text-sm text-muted mt-1 mb-6">Choose a new password for your account.</p>

      <form onSubmit={handleSubmit} className="space-y-3">
        <div>
          <label htmlFor="reset-password" className="text-xs font-medium text-muted mb-1 block">New password</label>
          <input
            id="reset-password"
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className="input input-bordered w-full"
            autoFocus
            autoComplete="new-password"
          />
          {newPassword && (
            <p className={`text-xs mt-1 ${pwValidationError ? 'text-warning' : 'text-success'}`}>
              {pwValidationError ?? 'Meets the password rules'}
            </p>
          )}
        </div>

        <div>
          <label htmlFor="reset-confirm" className="text-xs font-medium text-muted mb-1 block">Confirm password</label>
          <input
            id="reset-confirm"
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            className="input input-bordered w-full"
            autoComplete="new-password"
          />
        </div>

        <p className="text-xs text-muted">Min 8 chars · 1 uppercase · 1 number or symbol</p>

        {error && <p className="text-error text-sm">{error}</p>}

        <button
          type="submit"
          disabled={loading || !newPassword || !confirm || !!pwValidationError}
          className="btn btn-primary w-full mt-2"
        >
          {loading ? 'Saving' : 'Update password'}
        </button>
      </form>
    </div>
  );
};
