import { Router, Request, Response } from 'express';
import { query } from '../db.js';
import type { AuthRequest } from '../middleware/auth.js';
import { parsePredictionDate } from '../services/slate.js';
import { MAX_WINDOW_DAYS } from '../services/watchlist.js';
import { getWeeklyOutlook, parseOutlookDays } from '../services/weeklyOutlook.js';
import { MAX_STARTING_SLOTS, getStartSit, parseStartingSlots } from '../services/startSit.js';
import { getStreamers } from '../services/streamers.js';
import { checkTrade, parseTradeRequest } from '../services/tradeCheck.js';

const START_ERROR = 'start must be a calendar day formatted YYYY-MM-DD';
const DAYS_ERROR = `days must be a whole number between 1 and ${MAX_WINDOW_DAYS}`;

const router = Router();

router.get('/roster', async (req: Request, res: Response): Promise<void> => {
  const userId = (req as AuthRequest).userId;
  try {
    const result = await query(
      `SELECT mr.id, mr.player_id, mr.added_at,
              p.id, p.nba_id, p.name, p.team, p.position,
              p.points_per_game, p.rebounds_per_game, p.assists_per_game, p.steals_per_game, p.blocks_per_game,
              p.field_goal_percentage, p.three_point_percentage, p.free_throw_percentage, p.three_pointers_made,
              p.turnovers_per_game, p.minutes_per_game, p.games_played,
              p.injury_status, p.injury_detail, p.headshot_url
       FROM my_roster mr
       JOIN players p ON mr.player_id = p.id
       WHERE mr.user_id = $1
       ORDER BY p.points_per_game DESC`,
      [userId]
    );
    res.json(result.rows);
  } catch (error) {
    res.status(500).json({ error: 'Failed to fetch roster' });
  }
});

router.post('/roster', async (req: Request, res: Response): Promise<void> => {
  const userId = (req as AuthRequest).userId;
  try {
    const { player_id } = req.body;
    if (!player_id) {
      res.status(400).json({ error: 'player_id is required' });
      return;
    }
    const result = await query(
      `INSERT INTO my_roster (player_id, user_id) VALUES ($1, $2) RETURNING id, player_id, added_at`,
      [player_id, userId]
    );
    res.status(201).json(result.rows[0]);
  } catch (error: unknown) {
    if (error instanceof Error && 'code' in error && (error as { code: string }).code === '23505') {
      res.status(409).json({ error: 'Player is already on your roster' });
      return;
    }
    res.status(500).json({ error: 'Failed to add player' });
  }
});

router.delete('/roster/:playerId', async (req: Request, res: Response): Promise<void> => {
  const userId = (req as AuthRequest).userId;
  try {
    const { playerId } = req.params;
    const result = await query(
      `DELETE FROM my_roster WHERE player_id = $1 AND user_id = $2 RETURNING id`,
      [playerId, userId]
    );
    if (result.rows.length === 0) {
      res.status(404).json({ error: 'Player not on roster' });
      return;
    }
    res.json({ message: 'Player removed' });
  } catch (error) {
    res.status(500).json({ error: 'Failed to remove player' });
  }
});

router.get('/weekly-outlook', async (req: Request, res: Response): Promise<void> => {
  const userId = (req as AuthRequest).userId;
  const start = parsePredictionDate(req.query.start);
  if (start === null) {
    res.status(400).json({ error: START_ERROR });
    return;
  }
  const days = parseOutlookDays(req.query.days);
  if (days === null) {
    res.status(400).json({ error: DAYS_ERROR });
    return;
  }

  try {
    res.json(await getWeeklyOutlook(userId, start, days));
  } catch {
    res.status(500).json({ error: 'Failed to build weekly outlook' });
  }
});

router.get('/start-sit', async (req: Request, res: Response): Promise<void> => {
  const userId = (req as AuthRequest).userId;
  const start = parsePredictionDate(req.query.start);
  if (start === null) {
    res.status(400).json({ error: START_ERROR });
    return;
  }
  const days = parseOutlookDays(req.query.days);
  if (days === null) {
    res.status(400).json({ error: DAYS_ERROR });
    return;
  }
  const slots = parseStartingSlots(req.query.slots);
  if (slots === null) {
    res.status(400).json({ error: `slots must be a whole number between 1 and ${MAX_STARTING_SLOTS}` });
    return;
  }

  try {
    res.json(await getStartSit(userId, start, days, slots));
  } catch {
    res.status(500).json({ error: 'Failed to build the start/sit lineup' });
  }
});

router.get('/streamers', async (req: Request, res: Response): Promise<void> => {
  const userId = (req as AuthRequest).userId;
  const start = parsePredictionDate(req.query.start);
  if (start === null) {
    res.status(400).json({ error: START_ERROR });
    return;
  }
  const days = parseOutlookDays(req.query.days);
  if (days === null) {
    res.status(400).json({ error: DAYS_ERROR });
    return;
  }

  try {
    res.json(await getStreamers(userId, start, days));
  } catch {
    res.status(500).json({ error: 'Failed to rank streaming pickups' });
  }
});

router.post('/trade-check', async (req: Request, res: Response): Promise<void> => {
  const userId = (req as AuthRequest).userId;
  const body: unknown = req.body;
  const parsed = parseTradeRequest(body);
  if (!parsed.ok) {
    res.status(400).json({ error: parsed.error });
    return;
  }
  const fields = typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : {};
  const start = parsePredictionDate(fields.start);
  if (start === null) {
    res.status(400).json({ error: START_ERROR });
    return;
  }
  const days = parseOutlookDays(fields.days);
  if (days === null) {
    res.status(400).json({ error: DAYS_ERROR });
    return;
  }

  try {
    const outcome = await checkTrade(userId, parsed.request, start, days);
    if (outcome.kind === 'invalid') {
      res.status(400).json({ error: outcome.error });
      return;
    }
    res.json(outcome.response);
  } catch {
    res.status(500).json({ error: 'Failed to check the trade' });
  }
});

export { router as fantasyRouter };
