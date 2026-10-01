import { Flame } from 'lucide-react';
import { ReasonBadge } from '../watchlist/ReasonBadge';

export const SlateLegend = (): JSX.Element => (
  <div
    className="text-[11px] opacity-70 flex items-center gap-x-4 gap-y-1 flex-wrap"
    data-testid="slate-legend"
  >
    <span className="flex items-center gap-1.5">
      <span className="badge badge-primary badge-sm tabular-nums font-semibold">
        +11.2
      </span>
      <span>projected impact, 0 = average night</span>
    </span>
    <span className="flex items-center gap-1.5">
      <span className="tabular-nums">
        <span className="font-semibold">21.4</span> pts if he plays
      </span>
      <span>points, minutes and the category line, given he takes the floor</span>
    </span>
    <span className="flex items-center gap-1.5">
      <span className="tabular-nums opacity-70">88% to play, 18.9 over the schedule</span>
      <span>the same points with the chance he sits priced in</span>
    </span>
    <span className="flex items-center gap-1.5">
      <span className="badge badge-success badge-sm tabular-nums">87%</span>
      <span>chance he plays</span>
    </span>
    <span className="flex items-center gap-1.5">
      <Flame size={13} className="text-primary" />
      <span>slate standout</span>
    </span>
    <span className="flex items-center gap-1.5">
      <ReasonBadge reason="ROLE_INCREASE" />
      <span>why tonight differs from his usual; tap &quot;vs usual&quot; for the numbers</span>
    </span>
    <span className="flex items-center gap-1.5">
      <span className="badge badge-xs badge-error uppercase tracking-wide">
        Out<span className="font-bold normal-case">&nbsp;· new</span>
      </span>
      <span>injury report now; &quot;new&quot; = changed after this projection</span>
    </span>
  </div>
);
