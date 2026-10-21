// notes tokens written by ml/predict.py and ml/daily_run.py.
export const HEADLINE_RUN_NOTE = 'quantiles=headline';
export const RESCORE_RUN_NOTE = 'rescore=status_change';

function newestFullRunSql(runParam: string, excludeRescores: boolean): string {
  const rescoreFilter = excludeRescores
    ? `\n          AND COALESCE(qf.notes, '') NOT LIKE '%${RESCORE_RUN_NOTE}%'`
    : '';
  return `(SELECT qf.id
         FROM prediction_runs qr
         JOIN prediction_runs qf
           ON qf.channel = qr.channel
          AND qf.status = 'complete'
          AND (qf.predicted_at, qf.id) <= (qr.predicted_at, qr.id)
          AND COALESCE(qf.notes, '') NOT LIKE '%${HEADLINE_RUN_NOTE}%'${rescoreFilter}
         WHERE qr.id = ${runParam}
         ORDER BY qf.predicted_at DESC, qf.id DESC
         LIMIT 1)`;
}

function samePlayerGameSql(alias: string, runSql: string, quantileOnly: boolean): string {
  return `SELECT 1
         FROM player_game_predictions qx
         WHERE qx.prediction_run_id = ${runSql}
           AND qx.nba_player_id = ${alias}.nba_player_id
           AND qx.nba_game_id = ${alias}.nba_game_id${quantileOnly ? '\n           AND qx.quantile IS NOT NULL' : ''}`;
}

// a headline run stores no quantile rows, so they come from the newest full run that has the player-game.
export function runPredictionRowsSql(alias: string, runParam: string): string {
  const latestFull = newestFullRunSql(runParam, false);
  const scheduledFull = newestFullRunSql(runParam, true);
  return `(${alias}.prediction_run_id = ${runParam}
       OR (${alias}.quantile IS NOT NULL
           AND EXISTS (SELECT 1
                       FROM prediction_runs hr
                       WHERE hr.id = ${runParam}
                         AND hr.notes LIKE '%${HEADLINE_RUN_NOTE}%')
           AND ${alias}.prediction_run_id IN (${latestFull}, ${scheduledFull})
           AND EXISTS (${samePlayerGameSql(alias, runParam, false)})
           AND (${alias}.prediction_run_id = ${latestFull}
                OR NOT EXISTS (${samePlayerGameSql(alias, latestFull, true)}))))`;
}
