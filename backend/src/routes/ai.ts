import crypto from 'crypto';
import { Router, Request, Response } from 'express';
import { query } from '../db.js';
import { callClaude, buildTeamContext, buildWaiverContext, buildBettingContext, extractJSON } from '../services/ai.js';
import { getUserPreferences, buildPreferencesPromptBlock, buildBettingPromptBlock } from '../services/preferences.js';
import { getCurrentBenchmarks, formatBenchmarksLine } from '../services/benchmarks.js';
import { getUpcomingOdds } from '../services/odds.js';
import { sanitizeChatHistory, MAX_MESSAGE_LENGTH } from '../services/chatHistory.js';
import { stripSlop, stripSlopList } from '../services/plainText.js';
import type { AuthRequest } from '../middleware/auth.js';

const router = Router();

const PROMPT_VERSION = 'v7-plain-text';

const PLAIN_OUTPUT_RULE =
  'Write in plain sentences. Never use emoji and never use em dashes; use commas, periods or hyphens instead.';

async function getRosterHash(userId: number): Promise<string> {
  const result = await query(
    `SELECT player_id FROM my_roster WHERE user_id = $1 ORDER BY player_id`,
    [userId]
  );
  const ids = result.rows.map((r: { player_id: number }) => r.player_id).join(',');
  return crypto.createHash('md5').update(ids).digest('hex');
}

router.post('/chat', async (req: Request, res: Response): Promise<void> => {
  const userId = (req as AuthRequest).userId;
  try {
    const { message, context_type, history } = req.body ?? {};
    if (typeof message !== 'string' || message.trim().length === 0) {
      res.status(400).json({ error: 'message is required' });
      return;
    }
    if (message.length > MAX_MESSAGE_LENGTH) {
      res.status(400).json({ error: `message must be ${MAX_MESSAGE_LENGTH} characters or fewer` });
      return;
    }

    const prefs = await getUserPreferences(userId);

    let context = '';
    let persona: string;
    let prefsBlock: string;
    if (context_type === 'betting') {
      persona = 'You are an expert NBA betting analyst. You help users understand betting markets and find value in upcoming games. Be honest about uncertainty: lines are efficient and big edges are rare. Plain text only, no markdown headers. ' + PLAIN_OUTPUT_RULE;
      prefsBlock = buildBettingPromptBlock(prefs);
      try {
        const games = (await getUpcomingOdds()).filter((g) => Object.keys(g.markets).length > 0);
        if (games.length > 0) context = await buildBettingContext(games);
      } catch { /* espn unavailable, chat continues without game context */ }
    } else {
      persona = 'You are an expert fantasy basketball assistant for 9-category leagues (PTS, REB, AST, STL, BLK, FG%, FT%, 3PM, TO). You help users analyze their roster and make strategic decisions. ' + PLAIN_OUTPUT_RULE;
      prefsBlock = buildPreferencesPromptBlock(prefs);
      if (context_type === 'myteam') context = await buildTeamContext(userId);
      else if (context_type === 'waiver') context = await buildWaiverContext(userId, prefs.league_size);
    }

    const systemPrompt = `${persona}${prefsBlock}\n\n${context ? `Current context:\n\n${context}` : 'No additional context.'}\n\nProvide concise, actionable advice. Reference specific stats. Be direct.

${PLAIN_OUTPUT_RULE}`;

    const messages: Array<{ role: string; content: string }> = [
      ...sanitizeChatHistory(history),
      { role: 'user', content: message },
    ];

    const reply = await callClaude(systemPrompt, messages, { model: 'claude-sonnet-5', maxTokens: 1024 });
    res.json({ reply: stripSlop(reply) });
  } catch {
    res.status(500).json({ error: 'Failed to process chat' });
  }
});

router.get('/team-analysis', async (req: Request, res: Response): Promise<void> => {
  const userId = (req as AuthRequest).userId;
  try {
    const context = await buildTeamContext(userId);
    if (context === 'No players on roster.') {
      res.json({ strengths: [], weaknesses: [], suggestions: [], categories: {} });
      return;
    }

    const rosterHash = await getRosterHash(userId);
    const prefs = await getUserPreferences(userId);
    const prefsBlock = buildPreferencesPromptBlock(prefs);
    const benchmarks = await getCurrentBenchmarks();
    const benchmarksKey = JSON.stringify(benchmarks);
    const cacheKey = crypto
      .createHash('md5')
      .update(rosterHash + '|' + prefsBlock + '|' + benchmarksKey + '|' + PROMPT_VERSION)
      .digest('hex');

    const forceRefresh = req.query.refresh === 'true';
    if (!forceRefresh) {
      const cached = await query(
        `SELECT analysis FROM analysis_cache WHERE user_id = $1 AND roster_hash = $2`,
        [userId, cacheKey]
      );
      if (cached.rows.length > 0) {
        res.json(cached.rows[0].analysis);
        return;
      }

      const stale = await query(
        `SELECT analysis, created_at FROM analysis_cache WHERE user_id = $1`,
        [userId]
      );
      if (stale.rows.length > 0) {
        res.json({ ...stale.rows[0].analysis, stale: true, cached_at: stale.rows[0].created_at });
        return;
      }
    }

    const systemPrompt = `You are an expert 9-category fantasy basketball analyst.${prefsBlock}

Benchmarks are the actual current per-player averages across active NBA rotation players (n=${benchmarks.sample_size}, filter: 30+ games & 20+ minutes per game):
  ${formatBenchmarksLine(benchmarks)}

STRICT rating rules: compute the roster's per-player average for each category, then apply these thresholds. Do not inflate ratings:

  "strong"  = team average is at least 10% above the benchmark
              (for TO, lower is better, so strong means at least 10% BELOW ${benchmarks.TO})
  "average" = team average is within plus or minus 10% of the benchmark
  "weak"    = team average is at least 10% below the benchmark
              (for TO, weak means at least 10% ABOVE ${benchmarks.TO})

The benchmarks above are the empirical league average, so a roster of exactly average rotation players would receive nine "average" ratings. Rate honestly against the actual numbers.

Return ONLY a JSON object with this exact shape (no prose, no markdown):
{
  "categories": {
    "PTS": "strong" | "average" | "weak",
    "REB": "strong" | "average" | "weak",
    "AST": "strong" | "average" | "weak",
    "STL": "strong" | "average" | "weak",
    "BLK": "strong" | "average" | "weak",
    "FG%": "strong" | "average" | "weak",
    "FT%": "strong" | "average" | "weak",
    "3PM": "strong" | "average" | "weak",
    "TO": "strong" | "average" | "weak"
  },
  "strengths": ["<specific strength with stats>", ...],
  "weaknesses": ["<specific weakness with stats>", ...],
  "suggestions": ["<actionable suggestion>", ...]
}

You MUST include at least 2 entries in each of strengths, weaknesses, and suggestions. Rate every one of the 9 categories using the strict rules above. ${PLAIN_OUTPUT_RULE} Return ONLY valid JSON.`;

    const messages = [{ role: 'user', content: `Analyze this roster:\n\n${context}` }];
    const reply = await callClaude(systemPrompt, messages, { maxTokens: 2048 });

    try {
      const parsed = JSON.parse(extractJSON(reply));
      const analysis = {
        ...parsed,
        strengths: stripSlopList(parsed.strengths),
        weaknesses: stripSlopList(parsed.weaknesses),
        suggestions: stripSlopList(parsed.suggestions),
      };

      const empty =
        Object.keys(analysis.categories ?? {}).length === 0 &&
        (analysis.strengths ?? []).length === 0 &&
        (analysis.weaknesses ?? []).length === 0 &&
        (analysis.suggestions ?? []).length === 0;

      if (empty) {
        res.json({ ...analysis, _empty: true });
        return;
      }

      await query(
        `INSERT INTO analysis_cache (user_id, roster_hash, analysis, created_at)
         VALUES ($1, $2, $3, NOW())
         ON CONFLICT (user_id) DO UPDATE SET
           roster_hash = EXCLUDED.roster_hash,
           analysis = EXCLUDED.analysis,
           created_at = NOW()`,
        [userId, cacheKey, JSON.stringify(analysis)]
      );
      res.json(analysis);
    } catch {
      res.json({ raw_analysis: stripSlop(reply), strengths: [], weaknesses: [], suggestions: [], categories: {} });
    }
  } catch {
    res.status(500).json({ error: 'Failed to analyze team' });
  }
});

router.get('/waiver-suggestions', async (req: Request, res: Response): Promise<void> => {
  const userId = (req as AuthRequest).userId;
  try {
    const rosterCount = await query(
      'SELECT COUNT(*)::int AS n FROM my_roster WHERE user_id = $1',
      [userId]
    );
    if (rosterCount.rows[0].n === 0) {
      res.json({ trade_targets: [], waiver_pickups: [], summary: '', empty_roster: true });
      return;
    }

    const forceRefresh = req.query.refresh === 'true';
    const rosterHash = await getRosterHash(userId);
    const prefs = await getUserPreferences(userId);
    const prefsBlock = buildPreferencesPromptBlock(prefs);
    const cacheKey = crypto.createHash('md5').update(rosterHash + '|' + prefsBlock + '|' + PROMPT_VERSION).digest('hex');

    if (!forceRefresh) {
      const cached = await query(
        `SELECT suggestions, created_at FROM waiver_cache
         WHERE user_id = $1 AND roster_hash = $2 AND created_at > NOW() - INTERVAL '4 hours'`,
        [userId, cacheKey]
      );
      if (cached.rows.length > 0) {
        res.json({ ...cached.rows[0].suggestions, cached: true, cached_at: cached.rows[0].created_at });
        return;
      }

      const stale = await query(
        `SELECT suggestions, created_at FROM waiver_cache WHERE user_id = $1`,
        [userId]
      );
      if (stale.rows.length > 0) {
        res.json({
          ...stale.rows[0].suggestions,
          cached: true,
          stale: true,
          cached_at: stale.rows[0].created_at,
        });
        return;
      }
    }

    const context = await buildWaiverContext(userId, prefs.league_size);

    const systemPrompt = `You are an expert fantasy basketball analyst for 9-category leagues (PTS, REB, AST, STL, BLK, FG%, FT%, 3PM, TO). Given the user's roster and the provided candidate lists, suggest improvements that address the team's weakest categories.${prefsBlock}

Rules:
- Only recommend players from the candidate lists provided. Do not invent player names.
- The candidate lists are already ranked numerically by marginal value to this roster. Explain and sanity-check that ranking rather than re-ranking from scratch: favor the top of each list, and if you skip a higher-ranked player, say why in the reasoning of the player you chose instead.
- Each player name must appear at most once across the entire response.
- The "reasoning" field must be plain text only, no markdown, no rank numbers, no meta-commentary.
- Do not include phrases like "Duplicate entry", "instead recommend", or any self-correction notes.
- Provide exactly 5 trade targets and exactly 5 waiver pickups.

Return ONLY a JSON object with this exact shape (no prose, no markdown):
{
  "trade_targets": [
    { "name": "<player name>", "reasoning": "<plain text: which weak categories they address and why>" }
  ],
  "waiver_pickups": [
    { "name": "<player name>", "reasoning": "<plain text: which weak categories they address and why>" }
  ],
  "summary": "<2-3 sentence plain text strategy focused on the team's biggest weaknesses>"
}

${PLAIN_OUTPUT_RULE} Return ONLY valid JSON.`;

    const messages = [{ role: 'user', content: `Suggest improvements:\n\n${context}` }];
    const reply = await callClaude(systemPrompt, messages, { maxTokens: 2048 });

    try {
      const raw = JSON.parse(extractJSON(reply));
      const seen = new Set<string>();
      const dedup = (list: Array<{ name: string; reasoning: string }>) =>
        (list ?? []).filter((item) => {
          const key = item.name?.toLowerCase().trim();
          if (!key || seen.has(key)) return false;
          seen.add(key);
          return true;
        }).slice(0, 5).map((item) => ({
          ...item,
          name: stripSlop(item.name),
          reasoning: stripSlop(item.reasoning ?? ''),
        }));

      const suggestions = {
        trade_targets: dedup(raw.trade_targets),
        waiver_pickups: dedup(raw.waiver_pickups),
        summary: stripSlop(raw.summary ?? ''),
      };

      const empty = suggestions.trade_targets.length === 0 && suggestions.waiver_pickups.length === 0;
      if (empty) {
        res.json({ ...suggestions, _empty: true });
        return;
      }

      await query(
        `INSERT INTO waiver_cache (user_id, roster_hash, suggestions, created_at)
         VALUES ($1, $2, $3, NOW())
         ON CONFLICT (user_id) DO UPDATE SET
           roster_hash = EXCLUDED.roster_hash,
           suggestions = EXCLUDED.suggestions,
           created_at = NOW()`,
        [userId, cacheKey, JSON.stringify(suggestions)]
      );
      res.json(suggestions);
    } catch {
      res.json({ raw: stripSlop(reply), trade_targets: [], waiver_pickups: [], summary: '' });
    }
  } catch {
    res.status(500).json({ error: 'Failed to generate suggestions' });
  }
});

export { router as aiRouter };
