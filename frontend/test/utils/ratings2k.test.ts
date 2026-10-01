import { describe, it, expect } from 'vitest';
import type { Rating2kAttribute } from '../../src/types';
import {
  OTHER_GROUP_LABEL, RATINGS_2K_ATTRIBUTION, formatAttributeLabel, formatPositions,
  formatRatingDelta, groupRating2kAttributes, ratingBarPercent, ratingTier,
  tierBadgeClass, tierBarClass,
} from '../../src/utils/ratings2k';

// deliberately scrambled so the tests prove grouping does not depend on api order.
const ATTRIBUTE_NAMES = [
  'vertical', 'threePointShot', 'passIQ', 'interiorDefense', 'drivingDunk',
  'agility', 'ballHandle', 'block', 'closeShot', 'defensiveConsistency',
  'defensiveRebound', 'drawFoul', 'drivingLayup', 'durability', 'freeThrow',
  'hands', 'helpDefenseIQ', 'hustle', 'midRangeShot', 'offensiveConsistency',
  'offensiveRebound', 'passAccuracy', 'passPerception', 'passVision',
  'perimeterDefense', 'postControl', 'postFade', 'postHook', 'shotIQ', 'speed',
  'speedWithBall', 'stamina', 'standingDunk', 'steal', 'strength',
];

function attributes(names: string[] = ATTRIBUTE_NAMES): Rating2kAttribute[] {
  return names.map((attribute_name, index) => ({ attribute_name, value: 60 + index }));
}

describe('groupRating2kAttributes', () => {
  it('sorts all 35 known attributes into the five named groups', () => {
    const groups = groupRating2kAttributes(attributes());

    expect(groups.map((g) => g.label)).toEqual([
      'Outside Scoring', 'Inside Scoring', 'Athleticism', 'Playmaking',
      'Defense & Rebounding',
    ]);
    expect(groups.reduce((sum, g) => sum + g.attributes.length, 0)).toBe(35);
  });

  it('orders attributes within a group by the group definition, not the api order', () => {
    const scrambled = attributes(['threePointShot', 'closeShot', 'freeThrow']);

    const [group] = groupRating2kAttributes(scrambled);

    expect(group.label).toBe('Outside Scoring');
    expect(group.attributes.map((a) => a.attribute_name)).toEqual([
      'closeShot', 'threePointShot', 'freeThrow',
    ]);
  });

  it('puts an unknown attribute name in a trailing Other group instead of dropping it', () => {
    const withUnknown = attributes(['closeShot', 'quantumDribble']);

    const groups = groupRating2kAttributes(withUnknown);

    expect(groups.map((g) => g.label)).toEqual(['Outside Scoring', OTHER_GROUP_LABEL]);
    expect(groups[1].attributes.map((a) => a.attribute_name)).toEqual(['quantumDribble']);
  });

  it('still groups an attribute whose casing or separators changed upstream', () => {
    const groups = groupRating2kAttributes(attributes(['three_point_shot', 'Ball-Handle']));

    expect(groups.map((g) => g.label)).toEqual(['Outside Scoring', 'Playmaking']);
  });

  it('drops empty groups and handles an empty attribute list', () => {
    expect(groupRating2kAttributes([])).toEqual([]);
    expect(groupRating2kAttributes(attributes(['steal'])).map((g) => g.label)).toEqual([
      'Defense & Rebounding',
    ]);
  });
});

describe('formatAttributeLabel', () => {
  it('turns camelCase names into readable labels', () => {
    expect(formatAttributeLabel('threePointShot')).toBe('Three Point Shot');
    expect(formatAttributeLabel('speedWithBall')).toBe('Speed With Ball');
    expect(formatAttributeLabel('steal')).toBe('Steal');
  });

  it('keeps acronyms intact', () => {
    expect(formatAttributeLabel('shotIQ')).toBe('Shot IQ');
    expect(formatAttributeLabel('helpDefenseIQ')).toBe('Help Defense IQ');
  });

  it('handles separators and blank input', () => {
    expect(formatAttributeLabel('driving_layup')).toBe('Driving Layup');
    expect(formatAttributeLabel('post-fade')).toBe('Post Fade');
    expect(formatAttributeLabel('')).toBe('');
  });
});

describe('formatPositions', () => {
  it('joins a list and splits a joined string the same way', () => {
    expect(formatPositions(['PG', 'SG'])).toBe('PG / SG');
    expect(formatPositions('PG/SG')).toBe('PG / SG');
    expect(formatPositions('C, PF')).toBe('C / PF');
  });

  it('returns an empty string when there is nothing to show', () => {
    expect(formatPositions(null)).toBe('');
    expect(formatPositions([])).toBe('');
    expect(formatPositions(undefined)).toBe('');
  });
});

describe('rating tiers', () => {
  it('bands values from elite down to weak', () => {
    expect(ratingTier(98)).toBe('elite');
    expect(ratingTier(85)).toBe('elite');
    expect(ratingTier(84)).toBe('strong');
    expect(ratingTier(70)).toBe('strong');
    expect(ratingTier(69)).toBe('average');
    expect(ratingTier(55)).toBe('average');
    expect(ratingTier(54)).toBe('weak');
    expect(ratingTier(null)).toBe('unknown');
  });

  it('coerces string values, since numeric columns can arrive as strings', () => {
    expect(ratingTier('98')).toBe('elite');
    expect(tierBarClass('40')).toBe('bg-error');
  });

  it('maps every tier to a single-hue daisyUI class, never a palette color', () => {
    const classes = [
      tierBarClass(98), tierBarClass(80), tierBarClass(60), tierBarClass(30), tierBarClass(null),
      tierBadgeClass(98), tierBadgeClass(80), tierBadgeClass(60), tierBadgeClass(30),
      tierBadgeClass(null),
    ];

    expect(classes).toEqual([
      'bg-success', 'bg-primary', 'bg-base-content', 'bg-error', 'bg-base-300',
      'badge-success', 'badge-primary', 'badge-outline', 'badge-error', 'badge-ghost',
    ]);
    for (const className of classes) {
      expect(className).not.toMatch(
        /(slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}/
      );
    }
  });
});

describe('ratingBarPercent', () => {
  it('scales a value against the 2K maximum and clamps out-of-range input', () => {
    expect(ratingBarPercent(99)).toBe(100);
    expect(ratingBarPercent(0)).toBe(0);
    expect(ratingBarPercent(150)).toBe(100);
    expect(ratingBarPercent(-5)).toBe(0);
    expect(ratingBarPercent(null)).toBe(0);
    expect(ratingBarPercent(50)).toBe(51);
  });
});

describe('formatRatingDelta', () => {
  it('signs the change and blanks a missing one', () => {
    expect(formatRatingDelta(3)).toBe('+3');
    expect(formatRatingDelta(-2)).toBe('-2');
    expect(formatRatingDelta(0)).toBe('0');
    expect(formatRatingDelta(null)).toBe('');
  });
});

describe('RATINGS_2K_ATTRIBUTION', () => {
  it('credits the api, the source site, and disclaims affiliation', () => {
    expect(RATINGS_2K_ATTRIBUTION).toContain('nba2kapi.com');
    expect(RATINGS_2K_ATTRIBUTION).toContain('2kratings.com');
    expect(RATINGS_2K_ATTRIBUTION).toMatch(/Not affiliated/i);
  });
});
