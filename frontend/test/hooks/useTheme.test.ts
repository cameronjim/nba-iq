import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useTheme, THEMES } from '../../src/hooks/useTheme';

beforeEach(() => {
  // a light os preference must not override the dark default.
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useTheme', () => {
  it('exposes one dark and one light theme', () => {
    // act + assert
    expect(THEMES.map((t) => t.id)).toEqual(['night', 'paper']);
  });

  it('defaults to the dark theme when nothing is stored', () => {
    // act
    const { result } = renderHook(() => useTheme());

    // assert
    expect(result.current.theme).toBe('night');
  });

  it('applies a chosen theme to the document and persists it', () => {
    // arrange
    const { result } = renderHook(() => useTheme());

    // act
    act(() => result.current.setTheme('paper'));

    // assert
    expect(result.current.theme).toBe('paper');
    expect(document.documentElement.getAttribute('data-theme')).toBe('paper');
    expect(localStorage.getItem('theme')).toBe('paper');
  });

  it('ignores an unknown theme id', () => {
    // arrange
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setTheme('paper'));

    // act
    act(() => result.current.setTheme('neon-banana'));

    // assert
    expect(result.current.theme).toBe('paper');
  });

  it('falls back to dark when a removed theme is stored', () => {
    // arrange
    localStorage.setItem('theme', 'contrast');

    // act
    const { result } = renderHook(() => useTheme());

    // assert
    expect(result.current.theme).toBe('night');
  });

  it('initializes from a stored theme when one is present', () => {
    // arrange
    localStorage.setItem('theme', 'paper');

    // act
    const { result } = renderHook(() => useTheme());

    // assert
    expect(result.current.theme).toBe('paper');
  });
});
