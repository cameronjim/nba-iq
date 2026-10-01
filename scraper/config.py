from datetime import date, datetime
from zoneinfo import ZoneInfo

TEAM_META = {
    "ATL": {"conference": "East", "division": "Southeast", "full_name": "Atlanta Hawks"},
    "BOS": {"conference": "East", "division": "Atlantic", "full_name": "Boston Celtics"},
    "BKN": {"conference": "East", "division": "Atlantic", "full_name": "Brooklyn Nets"},
    "CHA": {"conference": "East", "division": "Southeast", "full_name": "Charlotte Hornets"},
    "CHI": {"conference": "East", "division": "Central", "full_name": "Chicago Bulls"},
    "CLE": {"conference": "East", "division": "Central", "full_name": "Cleveland Cavaliers"},
    "DAL": {"conference": "West", "division": "Southwest", "full_name": "Dallas Mavericks"},
    "DEN": {"conference": "West", "division": "Northwest", "full_name": "Denver Nuggets"},
    "DET": {"conference": "East", "division": "Central", "full_name": "Detroit Pistons"},
    "GSW": {"conference": "West", "division": "Pacific", "full_name": "Golden State Warriors"},
    "HOU": {"conference": "West", "division": "Southwest", "full_name": "Houston Rockets"},
    "IND": {"conference": "East", "division": "Central", "full_name": "Indiana Pacers"},
    "LAC": {"conference": "West", "division": "Pacific", "full_name": "Los Angeles Clippers"},
    "LAL": {"conference": "West", "division": "Pacific", "full_name": "Los Angeles Lakers"},
    "MEM": {"conference": "West", "division": "Southwest", "full_name": "Memphis Grizzlies"},
    "MIA": {"conference": "East", "division": "Southeast", "full_name": "Miami Heat"},
    "MIL": {"conference": "East", "division": "Central", "full_name": "Milwaukee Bucks"},
    "MIN": {"conference": "West", "division": "Northwest", "full_name": "Minnesota Timberwolves"},
    "NOP": {"conference": "West", "division": "Southwest", "full_name": "New Orleans Pelicans"},
    "NYK": {"conference": "East", "division": "Atlantic", "full_name": "New York Knicks"},
    "OKC": {"conference": "West", "division": "Northwest", "full_name": "Oklahoma City Thunder"},
    "ORL": {"conference": "East", "division": "Southeast", "full_name": "Orlando Magic"},
    "PHI": {"conference": "East", "division": "Atlantic", "full_name": "Philadelphia 76ers"},
    "PHX": {"conference": "West", "division": "Pacific", "full_name": "Phoenix Suns"},
    "POR": {"conference": "West", "division": "Northwest", "full_name": "Portland Trail Blazers"},
    "SAC": {"conference": "West", "division": "Pacific", "full_name": "Sacramento Kings"},
    "SAS": {"conference": "West", "division": "Southwest", "full_name": "San Antonio Spurs"},
    "TOR": {"conference": "East", "division": "Atlantic", "full_name": "Toronto Raptors"},
    "UTA": {"conference": "West", "division": "Northwest", "full_name": "Utah Jazz"},
    "WAS": {"conference": "East", "division": "Southeast", "full_name": "Washington Wizards"},
}

# leaguedashteamstats' Base measure type omits TEAM_ABBREVIATION, so the
# permanent team id is the only way to recover it there.
TEAM_ID_TO_ABBR = {
    "1610612737": "ATL", "1610612738": "BOS", "1610612751": "BKN",
    "1610612766": "CHA", "1610612741": "CHI", "1610612739": "CLE",
    "1610612742": "DAL", "1610612743": "DEN", "1610612765": "DET",
    "1610612744": "GSW", "1610612745": "HOU", "1610612754": "IND",
    "1610612746": "LAC", "1610612747": "LAL", "1610612763": "MEM",
    "1610612748": "MIA", "1610612749": "MIL", "1610612750": "MIN",
    "1610612740": "NOP", "1610612752": "NYK", "1610612760": "OKC",
    "1610612753": "ORL", "1610612755": "PHI", "1610612756": "PHX",
    "1610612757": "POR", "1610612758": "SAC", "1610612759": "SAS",
    "1610612761": "TOR", "1610612762": "UTA", "1610612764": "WAS",
}

ABBR_TO_TEAM_ID = {abbr: team_id for team_id, abbr in TEAM_ID_TO_ABBR.items()}

NAME_TO_ABBR = {
    meta["full_name"].lower(): abbr for abbr, meta in TEAM_META.items()
}
NAME_TO_ABBR["la clippers"] = "LAC"

# cbs team urls use its own short codes where they differ from nba.com's.
CBS_TEAM_ABBR_ALIASES = {
    "GS": "GSW", "NO": "NOP", "NY": "NYK", "PHO": "PHX", "SA": "SAS",
    "WSH": "WAS", "UTAH": "UTA", "BRK": "BKN", "CHO": "CHA",
}



def current_season(today: date) -> str:
    # july 1 starts a season, matching season_start_date in parsing.py.
    start_year = today.year if today.month >= 7 else today.year - 1
    return f"{start_year}-{(start_year + 1) % 100:02d}"


# the un-flagged cron follows this default; --season overrides it per run.
SEASON = current_season(datetime.now(ZoneInfo("America/New_York")).date())

BACKFILL_DEFAULT_FROM_SEASON = "1979-80"
# stats.nba.com resets connections after a handful of rapid requests, so the
# per-entity crawls are slow on purpose.
BACKFILL_REQUEST_DELAY_SECONDS = 5.0
BACKFILL_MAX_ATTEMPTS = 4
ADVANCED_RATINGS_FIRST_SEASON_START_YEAR = 1996

# BoxScoreSummaryV2 has no inactive-list data from this date on, so past it an
# empty V2 answer means "no data", not "nobody was inactive".
V2_INACTIVE_UNRELIABLE_FROM = date(2025, 4, 10)

NBA_2K_API_URL = "https://api.nba2kapi.com/api/public/players"
NBA_2K_PAGE_LIMIT = 100
# the endpoint allows 60 requests/minute per IP.
NBA_2K_REQUEST_DELAY_SECONDS = 1.1
NBA_2K_RETRY_DELAY_SECONDS = 2.0
NBA_2K_MAX_ATTEMPTS = 4
# curr = current NBA rosters, class = classic teams, allt = all-time teams.
NBA_2K_TEAM_TYPES = ("curr", "class", "allt")
NBA_2K_DEFAULT_TEAM_TYPES = "curr"

SEASON_TYPE_REGULAR = "Regular Season"
SEASON_TYPE_PLAYIN = "PlayIn"
SEASON_TYPE_PLAYOFFS = "Playoffs"
SEASON_TYPE_UNKNOWN = "Unknown"

# the game-log endpoints answer one season type per request, so each is fetched
# in turn; playoff games are the latest form a player shows before a new season.
SEASON_TYPES_INGESTED = (SEASON_TYPE_REGULAR, SEASON_TYPE_PLAYIN, SEASON_TYPE_PLAYOFFS)

# no play-in or playoff game is ever dated before april, so earlier runs skip
# those requests instead of paying the delay for an empty answer.
POSTSEASON_EARLIEST_MONTH = 4

# stats.nba.com revises box scores after the fact, so every incremental run
# re-reads this many days behind the stored watermark.
GAME_LOG_CORRECTION_WINDOW_DAYS = 3

GAME_STATUS_RECENT_WINDOW_DAYS = 10
GAME_STATUS_MAX_GAMES_PER_RUN = 40

BACKFILL_GAME_LOGS_DEFAULT_FROM_SEASON = "2022-23"

GAME_ID_PREFIX_TO_SEASON_TYPE = {
    "001": "Pre Season",
    "002": "Regular Season",
    "003": "All Star",
    "004": "Playoffs",
    "005": "PlayIn",
}

VALIDATION_POINTS_TOLERANCE = 1
VALIDATION_MAX_EXAMPLES = 15

TRUTH_LAYER_TABLES = (
    "nba_schedule",
    "player_game_logs",
    "team_game_logs",
    "player_game_status",
    "player_team_stints",
    "player_injury_reports",
    "ingestion_runs",
)

# a game-log stint is an observation, a snapshot stint a declaration; the source
# label is how a query tells them apart.
ROSTER_SNAPSHOT_SOURCE = "roster_snapshot"
ROSTER_WEB_SOURCE = "nba_web_players"

# stats.nba.com tarpits requests that lack a current browser fingerprint,
# including the Sec-Fetch-* headers; nba_api's defaults are too old.
STATS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}
STATS_PROBE_URL = "https://stats.nba.com/stats/commonteamroster"
STATS_PROBE_TIMEOUT_SECONDS = 10

NBA_WEB_GAMES_URL = "https://www.nba.com/games"
NBA_WEB_PLAYERS_URL = "https://www.nba.com/players"
NBA_WEB_TIMEOUT_SECONDS = 30
NBA_WEB_PAGE_DELAY_SECONDS = 1.5
NBA_WEB_SCHEDULE_DAYS_BACK = 3
NBA_WEB_SCHEDULE_DAYS_AHEAD = 21
ROSTER_SNAPSHOT_REQUEST_DELAY_SECONDS = BACKFILL_REQUEST_DELAY_SECONDS

# the league's official injury report. a slot with no report returns 403, not 404.
NBA_INJURY_REPORT_BASE_URL = "https://ak-static.cms.nba.com/referee/injury/"
NBA_INJURY_REPORT_SLOT_MINUTES = 15
NBA_INJURY_REPORT_TIMEOUT_SECONDS = 20
NBA_INJURY_REPORT_PROBE_DELAY_SECONDS = 0.2
NBA_INJURY_REPORT_SOURCE = "nba_official"
ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)
ESPN_TIMEOUT_SECONDS = 15
ESPN_MAX_ATTEMPTS = 3
ESPN_RETRY_DELAY_SECONDS = 2.0
# matches the backend odds board window: today through today+2, eastern.
ODDS_WINDOW_DAYS = 2
ODDS_SNAPSHOT_SOURCE = "espn_scoreboard"
ODDS_INGESTION_KIND = "odds_snapshot"

# player prop odds. the odds api bills an event-odds call per market per
# region, so eight markets cost eight of the free tier's 500 monthly credits.
PROPS_API_KEY_ENV = "ODDS_API_KEY"
PROPS_PROVIDER_THE_ODDS_API = "the_odds_api"
PROPS_INGESTION_KIND = "prop_odds_snapshot"
THE_ODDS_API_SPORT_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"
THE_ODDS_API_REGIONS = "us"
THE_ODDS_API_TIMEOUT_SECONDS = 20
THE_ODDS_API_MAX_ATTEMPTS = 3
THE_ODDS_API_RETRY_DELAY_SECONDS = 2.0
PROPS_MARKET_MAP = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_threes": "fg3m",
    "player_points_rebounds_assists": "pra",
    "player_steals": "stl",
    "player_blocks": "blk",
    "player_turnovers": "tov",
}
PROPS_WINDOW_DAYS = ODDS_WINDOW_DAYS
# the odds lane fires every 30 minutes; without this gap one day of it would
# spend the whole monthly quota.
PROPS_MIN_HOURS_BETWEEN_RUNS = 20
