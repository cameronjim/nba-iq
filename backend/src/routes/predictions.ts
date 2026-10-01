import { Router, Request, Response } from 'express';
import { query } from '../db.js';
import { SLATE_SORTS, getSlate, parsePredictionDate, parseSlateSort } from '../services/slate.js';
import {
  MAX_WINDOW_DAYS,
  POSITION_FILTERS,
  getWatchlist,
  parsePositionFilter,
  parseWindowDays,
} from '../services/watchlist.js';
import { parsePlayerId } from '../services/analytics.js';
import {
  MAX_UPCOMING_LIMIT,
  getUpcomingPredictionsForPlayer,
  parseFromDate,
  parseLimit,
} from '../services/playerPredictions.js';


const INVALID_DATE = 'date must be a calendar day formatted YYYY-MM-DD';
const INVALID_FROM = 'from must be a calendar day formatted YYYY-MM-DD';
const INVALID_LIMIT = `limit must be a whole number between 1 and ${MAX_UPCOMING_LIMIT}`;
const INVALID_PLAYER_ID = 'A numeric player id is required';
const INVALID_DAYS = `days must be a whole number between 1 and ${MAX_WINDOW_DAYS}`;
const INVALID_SORT = `sort must be one of ${SLATE_SORTS.join(', ')}`;
const INVALID_POSITION = `position must be one of ${POSITION_FILTERS.join(', ')}, or any`;

const predictionsRouter = Router();

predictionsRouter.get('/slate', async (req: Request, res: Response): Promise<void> => {
  const date = parsePredictionDate(req.query.date);
  if (date === null) {
    res.status(400).json({ error: INVALID_DATE });
    return;
  }

  const sort = parseSlateSort(req.query.sort);
  if (sort === null) {
    res.status(400).json({ error: INVALID_SORT });
    return;
  }

  try {
    res.json(await getSlate(date, sort));
  } catch {
    res.status(500).json({ error: 'Failed to fetch slate' });
  }
});

const watchlistRouter = Router();

watchlistRouter.get('/', async (req: Request, res: Response): Promise<void> => {
  const date = parsePredictionDate(req.query.date);
  if (date === null) {
    res.status(400).json({ error: INVALID_DATE });
    return;
  }

  const days = parseWindowDays(req.query.days);
  if (days === null) {
    res.status(400).json({ error: INVALID_DAYS });
    return;
  }

  const position = parsePositionFilter(req.query.position);
  if (position === false) {
    res.status(400).json({ error: INVALID_POSITION });
    return;
  }

  try {
    res.json(await getWatchlist(date, { days, position }));
  } catch {
    res.status(500).json({ error: 'Failed to fetch watchlist' });
  }
});

const playerPredictionsRouter = Router();

playerPredictionsRouter.get(
  '/:id/predictions',
  async (req: Request, res: Response): Promise<void> => {
    const playerId = parsePlayerId(req.params.id);
    if (playerId === null) {
      res.status(400).json({ error: INVALID_PLAYER_ID });
      return;
    }

    const from = parseFromDate(req.query.from);
    if (from === false) {
      res.status(400).json({ error: INVALID_FROM });
      return;
    }

    const limit = parseLimit(req.query.limit);
    if (limit === false) {
      res.status(400).json({ error: INVALID_LIMIT });
      return;
    }

    try {
      const players = await query(`SELECT nba_id, team FROM players WHERE id = $1`, [playerId]);
      const player = players.rows[0];
      if (!player) {
        res.status(404).json({ error: 'Player not found' });
        return;
      }

      const nbaPlayerId = player.nba_id === null || player.nba_id === undefined
        ? null
        : String(player.nba_id);
      const teamAbbr = player.team === null || player.team === undefined
        ? null
        : String(player.team);

      const payload = await getUpcomingPredictionsForPlayer(nbaPlayerId, {
        teamAbbr,
        from,
        limit,
      });
      res.json({ player_id: playerId, nba_player_id: nbaPlayerId, ...payload });
    } catch {
      res.status(500).json({ error: 'Failed to fetch player predictions' });
    }
  }
);

export { predictionsRouter, watchlistRouter, playerPredictionsRouter };
