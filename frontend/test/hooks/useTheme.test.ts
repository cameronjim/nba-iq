import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useTheme, THEMES } from '../../src/hooks/useTheme';

beforeEach(() => {
  // jsdom has no real matchMedia, so stub a deterministic default.
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useTheme', () => {
  it('exposes the expected set of theme ids', () => {
    expect(THEMES.map((t) => t.id)).toEqual([
      'paper', 'contrast', 'night',
    ]);
  });

  it('applies a chosen theme to the document and persists it', () => {
    const { result } = renderHook(() => useTheme());

    act(() => result.current.setTheme('contrast'));

    expect(result.current.theme).toBe('contrast');
    expect(document.documentElement.getAttribute('data-theme')).toBe('contrast');
    expect(localStorage.getItem('theme')).toBe('contrast');
  });

  it('ignores an unknown theme id', () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setTheme('night'));

    act(() => result.current.setTheme('neon-banana'));

    expect(result.current.theme).toBe('night');
  });

  it('falls back to paper when a theme from the old set is stored', () => {
    // arrange
    localStorage.setItem('theme', 'cream');

    // act
    const { result } = renderHook(() => useTheme());

    // assert
    expect(result.current.theme).toBe('paper');
  });

  it('initializes from a stored theme when one is present', () => {
    localStorage.setItem('theme', 'night');

    const { result } = renderHook(() => useTheme());

    expect(result.current.theme).toBe('night');
  });
});
