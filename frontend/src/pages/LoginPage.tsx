import { useState } from 'react';
import { Link, useNavigate, useLocation } from 'react-router-dom';
import { useGoogleLogin } from '@react-oauth/google';
import { login, googleSignInWithToken } from '../api/client';
import { GoogleSignInButton } from '../components/GoogleSignInButton';

interface LoginPageProps {
  onLogin: () => void;
}

interface LocationState {
  from?: string;
}

export const LoginPage = ({ onLogin }: LoginPageProps) => {
  const navigate = useNavigate();
  const location = useLocation();
  const state = location.state as LocationState | null;
  const redirectTo = state?.from ?? '/';

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  // a custom button on the popup flow, because google's iframe button can't follow the theme.
  // select_account lets users switch google accounts on a shared device.
  const signInWithGoogle = useGoogleLogin({
    flow: 'implicit',
    prompt: 'select_account',
    scope: 'openid email profile',
    onSuccess: async (tokenResponse) => {
      setError('');
      setLoading(true);
      try {
        await googleSignInWithToken(tokenResponse.access_token);
        onLogin();
        navigate(redirectTo, { replace: true });
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
    setLoading(true);
    try {
      await login(username, password);
      onLogin();
      navigate(redirectTo, { replace: true });
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error;
      setError(msg ?? 'Failed to sign in');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-sm mx-auto px-4 py-10">
      <h1 className="font-display text-3xl font-semibold uppercase tracking-wide">Sign In</h1>
      <p className="text-sm text-muted mt-1 mb-6">Sign in to NBA IQ.</p>

      <GoogleSignInButton label="Continue with Google" disabled={loading} onClick={() => signInWithGoogle()} />

      <p className="text-xs text-muted my-4">Or use your username and password.</p>

      <form onSubmit={handleSubmit} className="space-y-3">
        <div>
          <label htmlFor="login-username" className="text-xs font-medium text-muted mb-1 block">Username or email</label>
          <input
            id="login-username"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="input input-bordered w-full"
            autoFocus
            autoComplete="username"
          />
        </div>

        <div>
          <div className="flex items-baseline justify-between mb-1">
            <label htmlFor="login-password" className="text-xs font-medium text-muted">Password</label>
            <Link to="/forgot-password" className="link link-primary text-xs">
              Forgot?
            </Link>
          </div>
          <input
            id="login-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="input input-bordered w-full"
            autoComplete="current-password"
          />
        </div>

        {error && <p className="text-error text-sm">{error}</p>}

        <button
          type="submit"
          disabled={loading || !username || !password}
          className="btn btn-primary w-full mt-2"
        >
          {loading ? 'Signing in' : 'Sign In'}
        </button>
      </form>

      <p className="text-sm text-muted mt-6">
        New here?{' '}
        <Link to="/register" className="link link-primary font-medium">
          Create an account
        </Link>
      </p>
    </div>
  );
};
