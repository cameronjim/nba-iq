const EMOJI = /[\p{Extended_Pictographic}\p{Regional_Indicator}\u{FE0F}\u{200D}\u{20E3}]/gu;

export function stripSlop(text: string): string {
  return text
    .replace(EMOJI, '')
    .replace(/^([ \t]*)\u2014[ \t]*/gm, '$1- ')
    .replace(/[ \t]*\u2014[ \t]*$/gm, '')
    .replace(/[ \t]*\u2014[ \t]*/g, ', ')
    .replace(/(\S)[ \t]{2,}/g, '$1 ')
    .replace(/[ \t]+$/gm, '');
}

export function stripSlopList(items: unknown): string[] {
  if (!Array.isArray(items)) return [];
  return items.filter((item): item is string => typeof item === 'string').map(stripSlop);
}
