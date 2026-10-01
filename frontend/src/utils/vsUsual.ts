import { formatStat, toStatNumber } from './stats';
import type {
  NumericLike,
  SlatePlayer,
  VsUsual,
  VsUsualCategory,
  WatchlistEvidence,
} from '../types';

// short labels matching the Projections category line.
const VS_USUAL_LABELS: Record<VsUsualCategory | 'minutes' | 'pts', string> = {
  minutes: 'MIN',
  pts: 'PTS',
  reb: 'REB',
  ast: 'AST',
  stl: 'STL',
  blk: 'BLK',
  fg3m: '3PM',
};

export function signedStat(value: NumericLike | null | undefined): string {
  const n = toStatNumber(value);
  if (n === null) return formatStat(null);
  return `${n > 0 ? '+' : ''}${formatStat(n)}`;
}

// the reason-specific evidence lines, shared by both projection scopes.
export function reasonEvidenceLines(
  evidence: WatchlistEvidence,
  usualPoints: NumericLike | null
): string[] {
  const lines: string[] = [];
  if (evidence.fga_delta !== undefined) {
    lines.push(
      `Shots: ${formatStat(evidence.fga_projected)} projected, usually ${formatStat(evidence.fga_usual)} (+${formatStat(evidence.fga_delta)})`
    );
  }
  if (evidence.days_since_played !== undefined) {
    const last = evidence.last_played_date ? `, last played ${evidence.last_played_date}` : '';
    lines.push(`Absence: ${formatStat(evidence.days_since_played, 0)} days without a game${last}`);
  }
  if (evidence.pts_recent_delta !== undefined) {
    lines.push(
      `Recent form: ${formatStat(evidence.pts_recent)} points over his last 5, usually ${formatStat(usualPoints)}`
    );
  }
  if (evidence.teammate_out !== undefined) {
    const chance =
      evidence.teammate_out_prob_active === undefined
        ? ''
        : `, ${Math.round((toStatNumber(evidence.teammate_out_prob_active) ?? 0) * 100)}% to play`;
    lines.push(
      `Usage freed: ${evidence.teammate_out} usually plays ${formatStat(evidence.teammate_out_minutes)} minutes${chance}`
    );
  }
  return lines;
}

function comparisonLine(label: string, value: VsUsual): string | null {
  if (toStatNumber(value.delta) === null) return null;
  return `${label}: ${formatStat(value.projected)} projected, usually ${formatStat(value.usual)} (${signedStat(value.delta)})`;
}

export function slateEvidenceLines(player: SlatePlayer): string[] {
  const vs = player.vs_usual;
  if (!vs) return reasonEvidenceLines(player.evidence, null);
  const lines = [
    comparisonLine('Minutes', vs.minutes),
    comparisonLine('Points if he plays', vs.points),
    ...vs.categories.map((c) =>
      comparisonLine(VS_USUAL_LABELS[c.stat] ?? c.stat, {
        usual: c.usual,
        projected: c.projected,
        delta: c.delta,
      })
    ),
  ].filter((line): line is string => line !== null);
  return [...lines, ...reasonEvidenceLines(player.evidence, vs.points.usual)];
}
