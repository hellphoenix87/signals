from datetime import time
import MetaTrader5 as mt5


class Config:
    # === Symbols ===
    MAX_SYMBOLS = 10
    SYMBOLS = ["EURUSD", "USDJPY"]

    # === Timeframes (system + MTF signal gating) ===
    # Collector/orchestrator base timeframe (entry loop cadence)
    TIMEFRAME = mt5.TIMEFRAME_M1

    # Multi-timeframe signal strategy (bias/confirm/entry)
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
    # EXIT STRATEGY (HYBRID)
    # - Tick-driven: protective exits (soft SL / early abort)
    # - M1 candle-close: profit exits (reversal-in-profit / trailing buffer)
    # ============================================================

    # --- Loss protection (tick-driven) ---
    # Exit when floating profit <= -EXIT_MAX_LOSS_MONEY (account currency)
    EXIT_MAX_LOSS_MONEY: float = 10.0

    # Disable other loss modes unless you want them
    EXIT_MAX_LOSS_PRICE: float = 0.0
    EXIT_MAX_LOSS_PIPS: float = 0.0

    # Grace period for money soft-SL (avoid instant spread-trigger exits right after entry)
    EXIT_SOFT_SL_MONEY_GRACE_TICKS: int = 5

    # "first tick not favorable" is usually too noisy; keep off
    EXIT_ON_FIRST_TICK_NOT_FAVORABLE: bool = False
    EXIT_ON_FIRST_PROFIT_TICK: bool = False  # unused in current implementation

    # Early-abort (tick): after N ticks, if still never favorable and down >= X pips -> exit
    EXIT_EARLY_ABORT_ENABLED: bool = True
    EXIT_EARLY_ABORT_TICKS: int = 5
    EXIT_EARLY_ABORT_LOSS_PIPS: float = 2.0

    # Early-abort: minimum favorable move (in pips) to disable early-abort logic
    EXIT_EARLY_ABORT_MIN_FAV_PIPS: float = 1.0

    # --- Profit exits mode switches (HYBRID) ---
    # IMPORTANT: we want profit exits on candle close, NOT tick noise
    EXIT_PROFIT_EXITS_ON_TICK: bool = False
    EXIT_PROFIT_EXITS_ON_CANDLE_CLOSE: bool = True

    # Profit threshold: require real profit before profit-exit rules can trigger
    # (0.0 is extremely aggressive because any tiny green triggers exits)
    EXIT_MIN_PROFIT_PIPS: float = 2.0

    # Reversal exit (now evaluated on candle close when hybrid is enabled)
    EXIT_ON_FIRST_REVERSAL_IN_PROFIT: bool = True
    EXIT_TREAT_FLAT_AS_REVERSAL: bool = False

    # Trailing buffer exit (profit exit)
    EXIT_BUFFER_PIPS = 2.0  # 0.5p is extremely tight for live ticks/1m noise
    EXIT_EPS_PIPS: float = 0.0

    # Start trailing after N observations
    # - used by tick mode (mostly irrelevant if EXIT_PROFIT_EXITS_ON_TICK=False)
    EXIT_BUFFER_START_TICK = 3
    # - used by candle-close mode (new)
    EXIT_BUFFER_START_CANDLE: int = 2

    # --- Optional HTF gating for profit exits (recommended with MTF entries) ---
    # Blocks ONLY profit-taking exits (reversal/buffer) while HTF still supports the trade.
    # Protective exits (soft SL / early abort) are NOT blocked.
    EXIT_HTF_FILTER_ENABLED: bool = True
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
    ENTRY_ATR_PERIOD: int = 14
    ENTRY_ATR_MOVE_MULT: float = 0.15  # increase to 0.30–0.50 to filter more

    # Spread gate (0 disables). Requires candles include spread_points (preferred) or spread.
    MAX_SPREAD_POINTS: float = 0.0
