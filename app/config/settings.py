from datetime import time
import MetaTrader5 as mt5


class Config:
    # === Symbols ===
    MAX_SYMBOLS = 10
    SYMBOLS = ["EURUSD"]

    # === Timeframes (system + MTF signal gating) ===
    TIMEFRAME = mt5.TIMEFRAME_M1
    TF_ENTRY = mt5.TIMEFRAME_M1
    TF_CONFIRM = mt5.TIMEFRAME_M5
    TF_BIAS = mt5.TIMEFRAME_M15

    # === Daily limits ===
    DAILY_TARGET_PROFIT = 200
    DAILY_MAX_RISK_PERCENT = 2

    SESSION_START_TIME = time(hour=0, minute=0)
    SESSION_END_TIME = time(hour=22, minute=0)

    # === SL/TP defaults (used by broker SL/TP placement, not by ExitTrade) ===
    DEFAULT_SL_PIPS: float = 2.0
    DEFAULT_TP_PIPS: float = 50.0
    STAGNATION_EXIT_MINUTES = 3
    MIN_SL_PIPS: float = 5.0

    # ============================================================
    # === EXIT STRATEGY (TICK-DRIVEN ONLY) ===
    # ============================================================

    # --- Loss protection (tick-driven) ---
    EXIT_MAX_LOSS_MONEY: float = 10.0
    EXIT_MAX_LOSS_PRICE: float = 0.0
    EXIT_MAX_LOSS_PIPS: float = 0.0
    EXIT_SOFT_SL_MONEY_GRACE_TICKS: int = 5

    # --- Early exit rules ---
    EXIT_ON_FIRST_TICK_NOT_FAVORABLE: bool = False  # Optional, usually off

    # --- Break-even arming ---
    EXIT_BE_DISTANCE_PIPS: float = 0.5
    EXIT_BE_ARMING_TICKS: int = 10

    # --- Profit management (tick-driven trailing only) ---
    EXIT_MIN_PROFIT_PIPS: float = 0.0
    EXIT_BUFFER_PIPS: float = 0.2
    EXIT_EPS_PIPS: float = 0.0
    EXIT_BUFFER_START_TICK: int = 1
    EXIT_TRAIL_START_PIPS: float = 2.0
    EXIT_TRAIL_DISTANCE_PIPS: float = 1
    EXIT_BUFFER_TICK_LIMIT: int = 10
    EXIT_STALE_TICK_LIMIT: int = 20
    EXIT_EXTRA_REVERSAL_GUARD_PIPS: float = 0.5

    # --- HTF gating for profit exits ---
    EXIT_HTF_FILTER_ENABLED: bool = False
    EXIT_HTF_STALE_SECONDS: int = 180
    EXIT_HTF_USE_M15: bool = True
    EXIT_HTF_USE_M5: bool = True

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

    # === M1 signal-quality filters ===
    USE_CLOSED_CANDLES_ONLY: bool = True
    DROP_LAST_CANDLE_ALWAYS: bool = False

    # ATR momentum gate (reduces "signal then instant reverse")
    ENTRY_ATR_PERIOD: int = 0
    ENTRY_ATR_MOVE_MULT: float = 0

    # Spread gate (0 disables). Requires candles include spread_points (preferred) or spread.
    MAX_SPREAD_POINTS: float = 0

    # Enable liquidity check after n-tick confirmation (recommended: True)
    LIQUIDITY_CHECK_AFTER_NTICK = True

    USE_MULTI_TIMEFRAME_SIGNALS = False

    USE_N_TICK_CONFIRMATION = True
    N_TICK_CONFIRMATION = 3

    MAGIC_NUMBER: int = 123456
    MAX_DEVIATION: int = 5

    LOT_SIZE: float = 0.01
    DEFAULT_LOT: float = 0.01
    MIN_LOT: float = 0.01
