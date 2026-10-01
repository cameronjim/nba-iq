import crypto from 'crypto';
import { Router, Request, Response } from 'express';
import { query } from '../db.js';
import { requireAuth, AuthRequest } from '../middleware/auth.js';
import { requireAdmin } from '../middleware/admin.js';
import { etIsoDate } from '../services/dates.js';
import {
  getPropPickSummary,
  getSurfacedPropPicks,
  refreshPropPicks,
  settlePropPicks,
  MIN_PICK_EV,
  MIN_PICK_PROB_ACTIVE,
} from '../services/propPicks.js';
import { DEFAULT_VOID_RULE } from '../services/propProbability.js';
import { rateLimit } from '../middleware/rateLimit.js';
import { callClaude, buildBettingContext, extractJSON } from '../services/ai.js';
import { getUserPreferences, buildBettingPromptBlock } from '../services/preferences.js';
import { getUpcomingOdds, computeOddsHash, type BettingGame } from '../services/odds.js';
import { americanToImpliedProb, combineParlay, noVigProbabilities, profitOnWin } from '../services/oddsMath.js';
import {
  settleBet,
  summarizeLedger,
  betNet,
  STRAIGHT_MARKETS,
  WAGER_TYPES,
  type BetMarket,
  type StraightMarket,
  type BetSelection,
  type BetStatus,
  type WagerType,
} from '../services/betSettlement.js';

const router = Router();

const BETTING_PROMPT_VERSION = 'betting-v2';

const PICKS_TTL = '90 minutes';

const PICKS_PER_CATEGORY = 2;

const picksLimiter = rateLimit({
  scope: 'betting-picks',
  limit: 60,
  windowSeconds: 86_400,
  keyFor: (req) => String((req as AuthRequest).userId),
  failClosed: true,
});

const PARLAY_EV_NOTE =
  'Parlays multiply the house edge. The combined price is usually worse value than betting the legs individually, so treat this as entertainment.';

type PickCategory = 'best_value' | 'safe' | 'hail_mary';

interface RawPick {
  game_id?: string;
  category?: string;
  market?: string;
  selection?: string;
  estimated_win_prob?: number;
  rationale?: string;
  confidence?: string;
}

interface RawParlayLeg {
  game_id?: string;
  market?: string;
  selection?: string;
}

interface EnrichedPick {
  game_id: string;
  category: PickCategory;
  market: StraightMarket;
  selection: BetSelection;
  matchup: string;
  game_date: string;
  tipoff: string;
  selection_label: string;
  line: number | null;
  american_odds: number;
  implied_prob: number;
  implied_prob_novig: number | null;
  estimated_win_prob: number;
  estimate_source: 'claude';
  edge: number;
  rationale: string;
  confidence: 'low' | 'medium' | 'high';
}

interface ResolvedSelection {
  line: number | null;
  odds: number;
  implied: number;
  impliedNoVig: number | null;
  label: string;
}

function sidePair(
  selected: number | null,
  other: number | null
): { odds: number; impliedNoVig: number | null } | null {
  if (selected == null) return null;
  if (other == null) return { odds: selected, impliedNoVig: null };
  const pair = noVigProbabilities(americanToImpliedProb(selected), americanToImpliedProb(other));
  return { odds: selected, impliedNoVig: pair[0] };
}

function resolveSelection(
  game: BettingGame,
  market: StraightMarket,
  selection: BetSelection
): ResolvedSelection | null {
  if (market === 'spread' && (selection === 'home' || selection === 'away')) {
    const s = game.markets.spread;
    if (!s) return null;
    const isHome = selection === 'home';
    const line = isHome ? s.home_line : s.away_line;
    const side = sidePair(isHome ? s.home_price : s.away_price, isHome ? s.away_price : s.home_price);
    if (!side) return null;
    const team = isHome ? game.home_team : game.away_team;
    return {
      line,
      odds: side.odds,
      implied: americanToImpliedProb(side.odds),
      impliedNoVig: side.impliedNoVig,
      label: `${team} ${line > 0 ? '+' : ''}${line}`,
    };
  }
  if (market === 'total' && (selection === 'over' || selection === 'under')) {
    const t = game.markets.total;
    if (!t) return null;
    const isOver = selection === 'over';
    const side = sidePair(isOver ? t.over_price : t.under_price, isOver ? t.under_price : t.over_price);
    if (!side) return null;
    return {
      line: t.line,
      odds: side.odds,
      implied: americanToImpliedProb(side.odds),
      impliedNoVig: side.impliedNoVig,
      label: `${isOver ? 'Over' : 'Under'} ${t.line}`,
    };
  }
  if (market === 'moneyline' && (selection === 'home' || selection === 'away')) {
    const m = game.markets.moneyline;
    if (!m) return null;
    const isHome = selection === 'home';
    const side = sidePair(isHome ? m.home : m.away, isHome ? m.away : m.home);
    if (!side) return null;
    const team = isHome ? game.home_team : game.away_team;
    return {
      line: null,
      odds: side.odds,
      implied: americanToImpliedProb(side.odds),
      impliedNoVig: side.impliedNoVig,
      label: `${team} ML (${side.odds > 0 ? '+' : ''}${side.odds})`,
    };
  }
  return null;
}

const CATEGORIES: PickCategory[] = ['best_value', 'safe', 'hail_mary'];
const CONFIDENCES = ['low', 'medium', 'high'];

function enrichPicks(rawPicks: RawPick[], gamesById: Map<string, BettingGame>): EnrichedPick[] {
  const picks: EnrichedPick[] = [];
  const perCategory: Record<PickCategory, number> = { best_value: 0, safe: 0, hail_mary: 0 };

  for (const raw of rawPicks) {
    const game = raw.game_id ? gamesById.get(raw.game_id) : undefined;
    if (!game) continue;
    if (!CATEGORIES.includes(raw.category as PickCategory)) continue;
    const category = raw.category as PickCategory;
    if (perCategory[category] >= PICKS_PER_CATEGORY) continue;
    const market = raw.market as StraightMarket;
    const selection = raw.selection as BetSelection;
    const resolved = resolveSelection(game, market, selection);
    if (!resolved) continue;
    if (typeof raw.estimated_win_prob !== 'number') continue;

    const estimated = Math.min(0.95, Math.max(0.05, raw.estimated_win_prob));
    perCategory[category] += 1;
    picks.push({
      game_id: game.espn_event_id,
      category,
      market,
      selection,
      matchup: `${game.away_team} @ ${game.home_team}`,
      game_date: game.game_date,
      tipoff: game.tipoff,
      selection_label: resolved.label,
      line: resolved.line,
      american_odds: resolved.odds,
      implied_prob: resolved.implied,
      implied_prob_novig: resolved.impliedNoVig,
      estimated_win_prob: estimated,
      estimate_source: 'claude',
      edge: estimated - (resolved.impliedNoVig ?? resolved.implied),
      rationale: typeof raw.rationale === 'string' ? raw.rationale : '',
      confidence: CONFIDENCES.includes(raw.confidence ?? '')
        ? (raw.confidence as 'low' | 'medium' | 'high')
        : 'low',
    });
  }
  return picks;
}

interface ParlaySuggestion {
  legs: Array<{ game_id: string; market: StraightMarket; selection: BetSelection; selection_label: string; matchup: string; american_odds: number }>;
  combined_american: number;
  combined_implied_prob: number;
  rationale: string;
  ev_note: string;
}

function enrichParlay(
  rawLegs: RawParlayLeg[],
  rationale: string,
  picks: EnrichedPick[]
): ParlaySuggestion | null {
  const legs = rawLegs
    .map((leg) =>
      picks.find(
        (p) => p.game_id === leg.game_id && p.market === leg.market && p.selection === leg.selection
      )
    )
    .filter((p): p is EnrichedPick => p !== undefined)
    .filter((p, i, arr) => arr.findIndex((q) => q.game_id === p.game_id) === i);

  if (legs.length < 2 || legs.length > 3) return null;

  const combined = combineParlay(legs.map((l) => l.american_odds));
  return {
    legs: legs.map((l) => ({
      game_id: l.game_id,
      market: l.market,
      selection: l.selection,
      selection_label: l.selection_label,
      matchup: l.matchup,
      american_odds: l.american_odds,
    })),
    combined_american: combined.american,
    combined_implied_prob: combined.impliedProb,
    rationale,
    ev_note: PARLAY_EV_NOTE,
  };
}

function sendEspnError(res: Response, err: unknown): void {
  const isAbort = err instanceof Error && err.name === 'AbortError';
  res.status(isAbort ? 504 : 502).json({
    error: isAbort ? 'ESPN timed out' : 'ESPN API unavailable',
  });
}

router.get('/odds', async (_req: Request, res: Response): Promise<void> => {
  try {
    const games = await getUpcomingOdds();
    res.json({ games, fetched_at: new Date().toISOString() });
  } catch (err) {
    sendEspnError(res, err);
  }
});

const PROP_RULES = {
  min_ev: MIN_PICK_EV,
  min_prob_active: MIN_PICK_PROB_ACTIVE,
  void_rule: DEFAULT_VOID_RULE,
  estimate_source: 'model',
  paper_trading: true,
};

router.get('/props', async (_req: Request, res: Response): Promise<void> => {
  try {
    const surfaced = await getSurfacedPropPicks([etIsoDate(0), etIsoDate(1)]);
    res.json({ ...surfaced, rules: PROP_RULES });
  } catch {
    res.status(500).json({ error: 'Failed to load prop picks' });
  }
});

router.get('/props/summary', async (_req: Request, res: Response): Promise<void> => {
  try {
    const markets = await getPropPickSummary();
    res.json({ markets });
  } catch {
    res.status(500).json({ error: 'Failed to load prop pick summary' });
  }
});

router.post('/props/refresh', requireAuth, requireAdmin, async (_req: Request, res: Response): Promise<void> => {
  try {
    res.json(await refreshPropPicks());
  } catch {
    res.status(500).json({ error: 'Failed to refresh prop picks' });
  }
});

router.post('/props/settle', requireAuth, requireAdmin, async (_req: Request, res: Response): Promise<void> => {
  try {
    res.json(await settlePropPicks());
  } catch {
    res.status(500).json({ error: 'Failed to settle prop picks' });
  }
});

router.get('/picks', requireAuth, picksLimiter, async (req: Request, res: Response): Promise<void> => {
  const userId = (req as AuthRequest).userId;
  try {
    let games: BettingGame[];
    try {
      games = await getUpcomingOdds();
    } catch (err) {
      sendEspnError(res, err);
      return;
    }

    const bettable = games.filter((g) => Object.keys(g.markets).length > 0);
    if (bettable.length === 0) {
      res.json({ picks: [], parlay: null, summary: '', no_games: true });
      return;
    }

    const prefs = await getUserPreferences(userId);
    const prefsBlock = buildBettingPromptBlock(prefs);

    const oddsHash = computeOddsHash(bettable);
    const cacheKey = crypto
      .createHash('md5')
      .update(oddsHash + '|' + prefsBlock + '|' + BETTING_PROMPT_VERSION)
      .digest('hex');

    const forceRefresh = req.query.refresh === 'true';
    if (!forceRefresh) {
      const cached = await query(
        `SELECT picks, created_at FROM betting_cache
         WHERE user_id = $1 AND odds_hash = $2 AND created_at > NOW() - INTERVAL '${PICKS_TTL}'`,
        [userId, cacheKey]
      );
      if (cached.rows.length > 0) {
        res.json({
          ...cached.rows[0].picks,
          cached: true,
          cached_at: cached.rows[0].created_at,
        });
        return;
      }

      const stale = await query(
        `SELECT picks, created_at FROM betting_cache WHERE user_id = $1`,
        [userId]
      );
      if (stale.rows.length > 0) {
        res.json({
          ...stale.rows[0].picks,
          cached: true,
          stale: true,
          cached_at: stale.rows[0].created_at,
        });
        return;
      }
    }

    const context = await buildBettingContext(bettable);

    const systemPrompt = `You are an expert NBA betting analyst. You are given upcoming games with their posted betting markets (spread, total, moneyline), each with the sportsbook's implied probability, plus team records, offensive/defensive/net ratings, recent form over the last 10 games, head-to-head results between the two teams, and injury reports.${prefsBlock}

Your job is to estimate the TRUE win probability of selections and find value relative to the implied probability. Weigh recent form and head-to-head history heavily: a team's last 10 games and its history against this specific opponent are often better signals than season-long ratings. Rules:
- Only reference the provided games, by their exact game_id. Never invent games, lines, or odds.
- Take the posted lines and odds as given; your output is your estimated win probability and reasoning.
- Be honest and calibrated: estimated_win_prob must be between 0.30 and 0.80. Real edges over 8 percentage points are rare; most lines are efficient.
- Use the injury report, recent form, and head-to-head results to justify each pick. The "rationale" field is plain text only: no markdown and never use em dashes.
- Provide EXACTLY 2 picks in each category (6 picks total). If the slate is thin, still provide 2 per category and reflect the weaker conviction in lower confidence and honest rationale. The same game may appear in multiple picks only with different markets.
  - "best_value": the largest gaps between your estimated probability and the implied probability.
  - "safe": high estimated win probability, even if the payout is modest.
  - "hail_mary": a longshot (plus-money) with a plausible path to hitting.
- Also build one parlay of 2-3 legs chosen from your own picks, each leg from a DIFFERENT game. If only one game is available, return null for the parlay.
- End with a 2-3 sentence plain-text summary of the slate. Never use em dashes anywhere in your response.

Return ONLY a JSON object with this exact shape (no prose, no markdown):
{
  "picks": [
    { "game_id": "<id>", "category": "best_value" | "safe" | "hail_mary", "market": "spread" | "total" | "moneyline", "selection": "home" | "away" | "over" | "under", "estimated_win_prob": <number 0-1>, "rationale": "<plain text>", "confidence": "low" | "medium" | "high" }
  ],
  "parlay": { "legs": [ { "game_id": "<id>", "market": "...", "selection": "..." } ], "rationale": "<plain text>" } | null,
  "summary": "<plain text>"
}

Return ONLY valid JSON.`;

    const messages = [{ role: 'user', content: `Assess these games:\n\n${context}` }];
    const reply = await callClaude(systemPrompt, messages, { maxTokens: 3072 });

    try {
      const raw = JSON.parse(extractJSON(reply)) as {
        picks?: RawPick[];
        parlay?: { legs?: RawParlayLeg[]; rationale?: string } | null;
        summary?: string;
      };

      const picks = enrichPicks(raw.picks ?? [], new Map(bettable.map((g) => [g.espn_event_id, g])));
      const parlay = enrichParlay(raw.parlay?.legs ?? [], raw.parlay?.rationale ?? '', picks);
      const summary = typeof raw.summary === 'string' ? raw.summary : '';

      if (picks.length === 0) {
        res.json({ picks: [], parlay: null, summary, _empty: true });
        return;
      }

      await query(
        `INSERT INTO betting_cache (user_id, odds_hash, picks, created_at)
         VALUES ($1, $2, $3, NOW())
         ON CONFLICT (user_id) DO UPDATE SET
           odds_hash = EXCLUDED.odds_hash,
           picks = EXCLUDED.picks,
           created_at = NOW()`,
        [userId, cacheKey, JSON.stringify({ picks, parlay, summary })]
      );
      res.json({ picks, parlay, summary });
    } catch {
      res.json({ picks: [], parlay: null, summary: '', raw: reply });
    }
  } catch {
    res.status(500).json({ error: 'Failed to generate picks' });
  }
});

interface BetRow {
  id: number;
  market: BetMarket;
  nba_game_id: string | null;
  home_team: string | null;
  away_team: string | null;
  game_date: string | null;
  selection: BetSelection | null;
  line: number | null;
  american_odds: number | null;
  description: string | null;
  stake: number | null;
  wager_type: WagerType;
  status: BetStatus;
  created_at: string;
  settled_at: string | null;
}

router.get('/bets', requireAuth, async (req: Request, res: Response): Promise<void> => {
  const userId = (req as AuthRequest).userId;
  try {
    const settleable = await query(
      `SELECT b.id, b.market, b.selection, b.line::float AS line,
              g.home_score, g.away_score
       FROM bets b
       JOIN games g ON g.nba_game_id = b.nba_game_id
       WHERE b.user_id = $1 AND b.status = 'pending'
         AND b.market IN ('spread', 'total', 'moneyline')
         AND g.status = 'Final'
         AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL`,
      [userId]
    );
    for (const row of settleable.rows) {
      const outcome = settleBet(
        { market: row.market, selection: row.selection, line: row.line },
        row.home_score,
        row.away_score
      );
      await query(
        `UPDATE bets SET status = $1, settled_at = NOW() WHERE id = $2 AND user_id = $3`,
        [outcome, row.id, userId]
      );
    }

    const result = await query(
      `SELECT id, market, nba_game_id, home_team, away_team, game_date, selection,
              line::float AS line, american_odds, description,
              stake::float AS stake, wager_type,
              status, created_at, settled_at
       FROM bets
       WHERE user_id = $1
       ORDER BY created_at DESC`,
      [userId]
    );

    const bets = (result.rows as BetRow[]).map((b) => ({
      ...b,
      net: betNet(b.status, b.wager_type, b.stake, b.american_odds),
      to_win: b.stake != null && b.american_odds != null ? profitOnWin(b.stake, b.american_odds) : null,
    }));
    const net = Math.round(bets.reduce((sum, b) => sum + (b.net ?? 0), 0) * 100) / 100;
    res.json({ bets, summary: { ...summarizeLedger(bets), net } });
  } catch {
    res.status(500).json({ error: 'Failed to load bets' });
  }
});

const TEXT_MARKETS: BetMarket[] = ['prop', 'parlay', 'custom'];

router.post('/bets', requireAuth, async (req: Request, res: Response): Promise<void> => {
  const userId = (req as AuthRequest).userId;
  try {
    const { nba_game_id, market, selection, line, american_odds, description, stake, wager_type } = req.body ?? {};

    const isStraight = (STRAIGHT_MARKETS as string[]).includes(market);
    const isText = (TEXT_MARKETS as string[]).includes(market);
    if (!isStraight && !isText) {
      res.status(400).json({ error: 'Invalid market' });
      return;
    }

    if (!Number.isInteger(american_odds) || Math.abs(american_odds) < 100 || Math.abs(american_odds) > 10000) {
      res.status(400).json({ error: 'american_odds must be an integer like -110 or +150' });
      return;
    }

    const wagerType: WagerType = wager_type ?? 'cash';
    if (!WAGER_TYPES.includes(wagerType)) {
      res.status(400).json({ error: 'wager_type must be cash, bonus_bet, or odds_boost' });
      return;
    }
    if (typeof stake !== 'number' || stake <= 0 || stake > 100000) {
      res.status(400).json({ error: 'stake must be between 0 and 100000' });
      return;
    }

    interface GameRef { home_team: string; away_team: string; game_date: string }
    let resolvedGame: GameRef | null = null;
    const resolveGame = async (): Promise<GameRef | null> => {
      try {
        const games = await getUpcomingOdds();
        const game = games.find((g) => g.espn_event_id === nba_game_id);
        if (game) {
          return { home_team: game.home_team, away_team: game.away_team, game_date: game.game_date };
        }
      } catch {
      }
      const dbGame = await query(
        `SELECT home_team, away_team, game_date FROM games WHERE nba_game_id = $1`,
        [nba_game_id]
      );
      return dbGame.rows.length > 0 ? (dbGame.rows[0] as GameRef) : null;
    };

    if (isStraight) {
      const validSelections: Record<string, string[]> = {
        spread: ['home', 'away'],
        total: ['over', 'under'],
        moneyline: ['home', 'away'],
      };
      if (typeof nba_game_id !== 'string' || nba_game_id.length === 0 || nba_game_id.length > 20) {
        res.status(400).json({ error: 'nba_game_id is required' });
        return;
      }
      if (!validSelections[market].includes(selection)) {
        res.status(400).json({ error: 'Invalid selection for this market' });
        return;
      }
      if (market === 'moneyline') {
        if (line != null) {
          res.status(400).json({ error: 'Moneyline bets have no line' });
          return;
        }
      } else {
        if (typeof line !== 'number') {
          res.status(400).json({ error: 'line is required for spread and total bets' });
          return;
        }
        if (market === 'spread' && (line < -60 || line > 60)) {
          res.status(400).json({ error: 'Spread line out of range' });
          return;
        }
        if (market === 'total' && (line < 150 || line > 350)) {
          res.status(400).json({ error: 'Total line out of range' });
          return;
        }
      }
      resolvedGame = await resolveGame();
      if (!resolvedGame) {
        res.status(400).json({ error: 'Unknown game' });
        return;
      }
    } else {
      if (typeof description !== 'string' || description.trim().length < 3 || description.trim().length > 300) {
        res.status(400).json({ error: 'description is required (3-300 characters)' });
        return;
      }
      if (selection != null || line != null) {
        res.status(400).json({ error: 'selection and line only apply to spread/total/moneyline bets' });
        return;
      }
      if (typeof nba_game_id === 'string' && nba_game_id.length > 0 && nba_game_id.length <= 20) {
        resolvedGame = await resolveGame();
      }
    }

    const game = resolvedGame;
    const result = await query(
      `INSERT INTO bets (user_id, market, nba_game_id, home_team, away_team, game_date,
                         selection, line, american_odds, description, stake, wager_type)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
       RETURNING id, market, nba_game_id, home_team, away_team, game_date, selection,
                 line::float AS line, american_odds, description,
                 stake::float AS stake, wager_type,
                 status, created_at, settled_at`,
      [
        userId,
        market,
        // stores an espn event id pending a provider-to-nba mapping
        game ? nba_game_id : null,
        game?.home_team ?? null,
        game?.away_team ?? null,
        game?.game_date ?? null,
        isStraight ? selection : null,
        isStraight && market !== 'moneyline' ? line : null,
        american_odds,
        isText ? description.trim() : null,
        stake,
        wagerType,
      ]
    );
    const saved = result.rows[0];
    res.status(201).json({
      ...saved,
      to_win: profitOnWin(stake, american_odds),
    });
  } catch {
    res.status(500).json({ error: 'Failed to save bet' });
  }
});

router.patch('/bets/:id', requireAuth, async (req: Request, res: Response): Promise<void> => {
  const userId = (req as AuthRequest).userId;
  try {
    const id = parseInt(String(req.params.id), 10);
    if (Number.isNaN(id)) {
      res.status(400).json({ error: 'Invalid bet id' });
      return;
    }
    const { status } = req.body ?? {};
    if (!['pending', 'won', 'lost', 'push'].includes(status)) {
      res.status(400).json({ error: 'status must be pending, won, lost, or push' });
      return;
    }
    const settledAt = status === 'pending' ? null : new Date();
    const result = await query(
      `UPDATE bets
       SET status = $1, settled_at = $2
       WHERE id = $3 AND user_id = $4
       RETURNING id, market, nba_game_id, home_team, away_team, game_date, selection,
                 line::float AS line, american_odds, description,
                 stake::float AS stake, wager_type,
                 status, created_at, settled_at`,
      [status, settledAt, id, userId]
    );
    if (result.rows.length === 0) {
      res.status(404).json({ error: 'Bet not found' });
      return;
    }
    res.json(result.rows[0]);
  } catch {
    res.status(500).json({ error: 'Failed to update bet' });
  }
});

router.delete('/bets/:id', requireAuth, async (req: Request, res: Response): Promise<void> => {
  const userId = (req as AuthRequest).userId;
  try {
    const id = parseInt(String(req.params.id), 10);
    if (Number.isNaN(id)) {
      res.status(400).json({ error: 'Invalid bet id' });
      return;
    }
    const result = await query(
      `DELETE FROM bets WHERE id = $1 AND user_id = $2`,
      [id, userId]
    );
    if (result.rowCount === 0) {
      res.status(404).json({ error: 'Bet not found' });
      return;
    }
    res.status(204).end();
  } catch {
    res.status(500).json({ error: 'Failed to delete bet' });
  }
});

export { router as bettingRouter };
