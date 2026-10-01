import { useEffect, useState } from 'react';

const STORAGE_KEY = 'theme';

export interface ThemeOption {
  id: string;
  label: string;
  scheme: 'light' | 'dark';
}

export const THEMES: readonly ThemeOption[] = [
  { id: 'night', label: 'Dark', scheme: 'dark' },
  { id: 'paper', label: 'Light', scheme: 'light' },
];

const THEME_IDS = new Set(THEMES.map((t) => t.id));
const DEFAULT_THEME = 'night';

function getInitialTheme(): string {
  if (typeof window === 'undefined') return DEFAULT_THEME;
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored && THEME_IDS.has(stored) ? stored : DEFAULT_THEME;
}

// the pre-paint script in index.html mirrors this logic and must change with it.
export function useTheme(): {
  theme: string;
  setTheme: (id: string) => void;
  themes: readonly ThemeOption[];
} {
  const [theme, setThemeState] = useState<string>(getInitialTheme);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  const setTheme = (id: string): void => {
    if (THEME_IDS.has(id)) setThemeState(id);
  };

  return { theme, setTheme, themes: THEMES };
}
