import { describe, it, expect } from 'vitest';
import { stripSlop, stripSlopList } from '../../src/services/plainText.js';

describe('stripSlop', () => {
  it('replaces a spaced em dash with a comma', () => {
    // act + assert
    expect(stripSlop('Strong rebounder \u2014 weak from the line')).toBe('Strong rebounder, weak from the line');
  });

  it('replaces an unspaced em dash with a comma and a space', () => {
    // act + assert
    expect(stripSlop('Hot streak\u2014ride it')).toBe('Hot streak, ride it');
  });

  it('turns a leading em dash into a plain list marker', () => {
    // act + assert
    expect(stripSlop('\u2014 first point\n  \u2014 second point')).toBe('- first point\n  - second point');
  });

  it('drops a trailing em dash', () => {
    // act + assert
    expect(stripSlop('He might sit \u2014')).toBe('He might sit');
  });

  it('removes emoji, flags and variation selectors without leaving double spaces', () => {
    // arrange
    const input = 'Great pickup 🔥 for steals 🏀 and 3s ✅ today ❤️ 🇺🇸';

    // act
    const result = stripSlop(input);

    // assert
    expect(result).toBe('Great pickup for steals and 3s today');
  });

  it('leaves plain text, numbers and newlines alone', () => {
    // arrange
    const input = '1. Take Jokic (24.5 PTS, 11.2 REB)\n2. Sit Bench Guy';

    // act + assert
    expect(stripSlop(input)).toBe(input);
  });

  it('keeps ordinary punctuation and accented names', () => {
    // act + assert
    expect(stripSlop("Dončić's FG% is 47.8% - fine")).toBe("Dončić's FG% is 47.8% - fine");
  });
});

describe('stripSlopList', () => {
  it('cleans every string and drops non-strings', () => {
    // act
    const result = stripSlopList(['Good \u2014 cheap 🔥', 42, null, 'Fine']);

    // assert
    expect(result).toEqual(['Good, cheap', 'Fine']);
  });

  it('returns an empty list for a non-array', () => {
    // act + assert
    expect(stripSlopList(undefined)).toEqual([]);
  });
});
