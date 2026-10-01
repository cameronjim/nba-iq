import { IMPACT_CATEGORIES, type SlateResponse, type SlatePlayer } from '../services/slate.js';
import {
  IMPACT_PERCENTILE_FLOOR,
  type WatchlistResponse,
  type WatchlistPlayer,
  type WatchlistEvidence,
} from '../services/watchlist.js';
import type {
  PlayerPredictionsResponse,
  UpcomingGamePrediction,
  PredictionStatLine,
} from '../services/playerPredictions.js';
import type { PlayerAnalytics, LeagueDistribution } from '../services/analytics.js';
import type { PlayerWithScore } from '../services/fantasyScore.js';
import type { ResolvedPlayer } from './resolvePlayer.js';

export function fmt(v: number | null | undefined, suffix = ''): string {
  return v === null || v === undefined ? '—' : `${v}${suffix}`;
}

export function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : `${Math.round(v * 100)}%`;
}

export function signed(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return v >= 0 ? `+${v}` : `${v}`;
}

function dayOnly(iso: string | null): string {
  if (!iso) return '—';
  const match = /^(\d{4}-\d{2}-\d{2})/.exec(iso);
  return match ? match[1] : iso;
}

function mmdd(date: string | null): string {
  if (!date) return '??-??';
  const match = /^\d{4}-(\d{2}-\d{2})/.exec(date);
  return match ? match[1] : date;
}


function slatePlayerLine(p: SlatePlayer): string {
  const usual = `vs usual: pts ${signed(p.pts_vs_usual)}, min ${signed(p.min_vs_usual)}`;
  const injury =
    p.injury_status === null
      ? ''
      : ` | ${p.injury_status}: ${p.injury_detail ?? 'no detail'}${p.injury_changed_after_run ? ', changed after run' : ''}`;
  const spotlight = p.slate_spotlight ? ' *slate spotlight*' : '';
  const reasons = p.reasons.length > 0 ? ` | why: ${p.reasons.join(', ')}` : '';
  return (
    `${p.name} (${p.team_abbr ?? '—'}) impact ${fmt(p.impact)} | ${fmt(p.proj_pts)} pts, ${fmt(p.proj_min_p50)} min proj` +
    ` | ${pct(p.prob_active)} active | ${usual}${reasons}${injury}${spotlight}`
  );
}

export function formatSlate(slate: SlateResponse, playersPerGame: number): string {
  const lines: string[] = [];
  const runLabel = slate.run
    ? `model ${slate.run.model_version}, predicted ${slate.run.predicted_at ?? '—'}`
    : 'no completed prediction run';
  lines.push(`Slate for ${slate.date} (ET) — ${runLabel}`);

  if (slate.games.length === 0) {
    lines.push(`No NBA games scheduled for ${slate.date}.`);
    return lines.join('\n');
  }

  if (!slate.run) {
    lines.push('No completed prediction run; games listed without projections.');
  } else {
    lines.push(
      `Pool: ${slate.pool.sample_size} players (${slate.pool.definition}). Baseline: ${slate.baseline.definition}.`
    );
    lines.push(
      `Impact = z-score sum across ${IMPACT_CATEGORIES.length} categories (${IMPACT_CATEGORIES.join('/')}) vs ${slate.pool.label.toLowerCase()}.`
    );
  }
  lines.push('');

  for (const game of slate.games) {
    lines.push(
      `${game.away_team_abbr ?? '???'} @ ${game.home_team_abbr ?? '???'} (${game.game_status ?? 'scheduled'})`
    );
    if (game.players.length === 0) {
      lines.push('  (no projections)');
      continue;
    }
    const shown = game.players.slice(0, Math.min(playersPerGame, game.players.length));
    shown.forEach((p, i) => {
      lines.push(`  ${i + 1}. ${slatePlayerLine(p)}`);
    });
  }

  return lines.join('\n');
}


function watchlistEvidenceLine(e: WatchlistEvidence): string | null {
  const parts: string[] = [];
  if (e.fga_delta !== undefined) {
    parts.push(`fga ${fmt(e.fga_usual)} usual -> ${fmt(e.fga_projected)} proj (${signed(e.fga_delta)})`);
  }
  if (e.days_since_played !== undefined) {
    parts.push(`last played ${e.last_played_date ?? '—'} (${e.days_since_played} days ago)`);
  }
  if (e.pts_recent_delta !== undefined) {
    parts.push(`last-5 pts ${fmt(e.pts_recent)} vs usual (${signed(e.pts_recent_delta)}, sd ${fmt(e.pts_sd)})`);
  }
  if (e.teammate_out !== undefined) {
    parts.push(
      `teammate out: ${e.teammate_out} (${fmt(e.teammate_out_minutes)} usual min, ${pct(e.teammate_out_prob_active)} active)`
    );
  }
  return parts.length > 0 ? parts.join('; ') : null;
}

function watchlistPlayerBlock(p: WatchlistPlayer, index: number): string[] {
  const lines: string[] = [];
  const reasons = p.reasons.length > 0 ? p.reasons.join(', ') : 'none';
  lines.push(
    `${index + 1}. ${p.name} (${p.team_abbr ?? '—'}, ${p.position ?? '—'}) score ${fmt(p.score)} (${fmt(p.score_per_game)}/game, ${p.games_count} games) | reasons: ${reasons}`
  );
  lines.push(
    `   min ${fmt(p.minutes.usual)} usual -> ${fmt(p.minutes.projected)} proj (${signed(p.minutes.delta)}) | ` +
      `pts ${fmt(p.points.usual)} usual -> ${fmt(p.points.projected)} proj (${signed(p.points.delta)}) | ` +
      `${pct(p.prob_active)} active | impact pctile ${fmt(p.impact_percentile)}`
  );
  const evidence = watchlistEvidenceLine(p.evidence);
  if (evidence) lines.push(`   evidence: ${evidence}`);
  if (p.games_count > 1) {
    const shown = p.games.slice(0, 5);
    const parts = shown.map(
      (g) => `${mmdd(g.game_date)} vs ${g.opponent_team_abbr ?? '???'} (proj ${fmt(g.proj_pts)} pts)`
    );
    const extra = p.games.length > 5 ? ` (+${p.games.length - 5} more)` : '';
    lines.push(`   games: ${parts.join(', ')}${extra}`);
  }
  return lines;
}

export function formatWatchlist(w: WatchlistResponse): string {
  const lines: string[] = [];
  const runLabel = w.run
    ? `model ${w.run.model_version}, predicted ${w.run.predicted_at ?? '—'}`
    : 'no completed prediction run';
  lines.push(`Watchlist ${w.window.from} to ${w.window.to} (${w.window.days} days) — ${runLabel}`);
  lines.push(
    `Pool: ${w.pool.sample_size} player-nights. Position filter: ${w.position ?? 'none'} ` +
      `(coverage: ${w.position_coverage.known} known / ${w.position_coverage.unknown} unknown positions).`
  );
  lines.push(
    `Score = upside vs his own recent form x slate relevance (impact percentile above ${IMPACT_PERCENTILE_FLOOR}).`
  );
  lines.push('');

  if (w.players.length === 0) {
    lines.push('No watchlist candidates for this window.');
    return lines.join('\n');
  }

  w.players.forEach((p, i) => {
    lines.push(...watchlistPlayerBlock(p, i));
  });

  return lines.join('\n');
}


const PROJECTION_STAT_ORDER = ['pts', 'reb', 'ast', 'stl', 'blk', 'tov', 'fg3m', 'fgm', 'fga', 'ftm', 'fta'] as const;

function isEmptyStatLine(line: PredictionStatLine): boolean {
  return (
    line.expected === null &&
    line.p10 === null &&
    line.p50 === null &&
    line.p90 === null &&
    line.unconditional === null
  );
}

function projectionGameBlock(game: UpcomingGamePrediction): string[] {
  const oppSuffix =
    game.is_home === null
      ? 'opponent unknown'
      : `${game.is_home ? 'vs' : '@'} ${game.opponent_abbr ?? '???'} (${game.is_home ? 'home' : 'away'})`;
  const minutesLine = game.stats.minutes;
  const minutesValue = minutesLine ? (minutesLine.p50 ?? minutesLine.expected) : null;

  const lines = [`${game.game_date} ${oppSuffix} | ${pct(game.prob_active)} active | min ${fmt(minutesValue)} p50`];

  const statParts: string[] = [];
  for (const stat of PROJECTION_STAT_ORDER) {
    const line = game.stats[stat];
    if (!line || isEmptyStatLine(line)) continue;
    if (stat === 'pts') {
      const range =
        line.p10 !== null && line.p90 !== null
          ? ` (p10 ${fmt(line.p10)} / p90 ${fmt(line.p90)}${line.unconditional !== null ? `, uncond ${fmt(line.unconditional)}` : ''})`
          : '';
      statParts.push(`pts ${fmt(line.expected)} exp${range}`);
    } else {
      const value = line.expected ?? line.p50;
      statParts.push(`${stat} ${fmt(value)}`);
    }
  }
  if (statParts.length > 0) lines.push(`  ${statParts.join(' | ')}`);
  return lines;
}

export function formatProjections(
  p: ResolvedPlayer,
  payload: Omit<PlayerPredictionsResponse, 'player_id' | 'nba_player_id'>
): string {
  const header = `${p.name} (${p.team ?? '—'}, ${p.position ?? '—'}) — upcoming projections`;
  if (!payload.run || payload.games.length === 0) {
    return `${header}\nNo completed prediction run covers upcoming games for ${p.name}.`;
  }

  const metaParts: string[] = [];
  if (payload.run.horizon) metaParts.push(`horizon ${payload.run.horizon}`);
  if (payload.run.predicted_at) metaParts.push(`predicted ${payload.run.predicted_at}`);
  const meta = metaParts.length > 0 ? ` (${metaParts.join(', ')})` : '';

  const lines = [
    `${header}, model ${payload.run.model_version}${meta}`,
    'Conditional stats are "if he plays"; unconditional averages over availability.',
    '',
  ];

  for (const game of payload.games) {
    lines.push(...projectionGameBlock(game));
  }

  return lines.join('\n');
}


export function formatAnalytics(a: PlayerAnalytics): string {
  const lines: string[] = [];
  const poolLabel = a.pool.label.toLowerCase();
  lines.push(
    `${a.player.name} (${a.player.team ?? '—'}, ${a.player.position ?? '—'}) — season analytics vs ${poolLabel} (${a.pool.definition}, n=${a.pool.sample_size})`
  );
  const injury =
    a.player.injury_status === null
      ? 'none'
      : `${a.player.injury_status} (${a.player.injury_detail ?? 'no detail'})`;
  lines.push(`Injury: ${injury} | Game logs through ${dayOnly(a.as_of.logs)}`);
  lines.push('');

  lines.push('Per-game percentiles (100 = best; tov inverted):');
  lines.push(`  ${a.percentiles.map((sp) => `${sp.stat} ${fmt(sp.value)} (p${fmt(sp.percentile)})`).join(' | ')}`);
  lines.push('');

  lines.push('Last 10 vs season (per game):');
  const deviations = a.trends.last10_vs_season.filter((c) => c.z !== null && Math.abs(c.z) >= 0.5);
  if (deviations.length === 0) {
    lines.push('  no notable deviations');
  } else {
    lines.push(
      `  ${deviations.map((c) => `${c.stat} ${fmt(c.last10)} vs ${fmt(c.season)} (${signed(c.delta)}, z ${fmt(c.z)})`).join(' | ')}`
    );
  }
  lines.push('');

  lines.push(a.prediction?.summary ? `Next game: ${a.prediction.summary}` : 'Next game: no prediction available.');
  lines.push('');

  lines.push('Last 10 games:');
  const recentGames = a.trends.games.slice(-10);
  for (const g of recentGames) {
    const homeAway = g.is_home === true ? 'H' : g.is_home === false ? 'A' : '?';
    lines.push(
      `  ${mmdd(g.game_date)} vs ${g.opponent_team_abbr ?? '???'} (${homeAway}): ${fmt(g.minutes)} min, ${fmt(g.pts)} pts, ` +
        `${fmt(g.reb)} reb, ${fmt(g.ast)} ast, ${fmt(g.stl)} stl, ${fmt(g.blk)} blk, ${fmt(g.tov)} tov, ${fmt(g.fg3m)} fg3m`
    );
  }

  return lines.join('\n');
}


export interface PlayersListFilters {
  query?: string;
  team?: string;
  position?: string;
}

export function formatPlayersList(players: PlayerWithScore[], filters: PlayersListFilters): string {
  const filterParts: string[] = [];
  if (filters.query) filterParts.push(`query "${filters.query}"`);
  if (filters.team) filterParts.push(`team ${filters.team}`);
  if (filters.position) filterParts.push(`position ${filters.position}`);
  const filterSuffix = filterParts.length > 0 ? ` — filters: ${filterParts.join(', ')}` : '';

  const header = `Top players (fantasy score, NBA Standard: pts*1 + reb*1.2 + ast*1.5 + stl*3 + blk*3 - tov*1)${filterSuffix}`;

  if (players.length === 0) return `${header}\nNo players match.`;

  const lines = [header, 'Id is the app player id accepted by get_player_projections and get_player_analytics.'];
  for (const p of players) {
    const rank = p.fantasy_rank ?? 'unranked';
    const injury =
      p.injury_status === null ? '' : ` | ${p.injury_status}: ${p.injury_detail ?? 'no detail'}`;
    lines.push(
      `${rank}. (id ${p.id}) ${p.name} — ${p.team ?? '—'}, ${p.position ?? '—'} | ${fmt(p.fantasy_score)} fpts | ` +
        `${fmt(p.points_per_game)} pts / ${fmt(p.rebounds_per_game)} reb / ${fmt(p.assists_per_game)} ast | ` +
        `${fmt(p.minutes_per_game)} mpg, ${p.games_played} gp${injury}`
    );
  }

  return lines.join('\n');
}


export function formatStatLeaders(d: LeagueDistribution, limit: number): string {
  const lines = [`${d.stat} leaders — ${d.pool.label} (${d.pool.definition}, n=${d.pool.sample_size})`];
  const tovNote = d.stat === 'tov' ? ' Note: for tov, lower is better; percentiles already account for this.' : '';
  lines.push(`Pool mean ${fmt(d.mean)}, sd ${fmt(d.stddev)} (per game).${tovNote}`);

  const shown = d.players.slice(0, limit);
  shown.forEach((p, i) => {
    lines.push(`${i + 1}. ${p.name} (id ${p.id}) — ${fmt(p.value)} (p${fmt(p.percentile)})`);
  });

  return lines.join('\n');
}
