import { formatTimestamp } from './analytics';
import { formatStat, toStatNumber } from './stats';
import { reasonEvidenceLines, slateEvidenceLines } from './vsUsual';
import type {
  NumericLike,
  SlateGame,
  SlatePlayer,
  WatchlistEvidence,
  WatchlistGame,
  WatchlistPlayer,
  WatchlistReason,
} from '../types';

export type ProjectionSource =
  | { kind: 'tonight'; player: SlatePlayer; game: SlateGame }
  | { kind: 'week'; player: WatchlistPlayer };

export type Tone = 'error' | 'warning' | 'success';

export interface InjuryWords {
  label: string;
  tone: Tone;
  description: string;
}

export interface ProjectionRowModel {
  id: string;
  name: string;
  nameIsPlaceholder: boolean;
  matchup: string | null;
  line: string | null;
  chance: string | null;
  chanceTone: Tone | null;
  // the whole first line as one string, the same text the row renders.
  sentence: string;
  note: string | null;
  preseason: boolean;
  injury: InjuryWords | null;
  categories: string | null;
  sitsSentence: string | null;
  evidence: string[];
  games: WatchlistGame[];
}

export const ROW_SEPARATOR = ' · ';

const INJURY_WORDS: Record<string, { label: string; tone: Tone }> = {
  out: { label: 'Out', tone: 'error' },
  doubtful: { label: 'Doubtful', tone: 'error' },
  questionable: { label: 'Questionable', tone: 'warning' },
  probable: { label: 'Probable', tone: 'success' },
  available: { label: 'Available', tone: 'success' },
};

const CATEGORY_LABELS: ReadonlyArray<[keyof SlatePlayer['projected'], string]> = [
  ['reb', 'REB'],
  ['ast', 'AST'],
  ['stl', 'STL'],
  ['blk', 'BLK'],
  ['fg3m', '3PM'],
  ['tov', 'TO'],
];

function whole(value: NumericLike | null | undefined): string | null {
  const n = toStatNumber(value);
  return n === null ? null : Math.round(n).toString();
}

function chanceTone(probability: number): Tone | null {
  if (probability < 0.6) return 'error';
  if (probability < 0.85) return 'warning';
  return null;
}

function headline(
  points: NumericLike | null,
  minutes: NumericLike | null,
  perGame: boolean
): string | null {
  const pts = whole(points);
  const min = whole(minutes);
  const parts = [pts && `${pts} pts`, min && `${min} min`].filter((p): p is string => !!p);
  if (parts.length === 0) return null;
  return `${parts.join(', ')}${perGame ? ' a game' : ''} if he plays`;
}

// the current report, which can be newer than the projection.
function injuryWords(player: SlatePlayer): InjuryWords | null {
  const status = player.injury_status ?? null;
  const changed = player.injury_changed_after_run === true;
  if (status === null && !changed) return null;

  const stale = changed
    ? ' It changed after these numbers were published, so they do not reflect it.'
    : '';
  if (status === null) {
    return {
      label: 'Cleared',
      tone: 'success',
      description: `Off the injury report since these numbers were published.${stale}`,
    };
  }

  const known = INJURY_WORDS[status];
  const label = known?.label ?? player.injury_status_raw ?? 'On the report';
  const detail = player.injury_detail ? ` (${player.injury_detail})` : '';
  const asOf = formatTimestamp(player.injury_as_of ?? null);
  return {
    label: changed ? `${label} (new)` : label,
    tone: known?.tone ?? 'warning',
    description: `Injury report: ${label}${detail}${asOf ? `, as of ${asOf}` : ''}.${stale}`,
  };
}

function teammateClause(evidence: WatchlistEvidence): string | null {
  if (!evidence.teammate_out) return null;
  const chance = toStatNumber(evidence.teammate_out_prob_active);
  return chance !== null && chance >= 0.1
    ? `${evidence.teammate_out} is unlikely to play`
    : `${evidence.teammate_out} is out`;
}

// one sentence, from the strongest reason that fired.
function reasonSentence(
  reasons: WatchlistReason[],
  evidence: WatchlistEvidence,
  usualMinutes: NumericLike | null,
  usualPoints: NumericLike | null
): string | null {
  const teammate = teammateClause(evidence);

  if (reasons.includes('ROLE_INCREASE')) {
    const usual = whole(usualMinutes);
    const base = usual ? `Up from his usual ${usual} min` : 'More minutes than usual';
    return teammate ? `${base}: ${teammate}.` : `${base}.`;
  }
  if (reasons.includes('TEAMMATE_ABSENCE')) {
    return teammate
      ? `${teammate}, which opens up minutes.`
      : 'A regular starter is unlikely to play, which opens up minutes.';
  }
  if (reasons.includes('RETURNING_FROM_ABSENCE')) {
    const days = whole(evidence.days_since_played);
    return days ? `Back after ${days} days without a game.` : 'Back after a week or more out.';
  }
  if (reasons.includes('SHOT_VOLUME_SURGE')) {
    return evidence.fga_projected !== undefined && evidence.fga_usual !== undefined
      ? `Projected for ${formatStat(evidence.fga_projected)} shots, up from his usual ${formatStat(evidence.fga_usual)}.`
      : 'Projected to take more shots than usual.';
  }
  if (reasons.includes('HOT_STREAK')) {
    return evidence.pts_recent !== undefined && toStatNumber(usualPoints) !== null
      ? `Averaging ${formatStat(evidence.pts_recent)} points over his last 5 games, up from his usual ${formatStat(usualPoints)}.`
      : 'Scoring above his usual over his last 5 games.';
  }
  return null;
}

function categoryLine(player: SlatePlayer): string | null {
  const parts = CATEGORY_LABELS.filter(([key]) => toStatNumber(player.projected?.[key]) !== null).map(
    ([key, label]) => `${formatStat(player.projected[key])} ${label}`
  );
  return parts.length === 0 ? null : parts.join(ROW_SEPARATOR);
}

function tonightMatchup(player: SlatePlayer, game: SlateGame): string | null {
  const team = player.team_abbr;
  if (!team) return null;
  if (team === game.home_team_abbr && game.away_team_abbr) return `${team} vs ${game.away_team_abbr}`;
  if (team === game.away_team_abbr && game.home_team_abbr) return `${team} @ ${game.home_team_abbr}`;
  return team;
}

function weekMatchup(player: WatchlistPlayer): string | null {
  const team = player.team_abbr;
  if (player.games_count > 1) {
    return `${team ? `${team}, ` : ''}${player.games_count} games`;
  }
  if (!team) return player.opponent_team_abbr ? `vs ${player.opponent_team_abbr}` : null;
  return player.opponent_team_abbr ? `${team} vs ${player.opponent_team_abbr}` : team;
}

function meanPoints(games: WatchlistGame[]): number | null {
  const known = games.map((g) => toStatNumber(g.proj_pts)).filter((n): n is number => n !== null);
  if (known.length === 0) return null;
  return known.reduce((sum, n) => sum + n, 0) / known.length;
}

function sitsSentence(points: number | null, probability: number | null, perGame: boolean): string | null {
  if (points === null || probability === null) return null;
  return `${formatStat(points)} points${perGame ? ' a game' : ''} averaged over the chance he sits.`;
}

function assemble(
  base: Omit<ProjectionRowModel, 'sentence' | 'chance' | 'chanceTone'>,
  probActive: NumericLike | null
): ProjectionRowModel {
  const probability = toStatNumber(probActive);
  const chance = probability === null ? null : `${Math.round(probability * 100)}% to play`;
  const sentence = [base.name, base.matchup, base.line, chance]
    .filter((part): part is string => !!part)
    .join(ROW_SEPARATOR);
  return {
    ...base,
    chance,
    chanceTone: probability === null ? null : chanceTone(probability),
    sentence,
  };
}

export function toProjectionRow(source: ProjectionSource): ProjectionRowModel {
  if (source.kind === 'tonight') {
    const { player, game } = source;
    const injury = injuryWords(player);
    const usualMinutes = player.vs_usual?.minutes.usual ?? player.usual_min;
    const usualPoints = player.vs_usual?.points.usual ?? player.usual_pts;
    return assemble(
      {
        id: player.nba_player_id,
        name: player.name,
        nameIsPlaceholder: player.name_is_placeholder,
        matchup: tonightMatchup(player, game),
        line: headline(player.proj_pts_cond, player.proj_min_p50, false),
        note: reasonSentence(player.reasons, player.evidence, usualMinutes, usualPoints),
        preseason: game.preseason,
        injury,
        categories: categoryLine(player),
        sitsSentence: sitsSentence(toStatNumber(player.proj_pts), toStatNumber(player.prob_active), false),
        evidence: [...slateEvidenceLines(player), ...(injury ? [injury.description] : [])],
        games: [],
      },
      player.prob_active
    );
  }

  const { player } = source;
  const multi = player.games_count > 1;
  const minutesDelta = toStatNumber(player.minutes.delta);
  const minutesLine =
    minutesDelta === null
      ? []
      : [
          `Minutes: ${formatStat(player.minutes.projected)}${multi ? ' a game' : ''} projected, usually ${formatStat(player.minutes.usual)}`,
        ];
  return assemble(
    {
      id: player.nba_player_id,
      name: player.name,
      nameIsPlaceholder: player.name_is_placeholder,
      matchup: weekMatchup(player),
      line: headline(player.points.projected, player.minutes.projected, multi),
      note: reasonSentence(player.reasons, player.evidence, player.minutes.usual, player.points.usual),
      preseason: player.preseason || player.games.some((g) => g.preseason),
      injury: null,
      categories: null,
      sitsSentence: sitsSentence(meanPoints(player.games), toStatNumber(player.prob_active), multi),
      evidence: [...minutesLine, ...reasonEvidenceLines(player.evidence, player.points.usual)],
      games: multi ? player.games : [],
    },
    player.prob_active
  );
}
