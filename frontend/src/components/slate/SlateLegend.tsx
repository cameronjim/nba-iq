export const SlateLegend = (): JSX.Element => (
  <dl className="text-xs text-muted border-y border-base-300 py-2 flex flex-col gap-1" data-testid="slate-legend">
    <div className="flex gap-2">
      <dt className="font-semibold tabular-nums text-base-content w-24 shrink-0">+11.2</dt>
      <dd>projected impact, 0 = average night</dd>
    </div>
    <div className="flex gap-2">
      <dt className="font-semibold tabular-nums text-base-content w-24 shrink-0">21.4 pts</dt>
      <dd>points, minutes and the category line, given he takes the floor</dd>
    </div>
    <div className="flex gap-2">
      <dt className="font-semibold tabular-nums text-base-content w-24 shrink-0">88% to play</dt>
      <dd>the same points with the chance he sits priced in, e.g. 18.9 over the schedule</dd>
    </div>
    <div className="flex gap-2">
      <dt className="font-semibold tabular-nums text-success w-24 shrink-0">87%</dt>
      <dd>chance he plays</dd>
    </div>
    <div className="flex gap-2">
      <dt className="font-semibold text-primary w-24 shrink-0">Top</dt>
      <dd>slate standout</dd>
    </div>
    <div className="flex gap-2">
      <dt className="font-semibold text-base-content w-24 shrink-0">Role increase</dt>
      <dd>why tonight differs from his usual; open &quot;vs usual&quot; for the numbers</dd>
    </div>
    <div className="flex gap-2">
      <dt className="font-semibold uppercase text-error w-24 shrink-0">
        Out<span className="normal-case">&nbsp;· new</span>
      </dt>
      <dd>injury report now; &quot;new&quot; = changed after this projection</dd>
    </div>
  </dl>
);
