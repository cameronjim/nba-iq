import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = join(__dirname, '..', 'src');

interface Rule {
  name: string;
  pattern: RegExp;
}

const RULES: Rule[] = [
  { name: 'icon library import', pattern: /from ['"](lucide-react|react-icons|@heroicons|@tabler)/ },
  { name: 'em dash', pattern: /—/ },
  { name: 'emoji or pictograph', pattern: /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}⭐✓✔★☆]/u },
  { name: 'gradient', pattern: /\bbg-(gradient|linear|radial|conic)-|\b(from|via|to)-(primary|secondary|accent|base|white|black)\b/ },
  { name: 'drop shadow', pattern: /(?<![\w-])(drop-)?shadow(-(xs|sm|md|lg|xl|2xl|inner))?(?![\w-])/ },
  { name: 'glass blur', pattern: /\bbackdrop-|\bblur-(sm|md|lg|xl|2xl|3xl)\b/ },
  { name: 'soft radius', pattern: /\brounded-(md|lg|xl|2xl|3xl)\b/ },
  { name: 'animated hover', pattern: /\b(transition|duration-\d+|ease-(in|out)|hover:scale|hover:translate|group-hover:translate|hover:-translate|animate-(bounce|ping|spin))/ },
  { name: 'spinner instead of skeleton', pattern: /\bloading-(spinner|dots|ring|ball|bars|infinity)\b/ },
  { name: 'pure white or black', pattern: /\b(bg|text|border)-(white|black)\b|#fff\b|#ffffff\b|#000\b|#000000\b/i },
  { name: 'raw tailwind palette color', pattern: /\b(bg|text|border|ring|fill|stroke)-(red|green|blue|emerald|amber|yellow|purple|pink|orange|sky|indigo|violet|teal|cyan|lime|rose|fuchsia)-\d{2,3}\b/ },
  { name: 'rainbow semantic color', pattern: /\b(badge|btn|text|bg|border|alert)-(secondary|info)\b/ },
  { name: 'eyebrow label', pattern: /\btracking-widest\b/ },
  { name: 'colored left stripe', pattern: /\bborder-l-(2|4|8)\b/ },
  { name: 'opacity on text', pattern: /(?<![\w:-])opacity-\d+\b/ },
  { name: 'banned font', pattern: /\b(Inter|Geist|Space Grotesk)\b/ },
  { name: 'slop copy', pattern: /\b(seamless(ly)?|supercharge|unlock(s|ing)? (the|your)|elevate|empower|delve|leverage|cutting-edge|game[- ]changer|revolutioni[sz]e|effortless(ly)?|AI-powered|powered by AI|(?<!orlando )magic(al)?)\b/i },
  { name: "it's not x, it's y", pattern: /\b(it'?s|this is) not (just )?[^.'"]{1,40}[,;] (it'?s|but)\b/i },
];

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.(ts|tsx)$/.test(entry) ? [full] : [];
  });
}

function violations(): string[] {
  const found: string[] = [];
  for (const file of sourceFiles(SRC)) {
    const lines = readFileSync(file, 'utf8').split('\n');
    lines.forEach((line, i) => {
      for (const rule of RULES) {
        if (rule.pattern.test(line)) {
          found.push(`${relative(SRC, file)}:${i + 1} [${rule.name}] ${line.trim().slice(0, 100)}`);
        }
      }
    });
  }
  return found;
}

describe('design rules', () => {
  it('keeps the source free of the banned visual and copy patterns', () => {
    // act
    const found = violations();

    // assert
    expect(found).toEqual([]);
  });
});
