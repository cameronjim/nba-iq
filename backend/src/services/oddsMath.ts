
export function americanToDecimal(odds: number): number {
  if (odds > 0) return 1 + odds / 100;
  return 1 + 100 / Math.abs(odds);
}

export function decimalToAmerican(decimal: number): number {
  if (decimal >= 2) return Math.round((decimal - 1) * 100);
  return Math.round(-100 / (decimal - 1));
}

export function americanToImpliedProb(odds: number): number {
  if (odds > 0) return 100 / (odds + 100);
  return Math.abs(odds) / (Math.abs(odds) + 100);
}

export function noVigProbabilities(a: number, b: number): [number, number] {
  const total = a + b;
  return [a / total, b / total];
}

export interface ParlayOdds {
  american: number;
  impliedProb: number;
}

export function profitOnWin(stake: number, americanOdds: number): number {
  const profit = stake * (americanToDecimal(americanOdds) - 1);
  return Math.round(profit * 100) / 100;
}

export function combineParlay(legOdds: number[]): ParlayOdds {
  const decimal = legOdds.reduce((acc, odds) => acc * americanToDecimal(odds), 1);
  return {
    american: decimalToAmerican(decimal),
    impliedProb: 1 / decimal,
  };
}
