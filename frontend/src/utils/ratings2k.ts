import type { NumericLike, Rating2kAttribute } from '../types';
import { toStatNumber } from './stats';

// required credit line, must be rendered wherever 2K data appears.
export const RATINGS_2K_ATTRIBUTION =
  '2K ratings via nba2kapi.com (data from 2kratings.com). Not affiliated with or endorsed by 2K Sports, Take-Two, or the NBA.';

export const OTHER_GROUP_LABEL = 'Other';

const MAX_RATING = 99;

const GROUP_DEFINITIONS: Array<{ label: string; attributeNames: string[] }> = [
  {
    label: 'Outside Scoring',
    attributeNames: [
      'closeShot', 'midRangeShot', 'threePointShot', 'freeThrow', 'shotIQ',
      'offensiveConsistency',
    ],
  },
  {
    label: 'Inside Scoring',
    attributeNames: [
      'drivingLayup', 'drivingDunk', 'standingDunk', 'postControl', 'postHook',
      'postFade', 'drawFoul', 'hands',
    ],
  },
  {
    label: 'Athleticism',
    attributeNames: [
      'speed', 'agility', 'strength', 'vertical', 'stamina', 'hustle',
      'durability', 'speedWithBall',
    ],
  },
  {
    label: 'Playmaking',
    attributeNames: ['passAccuracy', 'passIQ', 'passVision', 'passPerception', 'ballHandle'],
  },
  {
    label: 'Defense & Rebounding',
    attributeNames: [
      'interiorDefense', 'perimeterDefense', 'steal', 'block', 'helpDefenseIQ',
      'defensiveConsistency', 'offensiveRebound', 'defensiveRebound',
    ],
  },
];

// survives upstream casing and separator changes: `three_point_shot` groups with `threePointShot`.
function canonicalKey(attributeName: string): string {
  return attributeName.replace(/[^a-z0-9]/gi, '').toLowerCase();
}

const GROUP_LABEL_BY_KEY = new Map<string, string>();
const ORDER_BY_KEY = new Map<string, number>();

for (const group of GROUP_DEFINITIONS) {
  group.attributeNames.forEach((name, index) => {
    GROUP_LABEL_BY_KEY.set(canonicalKey(name), group.label);
    ORDER_BY_KEY.set(canonicalKey(name), index);
  });
}

export interface Rating2kAttributeGroup {
  label: string;
  attributes: Rating2kAttribute[];
}

export function groupRating2kAttributes(attributes: Rating2kAttribute[]): Rating2kAttributeGroup[] {
  const buckets = new Map<string, Rating2kAttribute[]>();

  for (const attribute of attributes) {
    const label = GROUP_LABEL_BY_KEY.get(canonicalKey(attribute.attribute_name)) ?? OTHER_GROUP_LABEL;
    const bucket = buckets.get(label);
    if (bucket) bucket.push(attribute);
    else buckets.set(label, [attribute]);
  }

  const groups: Rating2kAttributeGroup[] = [];

  for (const group of GROUP_DEFINITIONS) {
    const bucket = buckets.get(group.label);
    if (!bucket || bucket.length === 0) continue;
    const ordered = [...bucket].sort(
      (a, b) =>
        (ORDER_BY_KEY.get(canonicalKey(a.attribute_name)) ?? 0) -
        (ORDER_BY_KEY.get(canonicalKey(b.attribute_name)) ?? 0)
    );
    groups.push({ label: group.label, attributes: ordered });
  }

  const other = buckets.get(OTHER_GROUP_LABEL);
  if (other && other.length > 0) groups.push({ label: OTHER_GROUP_LABEL, attributes: other });

  return groups;
}

export function formatAttributeLabel(attributeName: string): string {
  const spaced = attributeName
    .replace(/[_-]+/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    // keeps an acronym run together: "IQRating" becomes "IQ Rating".
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')
    .trim();

  return spaced
    .split(/\s+/)
    .filter((word) => word.length > 0)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

// the positions field arrives as either a list or a delimited string.
export function formatPositions(positions: string[] | string | null | undefined): string {
  if (!positions) return '';
  const parts = Array.isArray(positions) ? positions : positions.split(/[/,]/);
  return parts.map((part) => part.trim()).filter((part) => part.length > 0).join(' / ');
}

export type Rating2kTier = 'elite' | 'strong' | 'average' | 'weak' | 'unknown';

export function ratingTier(value: NumericLike | null | undefined): Rating2kTier {
  const parsed = toStatNumber(value);
  if (parsed === null) return 'unknown';
  if (parsed >= 85) return 'elite';
  if (parsed >= 70) return 'strong';
  if (parsed >= 55) return 'average';
  return 'weak';
}

const TIER_BAR_CLASS: Record<Rating2kTier, string> = {
  elite: 'bg-success',
  strong: 'bg-primary',
  average: 'bg-base-content',
  weak: 'bg-error',
  unknown: 'bg-base-300',
};

const TIER_BADGE_CLASS: Record<Rating2kTier, string> = {
  elite: 'badge-success',
  strong: 'badge-primary',
  average: 'badge-outline',
  weak: 'badge-error',
  unknown: 'badge-ghost',
};

export function tierBarClass(value: NumericLike | null | undefined): string {
  return TIER_BAR_CLASS[ratingTier(value)];
}

export function tierBadgeClass(value: NumericLike | null | undefined): string {
  return TIER_BADGE_CLASS[ratingTier(value)];
}

export function ratingBarPercent(value: NumericLike | null | undefined): number {
  const parsed = toStatNumber(value);
  if (parsed === null) return 0;
  const percent = (parsed / MAX_RATING) * 100;
  return Math.max(0, Math.min(100, Math.round(percent)));
}

export function formatRatingDelta(delta: NumericLike | null | undefined): string {
  const parsed = toStatNumber(delta);
  if (parsed === null) return '';
  if (parsed > 0) return `+${parsed}`;
  return String(parsed);
}
