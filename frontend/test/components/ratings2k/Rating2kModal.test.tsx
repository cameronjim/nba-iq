import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Rating2kModal } from '../../../src/components/ratings2k/Rating2kModal';
import type { Rating2kDetail, Rating2kSummary } from '../../../src/types';
import { formatAttributeLabel } from '../../../src/utils/ratings2k';

vi.mock('../../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../src/api/client')>();
  return {
    ...actual,
    getRatings2kPlayer: vi.fn(),
  };
});

const { getRatings2kPlayer } = await import('../../../src/api/client');
const detailMock = vi.mocked(getRatings2kPlayer);

const ATTRIBUTE_NAMES = [
  'agility', 'ballHandle', 'block', 'closeShot', 'defensiveConsistency',
  'defensiveRebound', 'drawFoul', 'drivingDunk', 'drivingLayup', 'durability',
  'freeThrow', 'hands', 'helpDefenseIQ', 'hustle', 'interiorDefense',
  'midRangeShot', 'offensiveConsistency', 'offensiveRebound', 'passAccuracy',
  'passIQ', 'passPerception', 'passVision', 'perimeterDefense', 'postControl',
  'postFade', 'postHook', 'shotIQ', 'speed', 'speedWithBall', 'stamina',
  'standingDunk', 'steal', 'strength', 'threePointShot', 'vertical',
];

const summary: Rating2kSummary = {
  slug: 'nikola-jokic',
  name: 'Nikola Jokic',
  team: 'Denver Nuggets',
  team_type: 'curr',
  overall: 98,
  positions: ['C'],
  game_version: 'NBA 2K25',
  player_image: 'https://example.test/jokic.png',
};

function detail(overrides: Partial<Rating2kDetail> = {}): Rating2kDetail {
  return {
    player: {
      ...summary,
      archetype: 'Two-Way Playmaking Big',
      build: 'Point Center',
      height: '6\'11"',
      weight: '284 lbs',
      wingspan: '7\'3"',
    },
    // odd values so no attribute collides with an overall shown in the header
    attributes: ATTRIBUTE_NAMES.map((attribute_name, index) => ({
      attribute_name,
      value: 41 + index * 2,
    })),
    badges: [{ badge_name: 'Dimer', tier: 'Hall of Fame' }, { badge_name: 'Post Playmaker' }],
    rating_history: [
      { game_version: 'NBA 2K25', overall: 98, delta: 2 },
      { game_version: 'NBA 2K24', overall: 96, delta: -1 },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  detailMock.mockResolvedValue(detail());
});

describe('Rating2kModal', () => {
  it('renders every one of the 35 attributes with its value, grouped into sections', async () => {
    render(<Rating2kModal slug="nikola-jokic" onClose={() => {}} />);
    await screen.findByText('Three Point Shot');

    for (const name of ATTRIBUTE_NAMES) {
      expect(screen.getByText(formatAttributeLabel(name))).toBeInTheDocument();
    }
    for (const group of ['Outside Scoring', 'Inside Scoring', 'Athleticism', 'Playmaking', 'Defense & Rebounding']) {
      expect(screen.getByText(group)).toBeInTheDocument();
    }
    expect(screen.getByText('41')).toBeInTheDocument();
    expect(screen.getByText('109')).toBeInTheDocument();
  });

  it('shows the overall, bio, archetype, badges, and rating history', async () => {
    render(<Rating2kModal slug="nikola-jokic" onClose={() => {}} />);

    expect(await screen.findByRole('heading', { name: 'Nikola Jokic' })).toBeInTheDocument();
    expect(screen.getAllByText('98')).toHaveLength(2);
    expect(screen.getByText(/Denver Nuggets/)).toBeInTheDocument();
    expect(screen.getByText(/Two-Way Playmaking Big/)).toBeInTheDocument();
    expect(screen.getByText('Overall by Game')).toBeInTheDocument();
    expect(screen.getByText('NBA 2K24')).toBeInTheDocument();
    expect(screen.getByText('+2')).toBeInTheDocument();
    expect(screen.getByText('-1')).toBeInTheDocument();
    expect(screen.getByText(/Dimer/)).toBeInTheDocument();
  });

  it('renders the attribution footnote', async () => {
    render(<Rating2kModal slug="nikola-jokic" onClose={() => {}} />);

    expect(await screen.findByText(/nba2kapi\.com/)).toBeInTheDocument();
    expect(screen.getByText(/Not affiliated/)).toBeInTheDocument();
  });

  it('shows skeletons while loading, with the summary header already in place', () => {
    detailMock.mockReturnValue(new Promise(() => {}));

    render(<Rating2kModal slug="nikola-jokic" summary={summary} onClose={() => {}} />);

    expect(screen.getAllByRole('status', { name: 'Loading 2K ratings' }).length).toBeGreaterThan(0);
    expect(document.body.querySelector('.loading-spinner')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Nikola Jokic' })).toBeInTheDocument();
  });

  it('shows an error state with a retry that refetches', async () => {
    detailMock.mockRejectedValueOnce(new Error('ratings down'));
    render(<Rating2kModal slug="nikola-jokic" onClose={() => {}} />);
    await screen.findByText(/Failed to load 2K ratings/i);
    detailMock.mockResolvedValue(detail());
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: /Try Again/i }));

    expect(await screen.findByText('Three Point Shot')).toBeInTheDocument();
    expect(detailMock).toHaveBeenCalledTimes(2);
  });

  it('shows a not-found state when the slug resolves to nothing', async () => {
    detailMock.mockResolvedValue(null);

    render(<Rating2kModal slug="ghost-player" onClose={() => {}} />);

    expect(await screen.findByText(/No 2K ratings for this player/i)).toBeInTheDocument();
    expect(screen.queryByText('Three Point Shot')).not.toBeInTheDocument();
  });

  it('groups an attribute the api added since release under Other', async () => {
    detailMock.mockResolvedValue(
      detail({ attributes: [{ attribute_name: 'quantumDribble', value: 77 }] })
    );

    render(<Rating2kModal slug="nikola-jokic" onClose={() => {}} />);

    expect(await screen.findByText('Other')).toBeInTheDocument();
    expect(screen.getByText('Quantum Dribble')).toBeInTheDocument();
  });

  it('closes on the close button, the backdrop, and the escape key', async () => {
    const onClose = vi.fn();
    render(<Rating2kModal slug="nikola-jokic" summary={summary} onClose={onClose} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: /Close 2K ratings/i }));
    const backdrop = document.body.querySelector('.modal-backdrop');
    if (backdrop) await user.click(backdrop);
    await user.keyboard('{Escape}');

    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it('renders dashes instead of NaN when values are missing', async () => {
    detailMock.mockResolvedValue(
      detail({
        attributes: [{ attribute_name: 'steal', value: null }],
        rating_history: [{ game_version: 'NBA 2K25', overall: null, delta: null }],
      })
    );

    render(<Rating2kModal slug="nikola-jokic" onClose={() => {}} />);
    await screen.findByText('Steal');

    expect(document.body.textContent).not.toMatch(/NaN|null|undefined/);
  });
});
