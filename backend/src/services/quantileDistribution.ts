export const P10 = 0.1;
export const P50 = 0.5;
export const P90 = 0.9;

export interface QuantileAnchors {
  p10: number;
  p50: number;
  p90: number;
}

function slopes(anchors: QuantileAnchors): { low: number; high: number } {
  return {
    low: (anchors.p50 - anchors.p10) / (P50 - P10),
    high: (anchors.p90 - anchors.p50) / (P90 - P50),
  };
}

// piecewise linear through the three anchors, linear tails on the outer slopes, floored at 0.
export function quantileAt(u: number, anchors: QuantileAnchors): number {
  const { p10, p50 } = anchors;
  const { low, high } = slopes(anchors);
  const value = u <= P50 ? p10 + (u - P10) * low : p50 + (u - P50) * high;
  return Math.max(0, value);
}

// the inverse of quantileAt over u in [0, 1]: P(X <= x), with the floor's mass sitting at 0.
export function cdfAt(x: number, anchors: QuantileAnchors): number {
  if (x < 0) return 0;
  const { p10, p50 } = anchors;
  const { low, high } = slopes(anchors);
  let u: number;
  if (x < p50) {
    if (low <= 0) return 0;
    u = P10 + (x - p10) / low;
  } else {
    if (high <= 0) return 1;
    u = P50 + (x - p50) / high;
  }
  return Math.min(1, Math.max(0, u));
}
