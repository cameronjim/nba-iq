import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useGoogleLogin } from '@react-oauth/google';
import { register, googleSignInWithToken } from '../api/client';
import { GoogleSignInButton } from '../components/GoogleSignInButton';

interface RegisterPageProps {
  onRegister: () => void;
}

function validatePassword(pwd: string): string | null {
  if (pwd.length < 8) return 'At least 8 characters required';
  if (!/[A-Z]/.test(pwd)) return 'Must include at least one uppercase letter';
  if (!/[0-9!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?`~]/.test(pwd)) return 'Must include at least one number or symbol';
  return null;
}

function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

export const RegisterPage = ({ onRegister }: RegisterPageProps) => {
  const navigate = useNavigate();

  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const pwValidationError = password ? validatePassword(password) : null;
  const emailLooksValid = email && isValidEmail(email);

  // select_account forces the picker rather than silently using the cached account.
  const signUpWithGoogle = useGoogleLogin({
    flow: 'implicit',
    prompt: 'select_account',
    scope: 'openid email profile',
    onSuccess: async (tokenResponse) => {
      setError('');
      setLoading(true);
      try {
        await googleSignInWithToken(tokenResponse.access_token);
        onRegister();
        navigate('/', { replace: true });
      } catch (err: unknown) {
        const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error;
        setError(msg ?? 'Google sign-in failed');
      } finally {
        setLoading(false);
      }
    },
    onError: () => setError('Google sign-in failed'),
  });

  const handleSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    setError('');

    if (!isValidEmail(email)) {
      setError('Please enter a valid email address');
      return;
    }
    const pwErr = validatePassword(password);
    if (pwErr) {
      setError(pwErr);
      return;
    }
    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    setLoading(true);
    try {
      await register(username, email, password);
      onRegister();
      navigate('/', { replace: true });
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error;
      setError(msg ?? 'Failed to create account');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-sm mx-auto px-4 py-10">
      <h1 className="font-display text-3xl font-semibold uppercase tracking-wide">Create Account</h1>
      <p className="text-sm text-muted mt-1 mb-6">Sign up to save a roster, track bets, and set preferences.</p>

      <GoogleSignInButton label="Sign up with Google" disabled={loading} onClick={() => signUpWithGoogle()} />

      <p className="text-xs text-muted my-4">Or create an account with an email address.</p>

      <form onSubmit={handleSubmit} className="space-y-3">
        <div>
          <label htmlFor="register-username" className="text-xs font-medium text-muted mb-1 block">Username</label>
          <input
            id="register-username"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="input input-bordered w-full"
            autoFocus
            autoComplete="username"
            minLength={3}
          />
        </div>

        <div>
          <label htmlFor="register-email" className="text-xs font-medium text-muted mb-1 block">Email</label>
          <input
            id="register-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="input input-bordered w-full"
            autoComplete="email"
          />
          {email && !emailLooksValid && (
            <p className="text-xs mt-1 text-warning">Please enter a valid email address</p>
          )}
        </div>

        <div>
          <label htmlFor="register-password" className="text-xs font-medium text-muted mb-1 block">Password</label>
          <input
            id="register-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="input input-bordered w-full"
            autoComplete="new-password"
          />
          {password && (
            <p className={`text-xs mt-1 ${pwValidationError ? 'text-warning' : 'text-success'}`}>
              {pwValidationError ?? 'Meets the password rules'}
            </p>
          )}
        </div>

        <div>
          <label htmlFor="register-confirm" className="text-xs font-medium text-muted mb-1 block">Confirm password</label>
          <input
            id="register-confirm"
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className="input input-bordered w-full"
            autoComplete="new-password"
          />
        </div>

        <p className="text-xs text-muted">Min 8 chars · 1 uppercase · 1 number or symbol</p>

        {error && <p className="text-error text-sm">{error}</p>}

        <button
          type="submit"
          disabled={
            loading ||
            !username ||
            !email ||
            !password ||
            !confirmPassword ||
            !emailLooksValid ||
            !!pwValidationError
          }
          className="btn btn-primary w-full mt-2"
        >
          {loading ? 'Creating account' : 'Create Account'}
        </button>

        <p className="text-xs text-muted">
          By creating an account you agree to the{' '}
          <Link to="/terms" className="link">Terms</Link> and{' '}
          <Link to="/privacy" className="link">Privacy Policy</Link>.
        </p>
      </form>

      <p className="text-sm text-muted mt-6">
        Already have an account?{' '}
        <Link to="/login" className="link link-primary font-medium">
          Sign in
        </Link>
      </p>
    </div>
  );
};
