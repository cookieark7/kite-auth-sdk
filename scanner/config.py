"""Constants for the scanner validation test. Constants only — no logic."""

from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from kite_auto.models import CandleInterval

SESSION_START = time(9, 15)
SESSION_END = time(15, 30)
OPEN_WINDOW_END = time(9, 20)  # close of the first 5-minute bar
EVAL_START = time(9, 20)
EVAL_END = time(15, 15)

INTERVAL = CandleInterval.MINUTE_5
BARS_PER_DAY = 75
MIN_BARS_PER_DAY = 70

RVOL_LOOKBACK = 14
ADR_LOOKBACK = 20
WARMUP_SESSIONS = 20
MIN_PRIOR_SESSIONS = 10

SPLIT_GAP_THRESHOLD = 0.20
SPLIT_QUARANTINE_SESSIONS = 2  # sessions dropped after a suspected corporate action
MAX_DAYS_PER_REQUEST = 100  # Kite cap for 5minute
RATE_LIMIT_PER_SEC = 3.0  # historical endpoint

DECILES = 10
MAX_DROP_FRACTION = 0.05  # warn above this share of stock-days removed by cleaning

IST = ZoneInfo("Asia/Kolkata")
DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw"
UNIVERSE_SYMBOLS_FILE = DATA_DIR / "universe_symbols.txt"
UNIVERSE_CACHE_FILE = DATA_DIR / "universe.parquet"
FEATURES_FILE = DATA_DIR / "features.parquet"
