import { useState } from 'react';
import { Link } from 'react-router-dom';
import { forgotPassword } from '../api/client';

export const ForgotPasswordPage = () => {
  const [email, setEmail] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await forgotPassword(email);
      setSubmitted(true);
    } catch {
      // the endpoint never leaks, so only unexpected client errors are caught.
      setError('Something went wrong. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-sm mx-auto px-4 py-10">
      {submitted ? (
        <>
          <h1 className="font-display text-3xl font-semibold uppercase tracking-wide">Check your inbox</h1>
          <p className="text-sm text-muted mt-2 mb-6">
            If an account exists with <strong>{email}</strong>, we've sent a link to reset your password. The link expires in 1 hour.
          </p>
          <Link to="/login" className="link link-primary text-sm">
            Back to sign in
          </Link>
        </>
      ) : (
        <>
          <h1 className="font-display text-3xl font-semibold uppercase tracking-wide">Forgot Password</h1>
          <p className="text-sm text-muted mt-1 mb-6">
            Enter your email and we'll send you a reset link.
          </p>

          <form onSubmit={handleSubmit} className="space-y-3">
            <div>
              <label htmlFor="forgot-email" className="text-xs font-medium text-muted mb-1 block">Email</label>
              <input
                id="forgot-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="input input-bordered w-full"
                autoFocus
                autoComplete="email"
                required
              />
            </div>

            {error && <p className="text-error text-sm">{error}</p>}

            <button
              type="submit"
              disabled={loading || !email}
              className="btn btn-primary w-full mt-2"
            >
              {loading ? 'Sending' : 'Send reset link'}
            </button>
          </form>

          <p className="text-sm text-muted mt-6">
            Remember your password?{' '}
            <Link to="/login" className="link link-primary font-medium">
              Sign in
            </Link>
          </p>
        </>
      )}
    </div>
  );
};
