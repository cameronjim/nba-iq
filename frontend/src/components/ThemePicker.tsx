import { useTheme } from '../hooks/useTheme';

// each row previews a theme by setting `data-theme` on a nested element, so daisyUI
// resolves the swatch to that theme's own palette.
export function ThemePicker(): JSX.Element {
  const { theme, setTheme, themes } = useTheme();

  const choose = (id: string): void => {
    setTheme(id);
    // daisyUI dropdowns close on blur.
    (document.activeElement as HTMLElement)?.blur();
  };

  return (
    <div className="dropdown dropdown-end">
      <button tabIndex={0} className="btn btn-ghost btn-sm">
        Theme
      </button>
      <ul
        tabIndex={0}
        className="dropdown-content menu z-50 mt-1 w-48 border border-base-300 bg-base-200 p-2 rounded-box"
      >
        {themes.map((t) => (
          <li key={t.id}>
            <button
              onClick={() => choose(t.id)}
              className={`flex items-center gap-2 ${theme === t.id ? 'font-semibold' : ''}`}
              aria-current={theme === t.id ? 'true' : undefined}
            >
              <span data-theme={t.id} className="inline-flex border border-base-300">
                <span className="block h-4 w-3 bg-base-100" />
                <span className="block h-4 w-3 bg-primary" />
              </span>
              {t.label}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
