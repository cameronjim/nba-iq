import type { WatchlistPositionFilter } from '../../types';

export const POSITION_LABELS: Record<WatchlistPositionFilter, string> = {
  G: 'Guards',
  F: 'Forwards',
  C: 'Centers',
  PG: 'Point guards',
  SG: 'Shooting guards',
  SF: 'Small forwards',
  PF: 'Power forwards',
};

const POSITION_ORDER: WatchlistPositionFilter[] = ['G', 'F', 'C', 'PG', 'SG', 'SF', 'PF'];

interface WeekFiltersProps {
  position: WatchlistPositionFilter | null;
  // only what the server said it honours, so a choice can never produce a 400.
  positionOptions: WatchlistPositionFilter[];
  onPositionChange: (position: WatchlistPositionFilter | null) => void;
  team: string;
  teams: string[];
  onTeamChange: (team: string) => void;
}

export const WeekFilters = ({
  position,
  positionOptions,
  onPositionChange,
  team,
  teams,
  onTeamChange,
}: WeekFiltersProps): JSX.Element => (
  <div className="flex flex-wrap items-center gap-2">
    <select
      value={position ?? ''}
      onChange={(e) => {
        const next = POSITION_ORDER.find((option) => option === e.target.value);
        onPositionChange(next ?? null);
      }}
      className="select select-bordered select-sm w-[170px]"
      aria-label="Filter by position"
    >
      <option value="">All positions</option>
      {POSITION_ORDER.filter((option) => positionOptions.includes(option)).map((option) => (
        <option key={option} value={option}>
          {POSITION_LABELS[option]}
        </option>
      ))}
    </select>
    <select
      value={team}
      onChange={(e) => onTeamChange(e.target.value)}
      className="select select-bordered select-sm w-[140px]"
      aria-label="Filter by team"
    >
      <option value="">All teams</option>
      {teams.map((abbr) => (
        <option key={abbr} value={abbr}>
          {abbr}
        </option>
      ))}
    </select>
  </div>
);
