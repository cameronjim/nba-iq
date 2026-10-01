import { describe, it, expect } from 'vitest';
import {
  formatGameDate,
  formatTimeWithZone,
  formatTimestampBeside,
  formatTimestampWithZone,
} from '../../src/utils/analytics';

describe('formatGameDate', () => {
  it('reads a calendar day on the local calendar, not as UTC midnight', () => {
    // arrange + act — parsing "2026-01-15" with `new Date()` yields Jan 14 for
    // every reader west of Greenwich, so this holds in any runner timezone.
    const label = formatGameDate('2026-01-15');

    // assert
    expect(label).toBe('Jan 15');
  });

  it('formats a day either side of a month boundary', () => {
    expect(formatGameDate('2026-02-04')).toBe('Feb 4');
    expect(formatGameDate('2025-12-31')).toBe('Dec 31');
  });

  it('passes an unparseable string through untouched', () => {
    expect(formatGameDate('not-a-date')).toBe('not-a-date');
    expect(formatGameDate('')).toBe('');
  });
});

describe('formatTimestampWithZone', () => {
  it('appends the short zone name so a clock time is never ambiguous', () => {
    // act
    const label = formatTimestampWithZone('2026-10-01T17:11:00Z', 'America/Los_Angeles');

    // assert
    expect(label).toMatch(/^Oct 1, 10:11\sAM PDT$/);
  });

  it('renders the same instant in the zone it is asked for', () => {
    // act + assert
    expect(formatTimestampWithZone('2026-10-01T17:11:00Z', 'America/New_York')).toMatch(
      /^Oct 1, 1:11\sPM EDT$/
    );
  });

  it('returns null for a missing or unparseable instant', () => {
    // act + assert
    expect([formatTimestampWithZone(null), formatTimestampWithZone('nope')]).toEqual([null, null]);
  });
});

describe('formatTimeWithZone', () => {
  it('drops the date but keeps the zone', () => {
    // act + assert
    expect(formatTimeWithZone('2026-10-01T17:11:00Z', 'America/Los_Angeles')).toMatch(
      /^10:11\sAM PDT$/
    );
  });
});

describe('formatTimestampBeside', () => {
  it('shows only the time when it falls on the same day as the reference', () => {
    // act + assert
    expect(
      formatTimestampBeside('2026-10-01T16:30:00Z', '2026-10-01T17:11:00Z', 'America/Los_Angeles')
    ).toMatch(/^9:30\sAM PDT$/);
  });

  it('keeps the date when the days differ', () => {
    // act + assert
    expect(
      formatTimestampBeside('2026-09-30T23:00:00Z', '2026-10-01T17:11:00Z', 'America/Los_Angeles')
    ).toMatch(/^Sep 30, 4:00\sPM PDT$/);
  });
});
