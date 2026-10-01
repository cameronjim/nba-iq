import { describe, it, expect } from 'vitest';
import {
  formatAmerican,
  formatLine,
  formatMoney,
  formatSignedMoney,
} from '../../src/utils/formatOdds';

describe('formatMoney', () => {
  it('formats dollars with the sign outside the symbol', () => {
    expect(formatMoney(13.75)).toBe('$13.75');
    expect(formatMoney(-50)).toBe('-$50.00');
    expect(formatMoney(0)).toBe('$0.00');
  });
});

describe('formatSignedMoney', () => {
  it('always shows the sign for net results', () => {
    expect(formatSignedMoney(47.62)).toBe('+$47.62');
    expect(formatSignedMoney(-20)).toBe('-$20.00');
    expect(formatSignedMoney(0)).toBe('+$0.00');
  });
});

describe('formatAmerican', () => {
  it('prefixes positive odds with a plus sign', () => {
    expect(formatAmerican(150)).toBe('+150');
    expect(formatAmerican(-110)).toBe('-110');
    expect(formatAmerican(100)).toBe('+100');
  });
});

describe('formatLine', () => {
  it('shows plus on positive lines only', () => {
    expect(formatLine(2.5)).toBe('+2.5');
    expect(formatLine(-2.5)).toBe('-2.5');
    expect(formatLine(0)).toBe('0');
  });
});
