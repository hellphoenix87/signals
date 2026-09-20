from datetime import time
import MetaTrader5 as mt5


class Config:
    """Every tunable for this bot, read elsewhere via `getattr(Config, "NAME", default)`.

    Grouped by concern: symbols/timeframes, daily limits, SL/TP defaults,
    risk management, logging, data/indicator sizing, strategy feature
    flags (M1 signal-quality filters, ATR momentum gate, spread gate,
    multi-timeframe strategy, N-tick confirmation), MT5 order parameters,
    tick-driven exit strategy, and HTF gating for profit exits.
    """

    MAX_SYMBOLS = 10
    SYMBOLS = ["EURUSD"]

    TIMEFRAME = mt5.TIMEFRAME_M1
    TF_ENTRY = mt5.TIMEFRAME_M1
    TF_CONFIRM = mt5.TIMEFRAME_M5
    TF_BIAS = mt5.TIMEFRAME_M15

    DAILY_TARGET_PROFIT = 200
    DAILY_MAX_RISK_PERCENT = 2

    SESSION_START_TIME = time(hour=0, minute=0)
    SESSION_END_TIME = time(hour=22, minute=0)

    DEFAULT_SL_PIPS: float = 5.0
    DEFAULT_TP_PIPS: float = 8.0
    STAGNATION_EXIT_MINUTES = 3
    MIN_SL_PIPS: float = 5.0

    LOT_RISK_PERCENT = 1

    # This demo account's actual balance ($100,000) is unrealistically
    # large and produces proportionally-correct but oversized lots
    # (~20 lots at LOT_RISK_PERCENT=1%, DEFAULT_SL_PIPS=5). Size against
    # this instead of the live balance so lot sizes reflect a realistic
    # account. Set to None to fall back to the live account balance.
    RISK_SIZING_BALANCE_OVERRIDE: float | None = 1000.0

    LOG_FILE = "trading_bot.log"
    LOG_LEVEL = "INFO"

    CANDLE_COUNT = 2000
    MIN_CANDLES_FOR_INDICATORS = 202
    CONFIDENCE_THRESHOLD = 0.5

    ENTRY_SMA_SHORT_WINDOW: int = 5
    ENTRY_SMA_LONG_WINDOW: int = 20
    ENTRY_RSI_PERIOD: int = 7

    ENTRY_MACD_WEIGHT: float = 1.0
    ENTRY_SMA_WEIGHT: float = 1.0
    ENTRY_RSI_WEIGHT: float = 2.5

    # Config-switchable alternative to the hand-coded vote above -- see
    # docs/test-results/ml-entry-model-comparison.md. Off by default:
    # opt-in only, since it needs a trained model file to exist.
    USE_ML_ENTRY_MODEL: bool = False
    ML_MODEL_PATH: str = "models/entry_signal_model.joblib"

    USE_CLOSED_CANDLES_ONLY: bool = True
    DROP_LAST_CANDLE_ALWAYS: bool = False

    ENTRY_ATR_PERIOD: int = 0
    ENTRY_ATR_MOVE_MULT: float = 0

    MAX_SPREAD_POINTS: float = 0

    USE_MULTI_TIMEFRAME_SIGNALS = True
    MTF_BIAS_SMA_SHORT: int = 10
    MTF_BIAS_SMA_LONG: int = 50
    MTF_ADX_PERIOD: int = 14
    MTF_ADX_MIN_STRENGTH: float = 20.0
    MTF_BIAS_WEIGHT: float = 0.5
    MTF_CONFIRM_WEIGHT: float = 0.3
    MTF_ENTRY_WEIGHT: float = 0.2
    MTF_SCORE_THRESHOLD: float = 0.6

    USE_N_TICK_CONFIRMATION = True
    N_TICK_CONFIRMATION = 1
    LIQUIDITY_CHECK_AFTER_NTICK = True

    # Backtest found MTF badly underperforms during 08:00-18:59 UTC
    # (London open through the NY session) across two independent
    # windows -- see docs/test-results/session-filter-analysis.md. No
    # effect found for single-timeframe, so this is harmless there.
    USE_SESSION_FILTER: bool = True
    # Per-symbol blocked-hours windows. EURUSD's 08-18 UTC block does NOT
    # transfer to other pairs -- docs/test-results/session-filter-per-pair-analysis.md
    # found a reproducibly *opposite* effect for AUDUSD/USDJPY, no effect
    # for USDCAD, and noise for USDCHF. A symbol with no entry here gets
    # no session filtering at all: inheriting EURUSD's window would be
    # actively wrong for at least 2 of the 6 other pairs checked, so "no
    # filter" is the only defensible default for an un-derived pair.
    SESSION_FILTER_BLOCKED_HOURS_UTC_BY_SYMBOL: dict[str, list[int]] = {"EURUSD": list(range(8, 19))}
    # None = auto-detect live via a real MT5 tick vs. true UTC (see
    # signal_generation.get_broker_utc_offset_hours) -- depends on both
    # the local machine's timezone and the broker server's, so hardcoding
    # it would silently drift wrong across a DST change or a new
    # deployment machine. Pin an int only for offline/deterministic tests
    # without an MT5 connection.
    SESSION_FILTER_UTC_OFFSET_HOURS = None

    MAGIC_NUMBER: int = 123456
    MAX_DEVIATION: int = 5
    LOT_SIZE: float = 0.01
    DEFAULT_LOT: float = 0.01
    MIN_LOT: float = 0.01

    EXIT_MAX_LOSS_MONEY: float = 5.0
    EXIT_MAX_LOSS_PRICE: float = 0.0
    EXIT_MAX_LOSS_PIPS: float = 0.0
    # Post-breakeven loss cap (Thread 4, docs/exit-strategy-open-threads.md):
    # once a position has armed breakeven, force-close if profit reverses to
    # -this value or below. Was hardcoded to -5 in LossExitManager,
    # independent of EXIT_MAX_LOSS_MONEY (the pre-breakeven cap) despite
    # sharing the same number by coincidence. First retuned to $3 (isolated
    # sweep, before Thread 3 was wired -- docs/test-results/post-breakeven-
    # loss-cap.md), then to $1 after the first full-lifecycle backtest (real
    # trail + real cap together) found the real trail's average win ($1.44)
    # is less than half of $3's average loss ($3.16) -- $1 beat every other
    # candidate tested ($0/$1/$1.5/$2/$2.5/$3), pooled and on EURUSD alone --
    # see docs/test-results/full-lifecycle-backtest.md.
    #
    # TEMPORARY, not a final answer: even at this best-tested value, the
    # full-lifecycle backtest is still net negative overall (-$852.83
    # pooled, -$23.80 EURUSD-only, both at $1). Exit-side tuning (this
    # value and Thread 3's trail shape) has been swept about as far as
    # bounded candidate sweeps can take it without turning the system
    # profitable -- the real lever left is entry-signal quality (Threads 1/2,
    # still open), not further retuning this number.
    EXIT_POST_BE_LOSS_CAP_MONEY: float = 1.0
    EXIT_SOFT_SL_MONEY_GRACE_TICKS: int = 5
    EXIT_ON_FIRST_TICK_NOT_FAVORABLE: bool = False
    EXIT_BE_DISTANCE_PIPS: float = 0.5
    EXIT_BE_ARMING_TICKS: int = 90
    EXIT_MIN_PROFIT_PIPS: float = 0.0
    EXIT_BUFFER_PIPS: float = 0.2
    EXIT_EPS_PIPS: float = 0.0
    EXIT_BUFFER_START_TICK: int = 1
    # Post-breakeven trailing-profit gap (Thread 3, docs/exit-strategy-open-
    # threads.md): once a position has armed breakeven, the exit trigger
    # trails at best_profit_seen - gap, where
    # gap = max(EXIT_TRAIL_GAP_FLOOR_MONEY, EXIT_TRAIL_GAP_PCT * best_profit).
    # The trail only actively enforces once that trigger is positive (peak
    # has grown past its own gap) -- a small peak leaves the trail inactive
    # rather than force-exiting the moment the trigger math goes negative.
    # Was hardcoded to a flat $0.04 pullback (ProfitExitManager), which a
    # pooled 7-pair x 3-window backtest showed fires on ordinary tick noise
    # (real median drawdown-from-peak $6.49) rather than ever functioning as
    # a real trail. This percentage-of-peak shape (60% giveback, $2 floor)
    # was the best of 5 candidates tested -- see
    # docs/test-results/post-breakeven-trail-simulation.md.
    EXIT_TRAIL_GAP_FLOOR_MONEY: float = 2.0
    EXIT_TRAIL_GAP_PCT: float = 0.6
    EXIT_BUFFER_TICK_LIMIT: int = 10
    EXIT_STALE_TICK_LIMIT: int = 20
    EXIT_EXTRA_REVERSAL_GUARD_PIPS: float = 0.5

    EXIT_HTF_FILTER_ENABLED: bool = False
    EXIT_HTF_STALE_SECONDS: int = 180
    EXIT_HTF_USE_M15: bool = True
    EXIT_HTF_USE_M5: bool = True
