import type { ReactNode } from 'react';

// functional glyphs only (sort, close, search, etc.). decorative icons beside
// headings, nav links, or cards are not allowed; use a text label instead.

export interface IconProps {
  size?: number;
  className?: string;
  title?: string;
}

const Glyph = ({ size = 16, className, title, children }: IconProps & { children: ReactNode }): JSX.Element => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 16 16"
    fill="none"
    stroke="currentColor"
    strokeWidth={1.5}
    strokeLinecap="square"
    strokeLinejoin="miter"
    className={className}
    role={title ? 'img' : undefined}
    aria-hidden={title ? undefined : true}
    aria-label={title}
  >
    {children}
  </svg>
);

export const IconChevronUp = (props: IconProps): JSX.Element => (
  <Glyph {...props}><path d="M4 10l4-4 4 4" /></Glyph>
);

export const IconChevronDown = (props: IconProps): JSX.Element => (
  <Glyph {...props}><path d="M4 6l4 4 4-4" /></Glyph>
);

export const IconChevronLeft = (props: IconProps): JSX.Element => (
  <Glyph {...props}><path d="M10 4L6 8l4 4" /></Glyph>
);

export const IconChevronRight = (props: IconProps): JSX.Element => (
  <Glyph {...props}><path d="M6 4l4 4-4 4" /></Glyph>
);

export const IconClose = (props: IconProps): JSX.Element => (
  <Glyph {...props}><path d="M4 4l8 8M12 4l-8 8" /></Glyph>
);

export const IconSearch = (props: IconProps): JSX.Element => (
  <Glyph {...props}><path d="M7 2.5a4.5 4.5 0 1 0 0 9 4.5 4.5 0 0 0 0-9zM10.5 10.5L14 14" /></Glyph>
);

export const IconRefresh = (props: IconProps): JSX.Element => (
  <Glyph {...props}><path d="M13 3v3.5H9.5M12.6 6.5A5 5 0 1 0 13 9" /></Glyph>
);

export const IconTrash = (props: IconProps): JSX.Element => (
  <Glyph {...props}><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.5 9h6l.5-9" /></Glyph>
);

export const IconPlus = (props: IconProps): JSX.Element => (
  <Glyph {...props}><path d="M8 3v10M3 8h10" /></Glyph>
);

export const IconAlert = (props: IconProps): JSX.Element => (
  <Glyph {...props}><path d="M8 2l6.5 12h-13zM8 6.5v3.5M8 11.5v1" /></Glyph>
);

export const IconExternal = (props: IconProps): JSX.Element => (
  <Glyph {...props}><path d="M9 3h4v4M13 3L7.5 8.5M11 9.5V13H3V5h3.5" /></Glyph>
);

export const IconSend = (props: IconProps): JSX.Element => (
  <Glyph {...props}><path d="M2.5 8h11M9.5 4l4 4-4 4" /></Glyph>
);
