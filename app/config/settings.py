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
    # Inert while False: MLSignalStrategy is still constructed and wired by
    # `strategy_factory`, but never wraps the base strategy.
    USE_ML_ENTRY_MODEL: bool = False
    ML_MODEL_PATH: str = "models/entry_signal_model.joblib"

    USE_CLOSED_CANDLES_ONLY: bool = True
    DROP_LAST_CANDLE_ALWAYS: bool = False

    # ATR momentum gate. Both must be > 0 for
    # `AtrMomentumFilteredSignalStrategy` to wrap the entry layer; 0/0 leaves
    # it built-but-inert.
    ENTRY_ATR_PERIOD: int = 0
    ENTRY_ATR_MOVE_MULT: float = 0

    # Entry spread gate (TradeExecutor._spread_ok), in MT5 POINTS -- EURUSD is
    # 5-digit, so 1 pip = 10 points. 0 disables it, which is how this shipped:
    # the gate existed and was wired into the entry path but never fired.
    #
    # 10 points = 1.0 pips, ~3x the 0.27-0.39 pip spread EURUSD normally shows
    # in the hours this bot trades. It targets the daily rollover window (UTC
    # hour 0), where spread reaches 8-14 pips = 80-140 points: at 0.2 lots that
    # marks a fresh position -$16 to -$28 on the spread alone, against a $5
    # (2.5 pip) pre-BE soft SL, so it is stopped out before price moves at all.
    # Measured: ~31% of pre-BE stop hits fire on TICK 1, 92% of those in that
    # window, averaging -$8.10 vs -$5.36 for genuine stops.
    #
    # Backtest, 12 DATE-PINNED windows (byte-reproducible), single-timeframe:
    # 12/12 windows improved, per-trade -$1.016 -> -$0.748.
    #
    # Set to 10, not 15, so live matches the threshold every pinned measurement
    # used. 15 was the original ship (more headroom, 11/12 on drifting windows),
    # but that left no pinned-window evidence for the value actually running --
    # reintroducing exactly the research-vs-production gap the pinned windows
    # were meant to close. Both block the 80-140 point rollover spikes that
    # motivated the gate; they differ only on moderate spreads, worth ~$0.026
    # per trade.
    # See docs/test-results/pre-be-phase-and-entry-signal-investigation.md
    MAX_SPREAD_POINTS: float = 10

    USE_MULTI_TIMEFRAME_SIGNALS = True
    MTF_BIAS_SMA_SHORT: int = 10
    MTF_BIAS_SMA_LONG: int = 50
    MTF_ADX_PERIOD: int = 14
    MTF_ADX_MIN_STRENGTH: float = 20.0
    MTF_BIAS_WEIGHT: float = 0.5
    MTF_CONFIRM_WEIGHT: float = 0.3
    MTF_ENTRY_WEIGHT: float = 0.2
    MTF_SCORE_THRESHOLD: float = 0.6
    # M1 entry-trigger indicator for the MTF path -- config-driven since the
    # docs/test-results/mtf-pullback-gate-direction-fix.md investigation
    # (was previously hardcoded to "macd" in strategy_factory). That
    # investigation's signal-quality-proxy testing initially favored "sma"
    # over "macd", but later full-lifecycle P&L testing in the same
    # investigation (docs/test-results/full-lifecycle-entry-indicator-comparison.md
    # onward) found the proxy didn't predict real P&L, and an extensive
    # exit-strategy loss-cap sweep (docs/test-results/cap-5-second-window-test.md)
    # never found a configuration where "sma" held up across two independent
    # windows -- its one promising result reversed from +$49.60 to -$299.00
    # between windows. Reverted to "macd" (the pre-investigation default)
    # pending the follow-up plan (docs/plans/done/test-additional-entry-indicators.md)
    # rather than ship a switch the same investigation ended up not trusting.
    MTF_ENTRY_INDICATOR: str = "macd"

    # N-tick entry confirmation. `strategy_factory` only wraps the base
    # strategy when N_TICK_CONFIRMATION > 1, so the flag alone does nothing --
    # it read `True` here for months alongside N_TICK_CONFIRMATION = 1, which
    # made the live bot look like it confirmed entries across ticks when it
    # never has. Both must be set to enable it: flag True AND the count > 1.
    USE_N_TICK_CONFIRMATION: bool = False
    N_TICK_CONFIRMATION: int = 1
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
    # Ticks a position gets to reach breakeven before LossExitManager closes it.
    #
    # 90 -> 30. Breakeven arms at median 0-11 ticks (p90 = 5-55), so only
    # 2.5-15.1% of trades arm after tick 30: the wall keeps nearly every trade
    # that ever works while killing non-starters 3x sooner. It also converts
    # expensive distance exits into cheap time ones -- on a high-volatility
    # window the $5 soft SL fired 340 times for -$2,005 at 90 ticks vs 170
    # times for -$1,015 at 30, and the time exit itself got cheaper (-$2.25 ->
    # -$1.63) because it fires before the trade has bled as far.
    #
    # Backtest, 12 date-pinned windows (see the canonical list in
    # docs/test-results/pre-be-phase-and-entry-signal-investigation.md):
    # stacked on the spread gate it wins 12/12 against the gate alone,
    # -$0.748 -> -$0.693 per trade. Standalone it was worth +$0.066; on top of
    # the gate it is +$0.055, since the gate already removes some trades this
    # would otherwise have cut late.
    #
    # Tested and rejected: 300 ticks and no timeout at all (2/4 -- released
    # trades do not reach their peak, they bleed to the $5 stop instead).
    EXIT_BE_ARMING_TICKS: int = 30
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

    # Staircase/ratchet post-breakeven trail, replacing the percentage-of-peak
    # trail above when enabled: as the running peak crosses each
    # EXIT_STAIRCASE_TIER_WIDTH increment, that tier becomes the new stop, so
    # giveback is capped at just under one tier however large the peak gets --
    # unlike the percentage trail, whose giveback grows with the peak (60% of
    # it). The EXIT_TRAIL_GAP_* settings above are NOT dead: they are what runs
    # when EXIT_STAIRCASE_TRAIL_ENABLED is False.
    #
    # Confirmed on 4/4 windows at the live $1 post-BE cap -- W1/W2 in
    # docs/test-results/exit-strategy-reactive-and-staircase-investigation.md,
    # W3/W4 in docs/test-results/staircase-trail-w3-w4-confirmation.md. It
    # reduces bleed; it does not make the system profitable (every window is
    # still net negative, and the post-BE cap is where the money goes).
    EXIT_STAIRCASE_TRAIL_ENABLED: bool = True
    EXIT_STAIRCASE_TIER_WIDTH: float = 2.0
    # Activation threshold for the FIRST tier. 0 means "same as tier width"
    # (tiers at $2/$4/$6...), which is the validated shape. Setting this lower
    # to engage the trail earlier was tested and was WORSE: first tier at $0.5
    # gave W1 -$646 -> -$846 and W2 -$630 -> -$789, despite a higher win rate.
    EXIT_STAIRCASE_FIRST_TIER: float = 0.0
    EXIT_BUFFER_TICK_LIMIT: int = 10
    EXIT_STALE_TICK_LIMIT: int = 20
    EXIT_EXTRA_REVERSAL_GUARD_PIPS: float = 0.5

    EXIT_HTF_FILTER_ENABLED: bool = False
    EXIT_HTF_STALE_SECONDS: int = 180
    EXIT_HTF_USE_M15: bool = True
    EXIT_HTF_USE_M5: bool = True
