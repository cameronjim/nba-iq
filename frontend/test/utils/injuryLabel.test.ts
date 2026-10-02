import { describe, it, expect } from 'vitest';
import { injuryLabel } from '../../src/utils/injuryLabel';

describe('injuryLabel', () => {
  it('returns null when there is no status', () => {
    // act + assert
    expect(injuryLabel(null)).toBeNull();
    expect(injuryLabel('   ')).toBeNull();
  });

  it('maps out-type statuses to OUT in the error tone', () => {
    // arrange
    const statuses = ['Out', 'OUT', 'Out For Season', 'out_indefinitely', 'Suspended', 'Not With Team'];

    // act
    const labels = statuses.map((s) => injuryLabel(s));

    // assert
    for (const label of labels) {
      expect(label?.short).toBe('OUT');
      expect(label?.tone).toBe('error');
    }
  });

  it('reads a long CBS phrase as OUT and keeps the phrase as the detail', () => {
    // act
    const label = injuryLabel('Expected to be out until at least Dec 1');

    // assert
    expect(label).toEqual({ short: 'OUT', tone: 'error', detail: 'Expected to be out until at least Dec 1' });
  });

  it('maps doubtful to DOUBTFUL in the error tone', () => {
    // act + assert
    expect(injuryLabel('Doubtful')).toEqual({ short: 'DOUBTFUL', tone: 'error', detail: null });
  });

  it('maps questionable, game time decision, gtd and day-to-day to GTD in the warning tone', () => {
    // arrange
    const statuses = ['Questionable', 'Game Time Decision', 'GTD', 'Day-To-Day', 'Day_To_Day'];

    // act
    const labels = statuses.map((s) => injuryLabel(s));

    // assert
    for (const label of labels) {
      expect(label).toEqual({ short: 'GTD', tone: 'warning', detail: null });
    }
  });

  it('maps probable to PROB in the success tone', () => {
    // act + assert
    expect(injuryLabel('Probable')).toEqual({ short: 'PROB', tone: 'success', detail: null });
  });

  it('maps g league to G LEAGUE in the muted tone', () => {
    // act + assert
    expect(injuryLabel('G League')).toEqual({ short: 'G LEAGUE', tone: 'muted', detail: null });
    expect(injuryLabel('G_League')?.short).toBe('G LEAGUE');
  });

  it('falls back to INJ in the warning tone and keeps the unrecognised text as detail', () => {
    // act
    const label = injuryLabel('Rest');

    // assert
    expect(label).toEqual({ short: 'INJ', tone: 'warning', detail: 'Rest' });
  });

  it('prefers the supplied detail over the status text', () => {
    // act
    const fromPhrase = injuryLabel('Expected to be out until at least Dec 1', 'Left knee');
    const fromLabel = injuryLabel('Out', 'Left knee');

    // assert
    expect(fromPhrase?.detail).toBe('Left knee');
    expect(fromLabel).toEqual({ short: 'OUT', tone: 'error', detail: 'Left knee' });
  });

  it('keeps out-for-season as detail since it says more than the badge', () => {
    // act + assert
    expect(injuryLabel('Out For Season')?.detail).toBe('Out For Season');
  });

  it('ignores a blank supplied detail', () => {
    // act + assert
    expect(injuryLabel('Questionable', '  ')?.detail).toBeNull();
  });
});
