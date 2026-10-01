import { useEffect, useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import {
  changePassword,
  getAuthToken,
  getCurrentUser,
  updateProfile,
  type CurrentUser,
} from '../api/client';
import { SkeletonLines } from '../components/Skeleton';

type TabKey = 'profile' | 'password';

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'profile', label: 'My Profile' },
  { key: 'password', label: 'Change Password' },
];

export const ProfilePage = () => {
  const navigate = useNavigate();
  const location = useLocation();

  const initialTab: TabKey =
    location.hash === '#password' ? 'password' : 'profile';
  const [activeTab, setActiveTab] = useState<TabKey>(initialTab);

  // the auth guard must come after every hook: an early return above one crashes
  // with react error #300 when the token disappears while the page is mounted.
  if (!getAuthToken()) {
    return <Navigate to="/login" replace state={{ from: '/profile' }} />;
  }

  const switchTab = (key: TabKey): void => {
    setActiveTab(key);
    navigate(`/profile${key === 'profile' ? '' : `#${key}`}`, { replace: true });
  };

  return (
    <div className="max-w-[1100px] mx-auto px-4 py-6">
      <h1 className="font-display text-3xl font-semibold uppercase tracking-wide mb-5">Account</h1>
      <div className="grid grid-cols-1 md:grid-cols-[220px_1fr] gap-5">
        <nav className="border border-base-300 p-2 h-fit">
          <ul className="menu menu-sm w-full">
            {TABS.map(({ key, label }) => (
              <li key={key}>
                <button
                  onClick={() => switchTab(key)}
                  className={activeTab === key ? 'menu-active' : ''}
                >
                  {label}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <section>
          {activeTab === 'profile' && <MyProfilePanel />}
          {activeTab === 'password' && <ChangePasswordPanel />}
        </section>
      </div>
    </div>
  );
};

const MyProfilePanel = () => {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [username, setUsername] = useState('');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    getCurrentUser()
      .then((u) => {
        setUser(u);
        setUsername(u.username);
        setName(u.name ?? '');
        setEmail(u.email ?? '');
        setPhone(u.phone ?? '');
      })
      .catch(() => setError('Failed to load profile'))
      .finally(() => setLoading(false));
  }, []);

  const dirty =
    !!user &&
    (username !== user.username ||
      name !== (user.name ?? '') ||
      email !== (user.email ?? '') ||
      phone !== (user.phone ?? ''));

  // without the auto-clear the green message stuck around forever after a save.
  useEffect(() => {
    if (!savedAt) return;
    if (dirty) {
      setSavedAt(null);
      return;
    }
    const id = setTimeout(() => setSavedAt(null), 3000);
    return () => clearTimeout(id);
  }, [savedAt, dirty]);

  const handleSave = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    if (!user) return;
    setSaving(true);
    setError('');
    try {
      const updated = await updateProfile({
        username: username === user.username ? undefined : username,
        name: name === (user.name ?? '') ? undefined : name,
        email: email === (user.email ?? '') ? undefined : email,
        phone: phone === (user.phone ?? '') ? undefined : phone,
      });
      // the response only includes changed fields, so merge to preserve has_password.
      setUser({ ...user, ...updated });
      setSavedAt(Date.now());
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error;
      setError(msg ?? 'Failed to update profile');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <SkeletonLines lines={5} label="Loading" />
    );
  }

  return (
    <>
      <h2 className="font-display text-xl font-semibold uppercase tracking-wide mb-1">My Profile</h2>
      <p className="text-sm text-muted mb-4">
        Email is used for password resets. Username is what you sign in with.
      </p>

      <form onSubmit={handleSave} className="space-y-4 max-w-md">
        <div>
          <label htmlFor="profile-username" className="text-xs font-medium text-muted mb-1 block">Username</label>
          <input
            id="profile-username"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="input input-bordered w-full"
            minLength={3}
            maxLength={50}
            autoComplete="username"
          />
          <p className="text-xs text-muted mt-1">3-50 characters. Must be unique.</p>
        </div>

        <div>
          <label htmlFor="profile-name" className="text-xs font-medium text-muted mb-1 block">Name</label>
          <input
            id="profile-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="input input-bordered w-full"
            maxLength={100}
            placeholder="Your name"
          />
        </div>

        <div>
          <label htmlFor="profile-email" className="text-xs font-medium text-muted mb-1 block">Email</label>
          <input
            id="profile-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="input input-bordered w-full"
            autoComplete="email"
          />
        </div>

        <div>
          <label htmlFor="profile-phone" className="text-xs font-medium text-muted mb-1 block">Phone</label>
          <input
            id="profile-phone"
            type="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            className="input input-bordered w-full"
            autoComplete="tel"
            placeholder="(555) 555-5555"
          />
        </div>

        {error && <p className="text-error text-sm">{error}</p>}
        {savedAt && !error && (
          <p className="text-success text-sm">Saved</p>
        )}

        <button
          type="submit"
          disabled={saving || !dirty}
          className="btn btn-primary"
        >
          {saving ? 'Saving' : 'Save changes'}
        </button>
      </form>
    </>
  );
};

const ChangePasswordPanel = () => {
  // has_password picks between change-password and first-time set-password, and is
  // null while the profile is still loading.
  const [hasPassword, setHasPassword] = useState<boolean | null>(null);
  const [profileLoadError, setProfileLoadError] = useState('');
  const [currentPw, setCurrentPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [confirmNewPw, setConfirmNewPw] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getCurrentUser()
      .then((u) => setHasPassword(u.has_password))
      .catch(() => setProfileLoadError('Failed to load account info'));
  }, []);

  const pwValidationError = newPw ? validatePassword(newPw) : null;

  const handleSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    setError('');
    const pwErr = validatePassword(newPw);
    if (pwErr) {
      setError(pwErr);
      return;
    }
    if (newPw !== confirmNewPw) {
      setError('New passwords do not match');
      return;
    }
    setLoading(true);
    try {
      // google-only users have no current password, and the backend allows that.
      await changePassword(hasPassword ? currentPw : null, newPw);
      setSuccess(true);
      setCurrentPw('');
      setNewPw('');
      setConfirmNewPw('');
      // a second change now requires the current password.
      setHasPassword(true);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error;
      setError(msg ?? 'Failed to update password');
    } finally {
      setLoading(false);
    }
  };

  if (profileLoadError) {
    return <p className="text-error text-sm">{profileLoadError}</p>;
  }
  if (hasPassword === null) {
    return (
      <SkeletonLines lines={5} label="Loading" />
    );
  }

  if (success) {
    return (
      <div>
        <h2 className="font-display text-xl font-semibold uppercase tracking-wide mb-1">Password saved</h2>
        <p className="text-sm text-success mb-4">
          Your password has been saved.
        </p>
        <button onClick={() => setSuccess(false)} className="btn btn-ghost btn-sm">
          Change password again
        </button>
      </div>
    );
  }

  const heading = hasPassword ? 'Change Password' : 'Set Password';
  const intro = hasPassword
    ? 'Update the password on your account.'
    : 'You signed in with Google and don\'t have a local password yet. Set one here so you can also sign in with your username.';
  const submitLabel = hasPassword ? 'Update password' : 'Set password';

  // currentPw is unused for google-only users, so do not gate the submit on it.
  const submitDisabled =
    loading ||
    !newPw ||
    !confirmNewPw ||
    !!pwValidationError ||
    (hasPassword && !currentPw);

  return (
    <>
      <h2 className="font-display text-xl font-semibold uppercase tracking-wide mb-1">{heading}</h2>
      <p className="text-sm text-muted mb-4">{intro}</p>

      <form onSubmit={handleSubmit} className="space-y-3 max-w-md">
        {hasPassword && (
          <div>
            <label htmlFor="pw-current" className="text-xs font-medium text-muted mb-1 block">Current password</label>
            <input
              id="pw-current"
              type="password"
              value={currentPw}
              onChange={(e) => setCurrentPw(e.target.value)}
              className={`input input-bordered w-full ${error === 'Current password is incorrect' ? 'input-error' : ''}`}
              autoComplete="current-password"
            />
          </div>
        )}

        <div>
          <label htmlFor="pw-new" className="text-xs font-medium text-muted mb-1 block">New password</label>
          <input
            id="pw-new"
            type="password"
            value={newPw}
            onChange={(e) => setNewPw(e.target.value)}
            className="input input-bordered w-full"
            autoComplete="new-password"
          />
          {newPw && (
            <p
              className={`text-xs mt-1 ${
                pwValidationError ? 'text-warning' : 'text-success'
              }`}
            >
              {pwValidationError ?? 'Meets the password rules'}
            </p>
          )}
        </div>

        <div>
          <label htmlFor="pw-confirm" className="text-xs font-medium text-muted mb-1 block">Confirm new password</label>
          <input
            id="pw-confirm"
            type="password"
            value={confirmNewPw}
            onChange={(e) => setConfirmNewPw(e.target.value)}
            className="input input-bordered w-full"
            autoComplete="new-password"
          />
        </div>

        <p className="text-xs text-muted">
          Min 8 chars · 1 uppercase · 1 number or symbol
        </p>

        {error && <p className="text-error text-sm">{error}</p>}

        <button type="submit" disabled={submitDisabled} className="btn btn-primary">
          {loading ? 'Saving' : submitLabel}
        </button>
      </form>
    </>
  );
};

function validatePassword(pwd: string): string | null {
  if (pwd.length < 8) return 'At least 8 characters required';
  if (!/[A-Z]/.test(pwd)) return 'Must include at least one uppercase letter';
  if (!/[0-9!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?`~]/.test(pwd)) {
    return 'Must include at least one number or symbol';
  }
  return null;
}
