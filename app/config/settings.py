from datetime import time
import MetaTrader5 as mt5


class Config:
    # === Symbols ===
    MAX_SYMBOLS = 10  # Maximum number of symbols to trade
    SYMBOLS = ["EURUSD"]

    # === Timeframe ===
    TIMEFRAME = TIMEFRAME = mt5.TIMEFRAME_M5  # Analysis timeframe (5-minute candles)

    # === Daily limits ===
    DAILY_TARGET_PROFIT = 200  # Max profit per day in account currency
    DAILY_MAX_RISK_PERCENT = 2  # Max risk per day (% of account balance)

    SESSION_START_TIME = time(hour=0, minute=0)
    SESSION_END_TIME = time(hour=22, minute=0)

    # === Stop Loss / Take Profit Defaults ===
    DEFAULT_SL_PIPS = None  # None = calculate dynamically per trade
    DEFAULT_TP_PIPS = None  # None = calculate dynamically per trade
    STAGNATION_EXIT_MINUTES = 3  # Close trade if profit stagnant

    # === Breakout Settings ===
    OPENING_RANGE_PERIOD = 1  # Number of first candles to calculate opening range
    BREAKOUT_BUFFER_PIPS = 2  # Buffer beyond opening range for breakout

    # === Risk Management ===
    LOT_RISK_PERCENT = 1  # Risk per trade (% of account balance)

    # === Logging / Debugging ===
    LOG_FILE = "trading_bot.log"
    LOG_LEVEL = "INFO"

    CANDLE_COUNT = 10000
    MIN_CANDLES_FOR_INDICATORS = 202
    CONFIDENCE_THRESHOLD = 0.5
