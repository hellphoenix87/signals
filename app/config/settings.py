from datetime import time
import MetaTrader5 as mt5


class Config:
    # === Symbols ===
    MAX_SYMBOLS = 10
    SYMBOLS = ["EURUSD"]

    # === Timeframe ===
    TIMEFRAME = mt5.TIMEFRAME_M1

    # === Daily limits ===
    DAILY_TARGET_PROFIT = 200
    DAILY_MAX_RISK_PERCENT = 2

    SESSION_START_TIME = time(hour=0, minute=0)
    SESSION_END_TIME = time(hour=22, minute=0)

    # === SL/TP defaults ===
    DEFAULT_SL_PIPS: float = 2.0
    DEFAULT_TP_PIPS: float = 50.0
    STAGNATION_EXIT_MINUTES = 3
    MIN_SL_PIPS: float = 5.0

    # === Exit strategy (money soft SL) ===
    # Exit when floating profit <= -EXIT_MAX_LOSS_MONEY (account currency)
    EXIT_MAX_LOSS_MONEY: float = 10.0
    # Disable other loss modes unless you want them
    EXIT_MAX_LOSS_PRICE: float = 0.0
    EXIT_MAX_LOSS_PIPS: float = 0.0
    EXIT_SOFT_SL_MONEY_GRACE_TICKS: int = 5

    # Tick-based exit behavior
    EXIT_ON_FIRST_TICK_NOT_FAVORABLE: bool = False
    EXIT_ON_FIRST_PROFIT_TICK: bool = False
    EXIT_MIN_PROFIT_PIPS: float = 0.0

    # Early-abort (M1-friendly): after N ticks, if still not favorable and down >= X pips -> exit
    EXIT_EARLY_ABORT_ENABLED: bool = True
    EXIT_EARLY_ABORT_TICKS: int = 5
    EXIT_EARLY_ABORT_LOSS_PIPS: float = 2.0

    EXIT_ON_FIRST_REVERSAL_IN_PROFIT: bool = True
    EXIT_TREAT_FLAT_AS_REVERSAL: bool = False

    # Buffer / trailing backup
    EXIT_BUFFER_PIPS = 0.5
    EXIT_BUFFER_START_TICK = 3
    EXIT_EPS_PIPS: float = 0.0

    # === Breakout settings (if used elsewhere) ===
    OPENING_RANGE_PERIOD = 1
    BREAKOUT_BUFFER_PIPS = 0.5

    # === Risk management ===
    LOT_RISK_PERCENT = 1

    # === Logging ===
    LOG_FILE = "trading_bot.log"
    LOG_LEVEL = "INFO"

    # === Data / indicators ===
    CANDLE_COUNT = 2000
    MIN_CANDLES_FOR_INDICATORS = 202
    CONFIDENCE_THRESHOLD = 0.5

    # === M1 signal-quality filters (STRONGLY recommended) ===
    # Use only closed candles for indicators/signals
    USE_CLOSED_CANDLES_ONLY: bool = True
    # Most M1 feeds include a forming candle at the end; drop it to avoid false signals
    DROP_LAST_CANDLE_ALWAYS: bool = False

    # ATR momentum gate (reduces "signal then instant reverse")
    ENTRY_ATR_PERIOD: int = 14
    ENTRY_ATR_MOVE_MULT: float = 0.20  # increase to 0.30–0.50 to filter more

    # Spread gate (0 disables). Requires candles include spread_points (preferred) or spread.
    MAX_SPREAD_POINTS: float = 0.0

    OCO_ENABLED: bool = True
    OCO_FALLBACK_TO_MARKET: bool = True

    # Distance from current price to place BUY STOP / SELL STOP
    OCO_OFFSET_PIPS: float = 2.0

    # Cancel both pending orders if not filled within this time
    OCO_EXPIRY_SECONDS: int = 120

    MAGIC: int = 123456
    DEVIATION: int = 5
    FILLING_MODE: int = 0
