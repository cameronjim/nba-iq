export function formatAmerican(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`;
}

export function formatLine(line: number): string {
  return line > 0 ? `+${line}` : `${line}`;
}

export function formatMoney(amount: number): string {
  const abs = Math.abs(amount).toFixed(2);
  return amount < 0 ? `-$${abs}` : `$${abs}`;
}

export function formatSignedMoney(amount: number): string {
  return amount >= 0 ? `+${formatMoney(amount)}` : formatMoney(amount);
}
