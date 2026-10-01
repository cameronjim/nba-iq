import { useEffect, useState } from 'react';
import { IconChevronDown, IconChevronUp } from '../icons';
import { SkeletonLines } from '../Skeleton';
import { getPreferences, updatePreferences, type BettingPreferences } from '../../api/client';
import { invalidateBettingClientCache } from '../../api/clientCaches';

interface BettingPrefsPanelProps {
  onSaved: () => void;
}

const RISK_CHOICES = [
  { value: 'conservative', label: 'Conservative', description: 'Safer picks, smaller swings' },
  { value: 'balanced', label: 'Balanced', description: 'A mix of safe and value plays' },
  { value: 'aggressive', label: 'Aggressive', description: 'More longshots and plus-money picks' },
] as const;

const MARKET_CHOICES = [
  { value: 'spread', label: 'Spreads' },
  { value: 'total', label: 'Totals (over/under)' },
  { value: 'moneyline', label: 'Moneylines' },
  { value: 'parlay', label: 'Parlays' },
] as const;

export const BettingPrefsPanel = ({ onSaved }: BettingPrefsPanelProps) => {
  const [open, setOpen] = useState(false);
  const [prefs, setPrefs] = useState<BettingPreferences>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    getPreferences()
      .then((all) => setPrefs(all.betting ?? {}))
      .catch(() => { /* silent: panel just starts blank */ })
      .finally(() => setLoading(false));
  }, []);

  const toggleMarket = (market: NonNullable<BettingPreferences['preferred_markets']>[number]): void => {
    const current = prefs.preferred_markets ?? [];
    const next = current.includes(market)
      ? current.filter((m) => m !== market)
      : [...current, market];
    setPrefs({ ...prefs, preferred_markets: next });
  };

  const handleSave = async (): Promise<void> => {
    setSaving(true);
    setError('');
    try {
      const updated = await updatePreferences({ betting: prefs });
      setPrefs(updated.betting ?? {});
      // prefs are part of the server's picks cache key, so wipe the local cache too.
      invalidateBettingClientCache();
      onSaved();
    } catch {
      setError('Failed to save');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="border border-base-300">
      <button
        onClick={() => setOpen(!open)}
        className="px-4 py-3 flex items-center justify-between w-full text-left"
        aria-expanded={open}
      >
        <span className="text-sm font-semibold">Betting Preferences</span>
        {open ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-4 border-t border-base-300 pt-4">
          {loading ? (
            <SkeletonLines lines={3} label="Loading betting preferences" />
          ) : (
            <>
              <div>
                <span className="text-xs font-semibold block mb-2">Risk appetite</span>
                <div className="flex flex-wrap gap-2">
                  {RISK_CHOICES.map((choice) => (
                    <button
                      key={choice.value}
                      type="button"
                      onClick={() => setPrefs({ ...prefs, risk_appetite: choice.value })}
                      className={`btn btn-sm ${prefs.risk_appetite === choice.value ? 'btn-primary' : 'btn-ghost border border-base-300'}`}
                      title={choice.description}
                    >
                      {choice.label}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <span className="text-xs font-semibold block mb-2">Bet types you're interested in</span>
                <div className="flex flex-wrap gap-3">
                  {MARKET_CHOICES.map((choice) => (
                    <label key={choice.value} className="flex items-center gap-1.5 cursor-pointer text-sm">
                      <input
                        type="checkbox"
                        className="checkbox checkbox-xs"
                        checked={(prefs.preferred_markets ?? []).includes(choice.value)}
                        onChange={() => toggleMarket(choice.value)}
                      />
                      {choice.label}
                    </label>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-xs font-semibold block mb-1" htmlFor="betting-notes">
                  Anything else the AI should know?
                </label>
                <textarea
                  id="betting-notes"
                  className="textarea textarea-bordered w-full text-sm"
                  rows={2}
                  maxLength={1000}
                  placeholder={'e.g. "I like unders", "never bet against my Knicks", "only suggest underdogs"'}
                  value={prefs.extra_notes ?? ''}
                  onChange={(e) => setPrefs({ ...prefs, extra_notes: e.target.value })}
                />
              </div>

              <div className="flex items-center gap-3">
                <button onClick={handleSave} disabled={saving} className="btn btn-primary btn-sm">
                  {saving ? 'Saving' : 'Save & Re-analyze'}
                </button>
                {error && <span className="text-xs text-error">{error}</span>}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
};
