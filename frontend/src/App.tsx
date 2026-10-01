import { useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { GoogleOAuthProvider } from '@react-oauth/google';
import { Navbar } from './components/Navbar';
import { HomePage } from './pages/HomePage';
import { StatsPage } from './pages/StatsPage';
import { HistoryPage } from './pages/HistoryPage';
import { PlayerPage } from './pages/PlayerPage';
import { Ratings2kPage } from './pages/Ratings2kPage';
import { SlatePage } from './pages/SlatePage';
import { WatchlistPage } from './pages/WatchlistPage';
import { FantasyPage } from './pages/FantasyPage';
import { ImproveTeamPage } from './pages/ImproveTeamPage';
import { BettingPage } from './pages/BettingPage';
import { LoginPage } from './pages/LoginPage';
import { RegisterPage } from './pages/RegisterPage';
import { ForgotPasswordPage } from './pages/ForgotPasswordPage';
import { ResetPasswordPage } from './pages/ResetPasswordPage';
import { ProfilePage } from './pages/ProfilePage';
import { PreferencesPage } from './pages/PreferencesPage';
import { AboutPage } from './pages/AboutPage';
import { AdminPage } from './pages/AdminPage';
import { TermsPage } from './pages/TermsPage';
import { PrivacyPage } from './pages/PrivacyPage';
import { SiteFooter } from './components/SiteFooter';
import { PageViewTracker } from './components/PageViewTracker';
import { getAuthToken, setAuthToken } from './api/client';
import { useWarmupPrefetch } from './hooks/useWarmupPrefetch';
import { invalidateCached, CACHE_KEYS } from './api/resourceCache';
import { invalidateAIClientCaches } from './api/clientCaches';

export const App = (): JSX.Element => {
  const [isLoggedIn, setIsLoggedIn] = useState(() => !!getAuthToken());

  // the ai endpoints are excluded because they cost per call.
  useWarmupPrefetch(isLoggedIn);

  const handleLogout = (): void => {
    setAuthToken(null);
    setIsLoggedIn(false);
    // user-scoped caches must not leak into the next account on this device.
    invalidateCached(CACHE_KEYS.roster);
    invalidateCached(CACHE_KEYS.bets);
    invalidateAIClientCaches();
  };

  const handleLoginSuccess = (): void => {
    setIsLoggedIn(true);
  };

  // public-by-design identifier, so it is safe in the bundle. unset makes GoogleLogin a no-op.
  const googleClientId = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? '';

  return (
    <GoogleOAuthProvider clientId={googleClientId}>
      <BrowserRouter>
        <PageViewTracker />
        <div className="flex min-h-screen flex-col bg-base-100 text-base-content">
          <Navbar isLoggedIn={isLoggedIn} onLogout={handleLogout} />
          <main className="flex-1">
            <Routes>
              <Route path="/" element={<HomePage isLoggedIn={isLoggedIn} />} />
              <Route path="/stats" element={<StatsPage />} />
              <Route path="/player/:id" element={<PlayerPage />} />
              <Route path="/projections" element={<SlatePage />} />
              <Route path="/watchlist" element={<WatchlistPage />} />
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/ratings" element={<Ratings2kPage />} />
              <Route path="/fantasy" element={<FantasyPage isLoggedIn={isLoggedIn} />} />
              <Route path="/improve" element={<ImproveTeamPage isLoggedIn={isLoggedIn} />} />
              <Route path="/betting" element={<BettingPage isLoggedIn={isLoggedIn} />} />
              <Route path="/login" element={<LoginPage onLogin={handleLoginSuccess} />} />
              <Route path="/register" element={<RegisterPage onRegister={handleLoginSuccess} />} />
              <Route path="/forgot-password" element={<ForgotPasswordPage />} />
              <Route path="/reset-password" element={<ResetPasswordPage />} />
              <Route path="/profile" element={<ProfilePage />} />
              <Route path="/change-password" element={<Navigate to="/profile#password" replace />} />
              <Route path="/preferences" element={<PreferencesPage />} />
              <Route path="/about" element={<AboutPage />} />
              <Route path="/terms" element={<TermsPage />} />
              <Route path="/privacy" element={<PrivacyPage />} />
              <Route path="/admin" element={<AdminPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
          <SiteFooter />
        </div>
      </BrowserRouter>
    </GoogleOAuthProvider>
  );
};
