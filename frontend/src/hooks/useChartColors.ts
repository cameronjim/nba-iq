import { useEffect, useState } from 'react';

// recharts writes colors onto svg presentation attributes, where `var(...)` is not resolved.
const COLOR_VARS = {
  primary: '--color-primary',
  accent: '--color-accent',
  success: '--color-success',
  error: '--color-error',
  content: '--color-base-content',
  grid: '--color-base-300',
  surface: '--color-base-100',
} as const;

export type ChartColors = Record<keyof typeof COLOR_VARS, string>;

const FALLBACK = 'currentColor';

function readChartColors(): ChartColors {
  const styles = getComputedStyle(document.documentElement);
  const entries = Object.entries(COLOR_VARS).map(([name, cssVar]) => [
    name,
    styles.getPropertyValue(cssVar).trim() || FALLBACK,
  ]);
  return Object.fromEntries(entries) as ChartColors;
}

export function useChartColors(): ChartColors {
  const [colors, setColors] = useState<ChartColors>(readChartColors);

  useEffect(() => {
    const refresh = (): void => setColors(readChartColors());
    // the theme is applied in an effect, so the very first read can predate it.
    refresh();
    const observer = new MutationObserver(refresh);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });
    return () => observer.disconnect();
  }, []);

  return colors;
}
