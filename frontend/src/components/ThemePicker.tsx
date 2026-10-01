import { useTheme } from '../hooks/useTheme';

export function ThemePicker(): JSX.Element {
  const { theme, setTheme, themes } = useTheme();
  const next = themes.find((t) => t.id !== theme) ?? themes[0];

  return (
    <button onClick={() => setTheme(next.id)} className="btn btn-ghost" aria-label={`Switch to ${next.label.toLowerCase()} mode`}>
      {next.label}
    </button>
  );
}
