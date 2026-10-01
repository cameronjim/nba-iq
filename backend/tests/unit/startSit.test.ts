import { describe, it, expect } from 'vitest';
import {
  DEFAULT_STARTING_SLOTS,
  buildStartSitDays,
  parseStartingSlots,
  startSitSentence,
  type StartSitRosterPlayer,
} from '../../src/services/startSit.js';
import type { WindowPredictionRow } from '../../src/services/watchlist.js';

const LINE = { pts: 20, reb: 6, ast: 4, stl: 1, blk: 0.5, tov: 2, fg3m: 2, fgm: 7, fga: 15, ftm: 4, fta: 5 };

function row(
  nbaId: string,
  gameId: string,
  date: string,
  scale: number,
  over: Partial<Record<string, unknown>> = {}
): WindowPredictionRow {
  const out: Record<string, unknown> = {
    game_date: date,
    nba_game_id: gameId,
    nba_player_id: nbaId,
    name: null,
    team_abbr: null,
    position: null,
    prob_active: 0.9,
    proj_min_p50: 30,
    c_pts: LINE.pts * scale,
    c_reb: null,
    c_ast: null,
    c_stl: null,
    c_blk: null,
    c_fg3m: null,
    c_fga: null,
  };
  // attempts stay fixed so shooting excess grows with scale instead of cancelling to float noise.
  for (const [stat, value] of Object.entries(LINE)) {
    out[`u_${stat}`] = stat === 'fga' || stat === 'fta' ? value : value * scale;
  }
  return { ...out, ...over } as WindowPredictionRow;
}

function rosterPlayer(i: number, team = 'GSW'): StartSitRosterPlayer {
  return { player_id: i, nba_player_id: String(100 + i), name: `Player ${String(i).padStart(2, '0')}`, team };
}

const WINDOW = { from: '2026-01-15', to: '2026-01-17', days: 3 };
const GAMES = new Map<string, [string | null, string | null]>([
  ['g1', ['GSW', 'LAL']],
  ['g2', ['BOS', 'GSW']],
]);

describe('buildStartSitDays', () => {
  it('orders each day by slate impact and sits whoever falls past the starting slots', () => {
    // arrange
    const roster = Array.from({ length: 12 }, (_, i) => rosterPlayer(i + 1));
    const rows = roster.map((p, i) => row(p.nba_player_id as string, 'g1', '2026-01-15', 0.5 + i * 0.1));
    rows.push(row('999', 'g1', '2026-01-15', 2));

    // act
    const [day] = buildStartSitDays(roster, rows, GAMES, WINDOW, DEFAULT_STARTING_SLOTS);

    // assert
    expect(day.date).toBe('2026-01-15');
    expect(day.players.map((p) => p.player_id)).toEqual([12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]);
    expect(day.players.filter((p) => p.start)).toHaveLength(10);
    expect(day.players.slice(10).every((p) => !p.start)).toBe(true);
    expect(day.recommendation).toBe('Start these 10; sit Player 02 and Player 01.');
    expect(day.players.some((p) => p.nba_player_id === '999')).toBe(false);
  });

  it('lists every window day, including days nobody on the roster plays', () => {
    // arrange
    const roster = [rosterPlayer(1), rosterPlayer(2), rosterPlayer(3)];
    const rows = [
      row('101', 'g2', '2026-01-16', 1),
      row('102', 'g2', '2026-01-16', 0.8),
      row('103', 'g2', '2026-01-16', 0.6),
      row('500', 'g2', '2026-01-16', 1.2),
    ];

    // act
    const days = buildStartSitDays(roster, rows, GAMES, WINDOW, 10);

    // assert
    expect(days.map((d) => d.date)).toEqual(['2026-01-15', '2026-01-16', '2026-01-17']);
    expect(days[0].recommendation).toBe('No one on your roster plays.');
    expect(days[1].recommendation).toBe('Start all 3 players with a game; they fit in your 10 slots.');
    expect(days[1].players.every((p) => p.start)).toBe(true);
    expect(days[1].players[0]).toMatchObject({ opponent: 'BOS', home: false });
  });

  it('respects a configured slot count', () => {
    // arrange
    const roster = [rosterPlayer(1), rosterPlayer(2), rosterPlayer(3)];
    const rows = [
      row('101', 'g1', '2026-01-15', 1),
      row('102', 'g1', '2026-01-15', 0.8),
      row('103', 'g1', '2026-01-15', 0.6),
    ];

    // act
    const [day] = buildStartSitDays(roster, rows, GAMES, WINDOW, 2);

    // assert
    expect(day.recommendation).toBe('Start these 2; sit Player 03.');
  });

  it('names the lone starter when there is one slot', () => {
    // arrange
    const roster = [rosterPlayer(1), rosterPlayer(2)];
    const rows = [row('101', 'g1', '2026-01-15', 0.6), row('102', 'g1', '2026-01-15', 1)];

    // act
    const [day] = buildStartSitDays(roster, rows, GAMES, WINDOW, 1);

    // assert
    expect(day.recommendation).toBe('Start Player 02; sit Player 01.');
  });
});

describe('startSitSentence', () => {
  it('reads as matchup, line if he plays, and chance to play', () => {
    // act
    const sentence = startSitSentence({
      name: 'Stephen Curry', opponent: 'LAL', home: true,
      points_if_plays: 20.4, minutes_if_plays: 24.6, prob_active: 0.82,
    });

    // assert
    expect(sentence).toBe('Stephen Curry · vs LAL · 20 pts, 25 min if he plays · 82% to play');
  });

  it('marks road games and drops parts the model did not project', () => {
    // act
    const sentence = startSitSentence({
      name: 'Stephen Curry', opponent: 'BOS', home: false,
      points_if_plays: null, minutes_if_plays: 31, prob_active: null,
    });

    // assert
    expect(sentence).toBe('Stephen Curry · @ BOS · 31 min if he plays');
  });
});

describe('parseStartingSlots', () => {
  it.each([
    [undefined, 10],
    ['8', 8],
    ['0', null],
    ['21', null],
    ['2.5', null],
  ])('parses %s as %s', (raw, expected) => {
    // act + assert
    expect(parseStartingSlots(raw)).toBe(expected);
  });
});
