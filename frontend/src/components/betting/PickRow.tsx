import {
  categoryLabel, chanceSentence, confidenceWord, matchupWords, straightBetText,
} from '../../utils/bettingCopy';
import type { BettingPick } from '../../types';

interface PickRowProps {
  pick: BettingPick;
}

export const PickRow = ({ pick }: PickRowProps): JSX.Element => (
  <li className="py-3 border-t border-base-300 first:border-t-0 space-y-1">
    <p className="text-sm">
      <span className="font-semibold">{straightBetText(pick)}</span>
      <span className="text-muted"> · {categoryLabel(pick.category)} · {confidenceWord(pick.confidence)}</span>
    </p>
    <p className="text-sm">{chanceSentence(pick.estimated_win_prob, pick.implied_prob, pick.implied_prob_novig)}</p>
    <p className="text-xs text-muted">
      {matchupWords(pick.matchup)}, {pick.tipoff}.
      {pick.rationale && <> Claude's reasoning: {pick.rationale}</>}
    </p>
  </li>
);
