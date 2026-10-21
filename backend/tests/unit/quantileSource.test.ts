import { describe, expect, it } from 'vitest';
import {
  HEADLINE_RUN_NOTE,
  RESCORE_RUN_NOTE,
  runPredictionRowsSql,
} from '../../src/services/quantileSource.js';

describe('runPredictionRowsSql', () => {
  it('matches the notes tokens the ml pipeline writes', () => {
    // act + assert
    expect(HEADLINE_RUN_NOTE).toBe('quantiles=headline');
    expect(RESCORE_RUN_NOTE).toBe('rescore=status_change');
  });

  it('binds no parameter beyond the run it is given', () => {
    // act
    const sql = runPredictionRowsSql('pgp', '$1');

    // assert
    expect(new Set(sql.match(/\$\d+/g))).toEqual(new Set(['$1']));
  });

  it('accepts a correlated run column instead of a parameter', () => {
    // act
    const sql = runPredictionRowsSql('p', 'r.id');

    // assert
    expect(sql).not.toMatch(/\$\d+/);
  });
});
