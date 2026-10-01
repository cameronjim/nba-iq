import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { getPreferences, getAuthToken } from '../api/client';
import { IconClose } from './icons';

const DISMISS_KEY = 'preferences_prompt_dismissed';

export const PreferencesPrompt = () => {
  const [shouldShow, setShouldShow] = useState(false);

  useEffect(() => {
    if (!getAuthToken()) return;
    if (localStorage.getItem(DISMISS_KEY) === '1') return;

    getPreferences()
      .then((prefs) => {
        const hasAny = !!(
          prefs.risk_tolerance ||
          prefs.player_age_pref ||
          prefs.opportunity_chase ||
          prefs.league_format ||
          prefs.league_size ||
          prefs.roster_strategy ||
          prefs.trade_activity ||
          prefs.schedule_weight ||
          prefs.rookie_hunger ||
          prefs.playoff_focus ||
          prefs.bench_philosophy ||
          (prefs.punt_categories && prefs.punt_categories.length > 0) ||
          (prefs.priority_categories && prefs.priority_categories.length > 0) ||
          (prefs.position_needs && prefs.position_needs.length > 0) ||
          (prefs.extra_notes && prefs.extra_notes.length > 0)
        );
        setShouldShow(!hasAny);
      })
      .catch(() => { /* silent: non-critical */ });
  }, []);

  if (!shouldShow) return null;

  return (
    <div className="bg-base-200 border border-base-300 px-4 py-3 mb-4 flex items-center justify-between gap-3">
      <div className="flex items-center gap-3">
        <div>
          <p className="text-sm font-medium">Get sharper recommendations</p>
          <p className="text-xs text-muted">
            Set your team preferences so every suggestion fits your strategy.
          </p>
        </div>
      </div>
      <div className="flex items-center gap-1 flex-shrink-0">
        <Link to="/preferences" className="btn btn-primary btn-sm">
          Set preferences
        </Link>
        <button
          onClick={() => {
            localStorage.setItem(DISMISS_KEY, '1');
            setShouldShow(false);
          }}
          className="btn btn-ghost btn-sm"
          aria-label="Dismiss"
          title="Dismiss"
        >
          <IconClose size={14} />
        </button>
      </div>
    </div>
  );
};
