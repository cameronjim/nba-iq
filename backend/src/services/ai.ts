import { query } from '../db.js';
import { activeProviderKind, getNarrator } from './aiProvider.js';
import {
  CATEGORY_LABELS,
  WAIVER_BAND_WIDTH,
  rankTradeTargets,
  rankingPools,
  rankWaiverCandidates,
  type RankedCandidate,
  type RankingPlayer,
} from './candidateRanking.js';
import { etIsoDate } from './dates.js';
import { getRankedPlayers, type PlayerWithScore } from './fantasyScore.js';
import type { BettingGame } from './odds.js';
import { COMPLETE_RUN_STATUS, PRODUCTION_CHANNEL } from './slate.js';
import {
  DEFAULT_PROJECTION_DAYS,
  fetchWindowProjections,
  type WindowProjection,
  type WindowProjectionSet,
} from './windowProjections.js';

export function extractJSON(text: string): string {
  const fenced = text.match(/```(?:json)?\s*\n?([\s\S]*?)```/);
  if (fenced) return fenced[1].trim();
  const braceMatch = text.match(/\{[\s\S]*\}/);
  if (braceMatch) return braceMatch[0];
  return text;
}

export async function callClaude(
  systemPrompt: string,
  messages: Array<{ role: string; content: string }>,
  options: { model?: string; maxTokens?: number } = {}
): Promise<string> {
  const result = await getNarrator().narrate({
    system: systemPrompt,
    messages: messages.map((m) => ({
      role: m.role as 'user' | 'assistant',
      content: m.content,
    })),
    maxTokens: options.maxTokens,
    model: activeProviderKind() === 'anthropic' ? options.model : undefined,
  });
  return result.text;
}

type PlayerRow = Record<string, unknown>;

function formatPlayerLine(p: PlayerRow): string {
  const inj = p.injury_status ? ` [${p.injury_status}]` : '';
  return (
    `${p.name} (${p.position}/${p.team})${inj} ` +
    `PTS:${p.points_per_game} REB:${p.rebounds_per_game} AST:${p.assists_per_game} ` +
    `STL:${p.steals_per_game} BLK:${p.blocks_per_game} FG%:${p.field_goal_percentage} ` +
    `FT%:${p.free_throw_percentage} 3PM:${p.three_pointers_made} TO:${p.turnovers_per_game}`
  );
}

function finite(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

async function buildRosterAnalyticsBlock(
  roster: Array<{ nba_id: string; name: string }>
): Promise<string> {
  if (roster.length === 0) return '';

  try {
    const ids = roster.map((p) => p.nba_id);
    const result = await query(
      `WITH logs AS (
         SELECT g.nba_player_id,
                g.minutes::float AS minutes,
                g.pts::float     AS pts,
                g.reb::float     AS reb,
                g.ast::float     AS ast,
                ROW_NUMBER() OVER (
                  PARTITION BY g.nba_player_id ORDER BY g.game_date DESC
                ) AS rn
         FROM player_game_logs g
         WHERE g.season = (SELECT MAX(season) FROM player_game_logs)
           AND g.season_type = 'Regular Season'
           AND g.nba_player_id = ANY($1)
       ),
       agg AS (
         SELECT nba_player_id,
                COUNT(*)::int                        AS games,
                AVG(pts)     FILTER (WHERE rn <= 10) AS pts_l10,
                AVG(pts)                             AS pts_season,
                AVG(reb)     FILTER (WHERE rn <= 10) AS reb_l10,
                AVG(reb)                             AS reb_season,
                AVG(ast)     FILTER (WHERE rn <= 10) AS ast_l10,
                AVG(ast)                             AS ast_season,
                AVG(minutes) FILTER (WHERE rn <= 10) AS min_l10,
                AVG(minutes)                         AS min_season
         FROM logs
         GROUP BY nba_player_id
       ),
       run AS (
         SELECT id FROM prediction_runs
         WHERE status = $2
           AND channel = $3
         ORDER BY predicted_at DESC, id DESC
         LIMIT 1
       ),
       prob AS (
         SELECT DISTINCT ON (pgp.nba_player_id)
                pgp.nba_player_id,
                pgp.value::float AS prob_active
         FROM player_game_predictions pgp
         JOIN run ON run.id = pgp.prediction_run_id
         WHERE pgp.stat = 'prob_active'
           AND pgp.quantile IS NULL
           AND pgp.nba_player_id = ANY($1)
           AND pgp.game_date >= CURRENT_DATE
         ORDER BY pgp.nba_player_id, pgp.game_date ASC
       )
       SELECT a.nba_player_id,
              a.games,
              a.pts_l10::float, a.pts_season::float,
              a.reb_l10::float, a.reb_season::float,
              a.ast_l10::float, a.ast_season::float,
              a.min_l10::float, a.min_season::float,
              pr.prob_active
       FROM agg a
       LEFT JOIN prob pr ON pr.nba_player_id = a.nba_player_id`,
      [ids, COMPLETE_RUN_STATUS, PRODUCTION_CHANNEL]
    );

    const nameById = new Map(roster.map((p) => [p.nba_id, p.name]));
    const lines: string[] = [];
    let anyProb = false;

    for (const row of result.rows) {
      const name = nameById.get(String(row.nba_player_id));
      if (!name) continue;

      const delta = (recent: unknown, season: unknown): string | null => {
        const a = finite(recent);
        const b = finite(season);
        if (a === null || b === null) return null;
        const diff = a - b;
        return `${a.toFixed(1)} (${diff >= 0 ? '+' : ''}${diff.toFixed(1)})`;
      };

      const parts = [
        ['PTS', delta(row.pts_l10, row.pts_season)],
        ['REB', delta(row.reb_l10, row.reb_season)],
        ['AST', delta(row.ast_l10, row.ast_season)],
        ['MIN', delta(row.min_l10, row.min_season)],
      ].filter((p): p is [string, string] => p[1] !== null);
      if (parts.length === 0) continue;

      const prob = finite(row.prob_active);
      let line = `${name} (${row.games}g): ` + parts.map(([k, v]) => `${k} ${v}`).join(' ');
      if (prob !== null) {
        anyProb = true;
        line += `, P(active next game) ${Math.round(prob * 100)}%`;
      }
      lines.push(line);
    }

    if (lines.length === 0) return '';

    const heading = anyProb
      ? 'RECENT FORM (last 10 games, change vs season average) AND MODELLED AVAILABILITY:'
      : 'RECENT FORM (last 10 games, change vs season average):';
    return `\n${heading}\n${lines.join('\n')}\n`;
  } catch {
    return '';
  }
}

function rosterNbaIds(rows: PlayerRow[]): Array<{ nba_id: string; name: string }> {
  const out: Array<{ nba_id: string; name: string }> = [];
  for (const row of rows) {
    if (row.nba_id === null || row.nba_id === undefined || row.nba_id === '') continue;
    out.push({ nba_id: String(row.nba_id), name: String(row.name ?? '') });
  }
  return out;
}

type ProjectionLoad =
  | { kind: 'loaded'; set: WindowProjectionSet }
  | { kind: 'failed' };

async function loadWindowProjections(): Promise<ProjectionLoad> {
  try {
    return { kind: 'loaded', set: await fetchWindowProjections(etIsoDate(0), DEFAULT_PROJECTION_DAYS) };
  } catch {
    return { kind: 'failed' };
  }
}

const PROJECTION_HEADING = `PROJECTED NEXT ${DEFAULT_PROJECTION_DAYS} DAYS`;

function signed(value: number, digits: number): string {
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`;
}

function formatProjectionTotals(p: WindowProjection): string {
  const t = p.totals;
  const count = (label: string, value: number | null): string | null =>
    value === null ? null : `${label} ${value.toFixed(1)}`;
  const rate = (label: string, made: number | null, attempted: number | null, unit: string): string | null =>
    made === null || attempted === null || attempted <= 0
      ? null
      : `${label} ${((made / attempted) * 100).toFixed(1)} (${attempted.toFixed(1)} ${unit})`;

  return [
    count('PTS', t.pts),
    count('REB', t.reb),
    count('AST', t.ast),
    count('STL', t.stl),
    count('BLK', t.blk),
    count('3PM', t.fg3m),
    rate('FG%', t.fgm, t.fga, 'FGA'),
    rate('FT%', t.ftm, t.fta, 'FTA'),
    count('TO', t.tov),
  ]
    .filter((part): part is string => part !== null)
    .join(' ');
}

function formatRosterProjectionBlock(rows: PlayerRow[], load: ProjectionLoad): string {
  if (load.kind === 'failed') {
    return `\n${PROJECTION_HEADING}: not available, the projection store could not be read.\n`;
  }
  const { set } = load;
  if (!set.run) {
    return `\n${PROJECTION_HEADING}: not available, no complete production model run exists yet.\n`;
  }

  const lines: string[] = [];
  let anyProjected = false;
  for (const row of rows) {
    if (row.nba_id === null || row.nba_id === undefined || row.nba_id === '') continue;
    const name = String(row.name ?? '');
    const team = row.team === null || row.team === undefined ? null : String(row.team);
    const scheduled = team ? set.scheduled_games.get(team) ?? 0 : 0;
    const projection = set.players.get(String(row.nba_id));

    if (!projection) {
      lines.push(`${name}: not projected by the latest run; ${scheduled} scheduled games`);
      continue;
    }
    anyProjected = true;
    let line = `${name}: ${projection.games} of ${scheduled} scheduled games`;
    if (projection.mean_prob_active !== null) {
      line += `, P(play) ${Math.round(projection.mean_prob_active * 100)}%`;
    }
    line += `, ${formatProjectionTotals(projection)}`;
    if (projection.min_vs_usual !== null) line += `, MIN vs usual ${signed(projection.min_vs_usual, 1)}`;
    lines.push(line);
  }

  const span = `${set.window.from} to ${set.window.to}`;
  if (!anyProjected) {
    return (
      `\n${PROJECTION_HEADING}: not available, the latest production run (${set.run.model_version}) ` +
      `has no projections for this roster between ${span}.\n`
    );
  }

  const heading =
    `${PROJECTION_HEADING} (${span}, production model ${set.run.model_version}; ` +
    'unconditional totals, so sitting risk is priced in; MIN vs usual = projected median minutes ' +
    'minus his recent per-game average):';
  return `\n${heading}\n${lines.join('\n')}\n`;
}

export async function buildTeamContext(userId: number): Promise<string> {
  const rosterResult = await query(
    `SELECT p.nba_id, p.name, p.team, p.position,
            p.points_per_game, p.rebounds_per_game, p.assists_per_game, p.steals_per_game, p.blocks_per_game,
            p.field_goal_percentage, p.free_throw_percentage, p.three_pointers_made,
            p.turnovers_per_game, p.injury_status
     FROM my_roster mr
     JOIN players p ON mr.player_id = p.id
     WHERE mr.user_id = $1
     ORDER BY p.points_per_game DESC`,
    [userId]
  );

  if (rosterResult.rows.length === 0) return 'No players on roster.';

  const players = rosterResult.rows;
  const avg = (key: string): string => {
    const vals = players.map((p: PlayerRow) => Number(p[key]) || 0);
    return (vals.reduce((a: number, b: number) => a + b, 0) / vals.length).toFixed(1);
  };

  let context =
    `ROSTER AVERAGES: PTS:${avg('points_per_game')} REB:${avg('rebounds_per_game')} ` +
    `AST:${avg('assists_per_game')} STL:${avg('steals_per_game')} BLK:${avg('blocks_per_game')} ` +
    `FG%:${avg('field_goal_percentage')} FT%:${avg('free_throw_percentage')} ` +
    `3PM:${avg('three_pointers_made')} TO:${avg('turnovers_per_game')}\n\n`;
  context += `MY ROSTER (${players.length}):\n`;
  for (const p of players) context += formatPlayerLine(p) + '\n';
  const ids = rosterNbaIds(players);
  context += await buildRosterAnalyticsBlock(ids);
  if (ids.length > 0) context += formatRosterProjectionBlock(players, await loadWindowProjections());

  return context;
}

function toRankingPlayer(row: PlayerRow): RankingPlayer {
  const n = (key: string): number => Number(row[key]) || 0;
  const optionalText = (key: string): string | null =>
    row[key] === null || row[key] === undefined || row[key] === '' ? null : String(row[key]);
  return {
    id: Number(row.id),
    nba_id: optionalText('nba_id'),
    name: String(row.name ?? ''),
    team: optionalText('team'),
    position: optionalText('position'),
    points_per_game: n('points_per_game'),
    rebounds_per_game: n('rebounds_per_game'),
    assists_per_game: n('assists_per_game'),
    steals_per_game: n('steals_per_game'),
    blocks_per_game: n('blocks_per_game'),
    three_pointers_made: n('three_pointers_made'),
    turnovers_per_game: n('turnovers_per_game'),
    field_goal_percentage: n('field_goal_percentage'),
    free_throw_percentage: n('free_throw_percentage'),
  };
}

function formatRankedLine(rank: number, c: RankedCandidate, player: PlayerWithScore | undefined): string {
  let basis: string;
  if (c.basis === 'projection') {
    basis = `projection, ${c.projected_games}g`;
    if (c.mean_prob_play !== null) basis += `, P(play) ${Math.round(c.mean_prob_play * 100)}%`;
  } else {
    basis = c.projected_games === null
      ? 'season_average, per game (schedule unknown)'
      : `season_average, ${c.projected_games} scheduled g`;
  }
  const drivers = c.drivers
    .map((d) => `${CATEGORY_LABELS[d.category]} ${signed(d.value, 3)}`)
    .join(', ');
  const stats = player
    ? formatPlayerLine(player as unknown as PlayerRow)
    : `${c.name} (${c.position}/${c.team})`;
  return `${rank}. [score ${c.score.toFixed(3)} | ${basis}] ${stats} | drivers: ${drivers}`;
}

export const RANKED_LIST_INSTRUCTIONS =
  'RANKING METHOD: both candidate lists below are pre-ranked numerically, best first. ' +
  `score = expected 9-category matchup wins the player adds to MY ROSTER over the next ${DEFAULT_PROJECTION_DAYS} days, ` +
  'computed from z-scores of his projected category totals against a typical opponent built from the rostered tier, ' +
  'so it already rewards filling my weak categories and discounts categories I already win. ' +
  "basis projection = the production model's unconditional projections (sitting risk priced in); " +
  'basis season_average = season per-game averages times scheduled games, with FG% and FT% treated as neutral. ' +
  'drivers = the three categories contributing most to the score. ' +
  'Your job is to explain and sanity-check this ranking (injury news, role changes, schedule, fit with my preferences), ' +
  'not to re-rank from scratch: favor the top of each list, and if you pass over a higher-ranked player, say why.';

export async function buildWaiverContext(userId: number, leagueSize?: number): Promise<string> {
  const ranked = await getRankedPlayers();

  const rosterResult = await query(
    `SELECT p.id, p.nba_id, p.name, p.team, p.position,
            p.points_per_game, p.rebounds_per_game, p.assists_per_game, p.steals_per_game, p.blocks_per_game,
            p.field_goal_percentage, p.free_throw_percentage, p.three_pointers_made,
            p.turnovers_per_game, p.injury_status
     FROM my_roster mr
     JOIN players p ON mr.player_id = p.id
     WHERE mr.user_id = $1
     ORDER BY p.points_per_game DESC`,
    [userId]
  );

  if (rosterResult.rows.length === 0) return 'No players on roster.';

  const rosterIds = new Set<number>(rosterResult.rows.map((r: { id: number }) => r.id));
  const players = rosterResult.rows;

  const pools = rankingPools(ranked, rosterIds, leagueSize);
  const teams = pools.teams;
  const rosteredCutoff = pools.rostered_cutoff;
  const tradePool = pools.trade_pool;
  const waiverPool = pools.waiver_pool;

  const avg = (key: string): string => {
    const vals = players.map((p: PlayerRow) => Number(p[key]) || 0);
    return (vals.reduce((a: number, b: number) => a + b, 0) / vals.length).toFixed(1);
  };

  let context =
    `LEAGUE: ${teams} teams (~${rosteredCutoff} players rostered)\n` +
    `ROSTER AVERAGES: PTS:${avg('points_per_game')} REB:${avg('rebounds_per_game')} ` +
    `AST:${avg('assists_per_game')} STL:${avg('steals_per_game')} BLK:${avg('blocks_per_game')} ` +
    `FG%:${avg('field_goal_percentage')} FT%:${avg('free_throw_percentage')} ` +
    `3PM:${avg('three_pointers_made')} TO:${avg('turnovers_per_game')}\n\n`;
  context += `MY ROSTER (${players.length}):\n`;
  for (const p of players) context += formatPlayerLine(p) + '\n';
  const ids = rosterNbaIds(players);
  context += await buildRosterAnalyticsBlock(ids);

  const load = await loadWindowProjections();
  if (ids.length > 0) context += formatRosterProjectionBlock(players, load);

  const roster = players.map(toRankingPlayer);
  const rankingOptions = {
    scheduledGames: load.kind === 'loaded' ? load.set.scheduled_games : new Map<string, number>(),
    population: [...roster, ...tradePool, ...waiverPool],
    rosteredPool: tradePool,
  };
  const projections = load.kind === 'loaded' ? load.set.players : new Map<string, WindowProjection>();
  const waiverPickups = rankWaiverCandidates(roster, waiverPool, projections, rankingOptions);
  const tradeTargets = rankTradeTargets(roster, tradePool, projections, rankingOptions);
  const byId = new Map<number, PlayerWithScore>(ranked.map((p) => [p.id, p]));

  context += `\n${RANKED_LIST_INSTRUCTIONS}\n`;

  context += `\nWAIVER CANDIDATES (top ${waiverPickups.length} by score from fantasy rank ${rosteredCutoff + 1} to ${rosteredCutoff + WAIVER_BAND_WIDTH}, presumed unrostered in a ${teams}-team league):\n`;
  waiverPickups.forEach((c, i) => {
    context += formatRankedLine(i + 1, c, byId.get(c.id)) + '\n';
  });

  context += `\nTRADE TARGETS (top ${tradeTargets.length} by score from the top ${rosteredCutoff}, presumed rostered by other managers):\n`;
  tradeTargets.forEach((c, i) => {
    context += formatRankedLine(i + 1, c, byId.get(c.id)) + '\n';
  });

  return context;
}

const pct = (p: number): string => `${(p * 100).toFixed(1)}%`;

const priceText = (price: number | null, implied: number | null): string =>
  price == null || implied == null ? 'n/a' : `${price}, implied ${pct(implied)}`;

function formatMarketLines(game: BettingGame): string[] {
  const lines: string[] = [];
  const s = game.markets.spread;
  if (s) {
    lines.push(
      `  SPREAD: home ${s.home_line > 0 ? '+' : ''}${s.home_line} (${priceText(s.home_price, s.home_implied)}) / ` +
      `away ${s.away_line > 0 ? '+' : ''}${s.away_line} (${priceText(s.away_price, s.away_implied)})`
    );
  }
  const t = game.markets.total;
  if (t) {
    lines.push(
      `  TOTAL: ${t.line}: over (${priceText(t.over_price, t.over_implied)}) / ` +
      `under (${priceText(t.under_price, t.under_implied)})`
    );
  }
  const m = game.markets.moneyline;
  if (m) {
    lines.push(
      `  MONEYLINE: home ${m.home > 0 ? '+' : ''}${m.home} (implied ${pct(m.home_implied)}) / ` +
      `away ${m.away > 0 ? '+' : ''}${m.away} (implied ${pct(m.away_implied)})`
    );
  }
  return lines;
}

interface FinalGameRow {
  home_team: string;
  away_team: string;
  home_score: number;
  away_score: number;
  game_date: string;
}

interface TeamForm {
  record: string;
  avgScored: string;
  avgAllowed: string;
}

function recentForm(finals: FinalGameRow[], team: string, n = 10): TeamForm | null {
  const games = finals
    .filter((g) => g.home_team === team || g.away_team === team)
    .slice(0, n);
  if (games.length === 0) return null;

  let wins = 0;
  let scored = 0;
  let allowed = 0;
  for (const g of games) {
    const isHome = g.home_team === team;
    const us = isHome ? g.home_score : g.away_score;
    const them = isHome ? g.away_score : g.home_score;
    if (us > them) wins += 1;
    scored += us;
    allowed += them;
  }
  return {
    record: `${wins}-${games.length - wins}`,
    avgScored: (scored / games.length).toFixed(1),
    avgAllowed: (allowed / games.length).toFixed(1),
  };
}

function headToHead(finals: FinalGameRow[], teamA: string, teamB: string, maxGames = 5): string[] {
  const meetings = finals.filter(
    (g) =>
      (g.home_team === teamA && g.away_team === teamB) ||
      (g.home_team === teamB && g.away_team === teamA)
  );
  if (meetings.length === 0) return [`  Head-to-head: no prior meetings in our database.`];

  const aWins = meetings.filter((g) => {
    const aIsHome = g.home_team === teamA;
    return aIsHome ? g.home_score > g.away_score : g.away_score > g.home_score;
  }).length;

  const lines = [
    `  Head-to-head (${meetings.length} meetings on record): ${teamA} ${aWins} wins, ${teamB} ${meetings.length - aWins} wins`,
  ];
  for (const g of meetings.slice(0, maxGames)) {
    lines.push(`    ${g.game_date}: ${g.away_team} ${g.away_score} at ${g.home_team} ${g.home_score}`);
  }
  return lines;
}

export async function buildBettingContext(games: BettingGame[]): Promise<string> {
  const teamNames = [...new Set(games.flatMap((g) => [g.home_team, g.away_team]))];

  const teamsResult = await query(
    `SELECT name, abbreviation, wins, losses,
            offensive_rating, defensive_rating, net_rating
     FROM teams
     WHERE name = ANY($1)`,
    [teamNames]
  );
  const teamByName = new Map<string, Record<string, unknown>>(
    teamsResult.rows.map((t: Record<string, unknown>) => [t.name as string, t])
  );

  const finalsResult = await query(
    `SELECT home_team, away_team, home_score, away_score,
            TO_CHAR(game_date, 'YYYY-MM-DD') AS game_date
     FROM games
     WHERE status = 'Final'
       AND home_score IS NOT NULL AND away_score IS NOT NULL
       AND (home_team = ANY($1) OR away_team = ANY($1))
     ORDER BY game_date DESC
     LIMIT 400`,
    [teamNames]
  );
  const finals = finalsResult.rows as FinalGameRow[];

  const injuriesResult = await query(
    `SELECT p.name, p.team, p.injury_status, p.injury_detail, p.points_per_game
     FROM players p
     JOIN teams t ON t.abbreviation = p.team
     WHERE p.injury_status IS NOT NULL
       AND p.minutes_per_game >= 15
       AND t.name = ANY($1)
     ORDER BY p.points_per_game DESC`,
    [teamNames]
  );
  const injuriesByAbbrev = new Map<string, string[]>();
  for (const row of injuriesResult.rows) {
    const list = injuriesByAbbrev.get(row.team) ?? [];
    list.push(`${row.name} (${row.points_per_game} ppg), ${row.injury_status}${row.injury_detail ? `: ${row.injury_detail}` : ''}`);
    injuriesByAbbrev.set(row.team, list);
  }

  const teamLine = (name: string): string => {
    const t = teamByName.get(name);
    const base = t
      ? `  ${name}: ${t.wins}-${t.losses}, ORtg ${t.offensive_rating}, DRtg ${t.defensive_rating}, Net ${t.net_rating}`
      : `  ${name}: no team stats available`;
    const form = recentForm(finals, name);
    if (!form) return base;
    return `${base}\n  ${name} last 10: ${form.record}, avg ${form.avgScored} scored, ${form.avgAllowed} allowed`;
  };

  const injuryLines = (name: string, abbrev: string): string[] => {
    const list = injuriesByAbbrev.get(abbrev);
    if (!list || list.length === 0) return [];
    return [`  ${name} injuries:`, ...list.map((l) => `    - ${l}`)];
  };

  const blocks = games.map((g) => {
    const lines = [
      `GAME ${g.espn_event_id}: ${g.away_team} @ ${g.home_team} (${g.game_date}, ${g.tipoff})`,
      ...formatMarketLines(g),
      teamLine(g.home_team),
      teamLine(g.away_team),
      ...headToHead(finals, g.home_team, g.away_team),
      ...injuryLines(g.home_team, g.home_abbrev),
      ...injuryLines(g.away_team, g.away_abbrev),
    ];
    return lines.join('\n');
  });

  return `UPCOMING GAMES WITH POSTED ODDS (${games.length}):\n\n${blocks.join('\n\n')}`;
}
