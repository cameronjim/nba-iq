import { Sparkles } from 'lucide-react';
import type { AnalyticsStat, NumericLike, PlayerPrediction, ProjectedRange } from '../../types';
import { formatStat, toStatNumber } from '../../utils/stats';
import { formatTimestampWithZone, statLabel } from '../../utils/analytics';
import { signedStat } from '../../utils/vsUsual';

interface PlayerPredictionCardProps {
  prediction: PlayerPrediction;
}

const CONFIDENCE_CLASS = {
  low: 'badge-ghost',
  medium: 'badge-warning',
  high: 'badge-success',
} as const;

type ProjectedValue = NumericLike | ProjectedRange | null;

function isRange(value: ProjectedValue): value is ProjectedRange {
  return typeof value === 'object' && value !== null && 'p50' in value;
}

const VsUsualLine = ({ prediction }: PlayerPredictionCardProps): JSX.Element | null => {
  const vs = prediction.vs_usual;
  if (!vs) return null;
  const parts = [
    toStatNumber(vs.minutes.delta) === null ? null : `MIN ${signedStat(vs.minutes.delta)}`,
    toStatNumber(vs.points.delta) === null ? null : `PTS ${signedStat(vs.points.delta)}`,
  ].filter((part): part is string => part !== null);
  if (parts.length === 0) return null;
  return (
    <p
      className="text-xs tabular-nums opacity-70"
      data-testid="prediction-vs-usual"
      title={`Usually ${formatStat(vs.minutes.usual)} min and ${formatStat(vs.points.usual)} pts, if he plays`}
    >
      vs usual: <span className="font-semibold">{parts.join(', ')}</span>
    </p>
  );
};

// a value arrives either as a plain number or as a {p10, p50, p90} band; a band renders
// as its median with the spread underneath, never as one falsely precise number.
export const PlayerPredictionCard = ({
  prediction,
}: PlayerPredictionCardProps): JSX.Element => {
  const projected = (
    Object.entries(prediction.projected ?? {}) as Array<[AnalyticsStat, ProjectedValue]>
  ).filter(([, value]) => value !== null && value !== undefined);
  const asOf = formatTimestampWithZone(prediction.as_of ?? null);
  const probActive =
    prediction.prob_active === null || prediction.prob_active === undefined
      ? null
      : toStatNumber(prediction.prob_active);
  const unconditionalPts =
    prediction.unconditional_pts === null || prediction.unconditional_pts === undefined
      ? null
      : prediction.unconditional_pts;

  return (
    <section className="card bg-base-200 border border-primary/30">
      <div className="card-body p-4 sm:p-5 gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Sparkles size={16} className="text-primary" />
          <h2 className="font-bold text-base">Projection</h2>
          {prediction.game_date && (
            <span className="badge badge-sm badge-outline">{prediction.game_date}</span>
          )}
          {probActive !== null && Number.isFinite(probActive) && (
            <span
              className={`badge badge-sm ${probActive >= 0.75 ? 'badge-success' : probActive >= 0.4 ? 'badge-warning' : 'badge-error'}`}
            >
              {Math.round(probActive * 100)}% to play
            </span>
          )}
          {prediction.confidence && (
            <span className={`badge badge-sm ${CONFIDENCE_CLASS[prediction.confidence]}`}>
              {prediction.confidence} confidence
            </span>
          )}
        </div>

        {prediction.summary && <p className="text-sm opacity-80">{prediction.summary}</p>}

        {projected.length > 0 && (
          <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
            {projected.map(([stat, value]) => (
              <div key={stat} className="rounded-box bg-base-100 border border-base-300 px-2 py-1.5">
                {isRange(value) ? (
                  <>
                    <p className="text-sm font-semibold tabular-nums">{formatStat(value.p50)}</p>
                    <p className="text-[10px] tabular-nums opacity-60">
                      {formatStat(value.p10)}-{formatStat(value.p90)}
                    </p>
                  </>
                ) : (
                  <p className="text-sm font-semibold tabular-nums">
                    {formatStat(value as NumericLike)}
                  </p>
                )}
                <p className="text-[10px] uppercase tracking-wider opacity-50">{statLabel(stat)}</p>
              </div>
            ))}
          </div>
        )}

        <VsUsualLine prediction={prediction} />

        {unconditionalPts !== null && (
          <p className="text-xs opacity-60">
            Points, counting the chance he sits:{' '}
            <span className="font-semibold tabular-nums">{formatStat(unconditionalPts)}</span>
          </p>
        )}

        <p className="text-[10px] opacity-40">
          {[
            asOf ? `Published ${asOf}` : null,
            prediction.conditional ? 'stat lines assume he plays' : null,
          ]
            .filter(Boolean)
            .join(' · ')}
        </p>
      </div>
    </section>
  );
};
