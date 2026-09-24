"""Backtest the pre-breakeven soft-SL layer of the REAL exit strategy
(`LossExitManager.check_exit_on_tick`) against real historical ticks --
not the signal-quality target/stop proxy `backtest_signals.py` uses.

Full exit-strategy-aware backtesting has been deliberately deferred
throughout this project (`TradingMode.BACKTEST`'s mark-to-market is
still a stub). This script doesn't attempt the full lifecycle -- it
answers one specific, narrower question: for real entries from the
live signal generator, how does the pre-breakeven phase actually
resolve once real historical ticks are fed through the real,
production `LossExitManager`?

For each buy/sell signal from `strategy_factory(config=Config)` (MTF,
the live default), opens a simulated position at the next real tick's
ask/bid after the signal's candle closes, and replays real historical
ticks (`mt5.copy_ticks_from`) through the real `LossExitManager`,
tracking one of four outcomes for the pre-breakeven phase:

  - "soft_sl"    -- Config.EXIT_MAX_LOSS_MONEY/_PRICE/_PIPS fired
                    (reason="profit_drop")
  - "timed_out"  -- the EXIT_BE_ARMING_TICKS window expired without
                    reaching break-even (reason="failed_to_reach_be") --
                    always a small loss strictly between the soft-SL
                    threshold and $0, since a tick actually crossing the
                    soft-SL would already have triggered "soft_sl" first
  - "reached_be" -- break-even armed within the window (no action
                    returned; profit-taking/trailing logic would take
                    over from here -- not simulated by this script)
  - "exhausted"  -- ran out of fetched ticks before either of the above
                    resolved (a data-availability limit of this script,
                    not a real outcome)

`--counterfactual` answers the natural follow-up: for every trade the
soft SL or the breakeven-timeout cut short, was that actually a good
call? It continues replaying the *same* real tick stream from the
point of the real exit onward, as if only the broker-side wide SL
(`Config.DEFAULT_SL_PIPS`) existed -- no soft SL, no timeout -- and
records which happens first: price recovers to break-even
("would_have_recovered"), price reaches the broker's wide SL distance
("would_have_hit_broker_sl"), or neither within the extended tick
budget ("still_unresolved"). This isolates the value of the
pre-breakeven soft-SL/timeout layer specifically, using the exact same
price path both simulations saw, not a separate/resampled one.

`--disable-timeout` answers a narrower follow-up: not "remove the whole
pre-breakeven layer" (that's `--counterfactual`), but "remove just the
90-tick breakeven-timeout, keep the soft SL." Wires
`ExitTradeConfig(be_arming_ticks=0)` -- the real `LossExitManager`'s own
documented way to disable it -- so `timed_out` can never occur; every
other exit parameter (soft SL, HTF gating) stays at its real `Config`
value.

`--measure-post-be` is purely observational, not a rule test: for every
`reached_be` trade, continues watching the same real tick stream PAST
arming with NO exit logic applied at all, tracking the running peak
profit and the largest pullback ever seen from *whatever the running
peak was at that moment* (not just the final peak -- that's the number
a trailing gap actually has to survive without getting stopped out by
ordinary noise before a new high is made). Exists to answer "what
should the post-breakeven trailing gap actually be sized at," grounded
in this account's real price action instead of a guessed constant --
see the code review finding that `ProfitExitManager`'s real trailing
gap is a hardcoded $0.04, disconnected from `Config` entirely.

`--simulate-trail` takes the next step past `--measure-post-be`: for
every `reached_be` trade, replays a fixed set of *actual* candidate
trailing-gap rules (informed by `--measure-post-be`'s pooled
percentiles) against the same tick stream in a single pass, and
records what each rule would have actually captured -- not just the
noise floor a rule has to survive, but real simulated P&L per rule.
Rules are a giveback allowance from the running peak profit (flat
dollar, or a floor-protected percentage of peak); a rule only starts
actively enforcing once peak has grown past its own gap (trigger =
peak - gap(peak) > 0) -- before that, the trail stays inactive rather
than forcing an exit the moment a small peak's trigger math goes
negative (answers open sub-question (a) from
docs/exit-strategy-open-threads.md Thread 3: yes, there's an implicit
minimum-profit floor before the trail engages). A reversal below
breakeven before the trail ever engages is Thread 4's (the post-BE
loss cap's) territory, not simulated here.

`--full-lifecycle` is the odd one out from everything above: instead of
stopping at breakeven or reimplementing candidate rules independently,
it replays real ticks through the actual, complete `ExitTrade` --
`exit_trade._loss_manager.check_exit_on_tick` THEN (if no loss action)
`exit_trade._profit_manager.check_exit_on_tick`, the same precedence
`ExitTrade.on_tick` itself uses, gated the same way on
`config.profit_exits_on_tick` -- from entry to a genuine final exit, one
`PosState` carrying continuously across the whole trade with no phase
boundary. This is the first backtest in this project to report a real,
single, complete realized P&L per trade under the system as it actually
exists today (mutually exclusive with `--counterfactual`/
`--disable-timeout`/`--measure-post-be`/`--simulate-trail`, which are all
phase-based).

`--sweep-pre-be-threshold` answers Thread 2 (docs/exit-strategy-open-
threads.md): instead of the single Config-configured pre-breakeven
soft-SL threshold, replays every candidate in PRE_BE_THRESHOLD_CANDIDATES
(each a real LossExitManager built with a different max_loss_money,
everything else at Config's real values) against the same tick stream in
one pass -- so a threshold conditional on M5-confirm state can be tested
against the real system, not guessed from single-threshold data. Pre-BE
phase only; mutually exclusive with --full-lifecycle.

Usage:
    pipenv run python scripts/backtest_exit_strategy.py [--symbol EURUSD]
        [--weeks 4] [--start-pos 1] [--lot 0.2] [--max-ticks-per-trade 500]
        [--counterfactual] [--counterfactual-max-ticks 3000]
        [--disable-timeout] [--measure-post-be] [--simulate-trail]
        [--post-be-max-ticks 3000]
        [--full-lifecycle] [--full-lifecycle-max-ticks 4000]
        [--sweep-pre-be-threshold]

Per-trade results are written to a CSV under `backtest_results/` for
offline analysis; the aggregate summary is printed to stdout.
"""

import argparse
import bisect
import contextlib
import csv
import datetime
import io
import logging
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

import MetaTrader5 as mt5

from app.config.settings import Config
from app.data.market_data import MarketData
from app.signals.signal_generation import strategy_factory, build_indicator
from app.signals.indicators.rsi import _compute_latest_rsi
from app.signals.indicators.macd import _macd_histogram
from app.signals.indicators.atr import calculate_atr

_metadata_logger = logging.getLogger("backtest_exit_strategy.metadata")
from app.trade_execution.broker import Broker
from app.trade_execution.mode import TradingMode
from app.risk.risk_manager import create_risk_manager
from app.exit_strategies.exit_trade import create_exit_trade, ExitTradeConfig
from app.exit_strategies.exit_shared import PosState

from scripts.backtest_signals import fetch_history, TF_SECONDS

RESULTS_DIR = Path(__file__).resolve().parent.parent / "backtest_results"
M1_BARS_PER_TRADING_WEEK = 5 * 24 * 60

# Candidate post-breakeven trailing-gap rules for --simulate-trail, informed
# by the pooled --measure-post-be percentiles (median drawdown-from-peak
# $6.49, p25 $4.70, p75 $9.55) gathered across 7 pairs x 3 windows. Each maps
# the running peak profit to a giveback allowance (gap); trigger = peak -
# gap, floored at $0 (Thread 4's post-BE loss cap owns anything below that).
TRAIL_RULES: list[tuple[str, Any]] = [
    ("flat5", lambda peak: 5.0),
    ("flat7", lambda peak: 7.0),
    ("flat9", lambda peak: 9.0),
    ("pct40_floor2", lambda peak: max(2.0, 0.4 * peak)),
    ("pct60_floor2", lambda peak: max(2.0, 0.6 * peak)),
]

# Provisional pick among TRAIL_RULES (per-conversation decision, not yet
# validated against the real loss-manager-combined full sweep or the actual
# live $0.04 rule) -- kept only for report labeling. All 5 candidates still
# run side by side in every --simulate-trail sweep for comparison; this is
# not wired into ProfitExitManager/Config.
ACTIVE_TRAIL_RULE = "pct60_floor2"

# Thread 4 candidate values for LossExitManager's post-BE loss cap
# (`Config.EXIT_POST_BE_LOSS_CAP_MONEY`, app/exit_strategies/managers/
# loss.py) -- originally hardcoded to -5, now wired to Config and retuned
# to -3 after this candidate spread's pooled 7-pair x 3-window result
# (docs/test-results/post-breakeven-loss-cap.md: -$3 beat -$5 in 6/7
# pairs). -5.0 stays in the list as the pre-retune baseline for
# comparison. The spread is informed by the pooled worst-further-
# drawdown-after-cap percentiles measured this session (median -$7.96,
# avg -$10.14, p10 -$16.44).
CAP_THRESHOLDS: list[float] = [-3.0, -5.0, -7.0, -10.0, -15.0]

# Retune candidates for --full-lifecycle --sweep-post-be-cap: the first
# full-lifecycle backtest (post Thread 3+4 wiring) found the real
# trailing-stop's average captured win ($1.44) is less than half the real
# post-BE cap's average loss ($3.16 at -3.0) -- CAP_THRESHOLDS above was
# swept in isolation, before Thread 3 was wired, so it never accounted for
# this. These candidates test tighter values against the REAL combined
# system (real trail + real cap together, only the cap value varies).
# 0.0 is the tightest possible value -- exits on the very first tick
# profit dips below $0 at all, zero tolerance (needed a real bug fix in
# loss.py first: `getattr(..., 5.0) or 5.0` was silently discarding a
# deliberate 0.0). Retuned to 1.0 (see docs/test-results/full-lifecycle-
# backtest.md) -- but at 1.0, most cap-hit trades resolve in ~200 ticks,
# too short a window for any recovery-predicting signal (indicator or
# timing-based) to show up, per a follow-up correlation check. 7.0/15.0
# added to test whether that signal reappears with more decision room --
# 7.0 as the "cut" value under test, 15.0 as a looser reference to label
# whether a 7.0-cut trade genuinely recovers (same technique used to
# validate 1.0 against 3.0 originally).
FULL_LIFECYCLE_CAP_CANDIDATES: list[float] = [0.0, 1.0, 1.5, 2.0, 2.5, 3.0, 7.0, 15.0, 20.0, 30.0, 50.0]

# Candidate tier widths for --sweep-staircase-tiers: $1.0 (the first
# staircase-trail run's arbitrary starting choice) bracketed on both
# sides to see whether a different width is more robust across windows.
# Extended past $2.0 (0.5/1.0/1.5/2.0 sweep result) since both windows
# were still improving monotonically at the top of that range, with no
# peak/reversal found yet.
STAIRCASE_TIER_CANDIDATES: list[float] = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]

# Thread 2 (docs/exit-strategy-open-threads.md): candidate values for the
# PRE-breakeven soft-SL money threshold (`Config.EXIT_MAX_LOSS_MONEY`,
# LossExitManager._pre_be_soft_sl_hit), tested for --sweep-pre-be-threshold
# to see whether a threshold conditional on M5-confirm state (whether
# RSI/M5 actually agrees with the trade direction) beats today's flat $5
# rule. 5.0 is today's live value, kept for direct comparison.
PRE_BE_THRESHOLD_CANDIDATES: list[float] = [2.0, 3.0, 4.0, 5.0, 7.0, 10.0]

# Days of extra candles fetched before a pinned window's start, so the first
# signals have full indicator history rather than a truncated warmup.
WARMUP_DAYS: int = 3

# --atr-normalize: bounds on the per-trade threshold scale factor. Fixed in
# advance (see docs/plans/in-progress/volatility-normalized-thresholds.md) so
# they can't be quietly tuned against results; an ATR far outside the baseline
# shouldn't drive the soft SL to ~0 or to an effectively disabled value.
ATR_SCALE_MIN: float = 0.5
ATR_SCALE_MAX: float = 3.0

# Thresholds scaled by --atr-normalize. All three are money values on
# ExitTradeConfig, so they scale with the same factor.
ATR_SCALED_FIELDS: tuple[str, ...] = (
    "max_loss_money",
    "post_be_loss_cap_money",
    "trail_gap_floor_money",
)


def _confirm_entry_tick(
    ticks,
    side: str,
    candle_close_price: float,
    n_ticks: int,
    max_wait_seconds: float = 60.0,
):
    """Index of the tick that completes n-tick confirmation, or None.

    Mirrors `NTickConfirmedSignalStrategy.on_new_tick` as it behaves after the
    flat-tick fix: the reference price starts at the signal candle's close and
    ratchets up on each favorable tick; an unfavorable (or flat) tick resets
    the counter and the reference. Live does a hard reset on every new M1
    candle, so confirmation that has not completed within `max_wait_seconds`
    of the candle close is abandoned rather than carried forward.
    """
    ref = float(candle_close_price)
    streak = 0
    t0 = None
    for i, t in enumerate(ticks):
        ts = float(t["time"])
        if t0 is None:
            t0 = ts
        elif ts - t0 > max_wait_seconds:
            return None
        price = float(t["bid"]) if side == "buy" else float(t["ask"])
        favorable = (price > ref) if side == "buy" else (price < ref)
        if favorable:
            streak += 1
            ref = price
            if streak >= n_ticks:
                return i
        else:
            streak = 0
            ref = float(candle_close_price)
    return None


def _parse_dt(value: Optional[str]) -> Optional[datetime.datetime]:
    """Parse a --start-date/--end-date value; None passes through."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise SystemExit(f"Could not parse date {value!r}; use YYYY-MM-DD or 'YYYY-MM-DD HH:MM'")


def _entry_spread_pips(tick, pip_size: float) -> float:
    """Spread at the entry tick, in pips. The full-lifecycle sim marks P&L
    against the opposite side of the book, so a wide spread puts a position
    instantly underwater without price having moved -- measured at ~14 pips
    (= -$28 at 0.2 lots) during the daily rollover window, enough to trip the
    $5 pre-BE soft SL on tick 1."""
    try:
        return (float(tick["ask"]) - float(tick["bid"])) / pip_size
    except Exception:
        return 0.0


def _atr_scale_factor(
    atr_value: Optional[float], pip_size: float, baseline_pips: float
) -> float:
    """Per-trade threshold scale factor from the entry ATR.

    `clamp(atr_pips / baseline_pips, ATR_SCALE_MIN, ATR_SCALE_MAX)`; returns
    `1.0` (thresholds unchanged) when ATR or the baseline is unusable.
    """
    if not atr_value or not pip_size or baseline_pips <= 0:
        return 1.0
    atr_pips = float(atr_value) / float(pip_size)
    if atr_pips <= 0:
        return 1.0
    return max(ATR_SCALE_MIN, min(ATR_SCALE_MAX, atr_pips / float(baseline_pips)))


def _scaled_exit_trade(
    scale: float,
    *,
    base_overrides: dict,
    broker,
    risk_manager,
    cache: dict,
):
    """A real `ExitTrade` whose money thresholds are multiplied by `scale`.

    Memoized on the rounded scale factor -- there are thousands of trades per
    window but only a couple of hundred distinct rounded scales.
    """
    key = round(float(scale), 2)
    cached = cache.get(key)
    if cached is not None:
        return cached
    overrides = dict(base_overrides)
    defaults = ExitTradeConfig()
    for field in ATR_SCALED_FIELDS:
        base = overrides.get(field, getattr(defaults, field))
        overrides[field] = float(base) * key
    built = create_exit_trade(
        broker=broker, risk_manager=risk_manager, config=ExitTradeConfig(**overrides)
    )
    cache[key] = built
    return built


def to_tick_dict(t) -> dict:
    """`mt5.copy_ticks_from` returns a numpy structured array -- each
    record only supports `t["bid"]` item access, not attribute access.
    The real managers' own `get_tick_value` (app/exit_strategies/managers/
    {profit,loss}.py) only handles a plain dict or an attribute-accessible
    object (real live ticks from `symbol_info_tick` are the latter) -- so a
    raw numpy tick record silently reads back `None` for both, unnoticed
    everywhere `price` has an explicit None-guard (LossExitManager, where
    it only gates the price/pips soft-SL variants -- dead code paths this
    whole project's history, since only the money-based variant has ever
    been configured), but a hard crash where it doesn't (ProfitExitManager,
    which uses price unconditionally). Converting to a plain dict once
    here is the fix, not touching the production `get_tick_value`
    functions themselves, which are correct for how live trading actually
    calls them."""
    return {"bid": float(t["bid"]), "ask": float(t["ask"])}


def profit_needs_conversion(symbol: str, account_currency: str) -> bool:
    """Return whether `(price_diff * lot * contract_size)` for `symbol`
    comes out in a currency other than the account currency and needs
    converting. True for "indirect" pairs where USD is the *base*
    currency (USDJPY, USDCHF, USDCAD against a USD account) -- the raw
    formula computes profit in the quote currency (JPY/CHF/CAD) there,
    not USD. False for "direct" pairs where the quote currency already
    is the account currency (EURUSD, GBPUSD, AUDUSD, NZDUSD). Uses MT5's
    own `symbol_info.currency_profit`, not a hardcoded pair list.
    """
    info = mt5.symbol_info(symbol)
    profit_currency = getattr(info, "currency_profit", account_currency) if info else account_currency
    return profit_currency != account_currency


def compute_profit(
    *, side: str, entry_price: float, price: float, lot: float, contract_size: float, needs_conversion: bool
) -> float:
    """Position profit in account-currency terms. For indirect pairs
    (`needs_conversion`), divides by the *current* tick price to convert
    from the quote currency to the account currency -- matching how MT5
    itself continuously re-converts floating profit in real time, not a
    fixed snapshot rate."""
    raw = (price - entry_price) * lot * contract_size if side == "buy" else (entry_price - price) * lot * contract_size
    return raw / price if needs_conversion else raw


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=None, help="Symbol to backtest (default: Config.SYMBOLS[0])")
    parser.add_argument("--weeks", type=float, default=4.0, help="Weeks of M1 history to fetch (approximate)")
    parser.add_argument("--start-pos", type=int, default=1, help="MT5 bars back from now to start the M1 window. DRIFTS: it counts back from the moment the run starts, so the same value covers a different window every run (~1.5%% of trades over a few hours) and two runs made at different times are NOT comparable. Prefer --start-date for anything you intend to compare.")
    parser.add_argument("--start-date", type=str, default=None, help="Pin the window to a wall-clock date (YYYY-MM-DD, or YYYY-MM-DD HH:MM) instead of bars-back-from-now. Makes a run reproducible and two variants exactly comparable. Ends at --end-date, or --weeks later.")
    parser.add_argument("--end-date", type=str, default=None, help="End of the pinned window (YYYY-MM-DD[ HH:MM]); defaults to --start-date plus --weeks.")
    parser.add_argument("--lot", type=float, default=0.2, help="Fixed simulated lot size (default: 0.2, matching the current $1000 sizing basis)")
    parser.add_argument("--max-ticks-per-trade", type=int, default=500, help="Max real ticks fetched per simulated trade (be_arming_ticks=90 by default, so this is a generous ceiling)")
    parser.add_argument("--counterfactual", action="store_true", help="For every soft_sl/timed_out trade, continue the same real tick stream as if only the broker-side wide SL existed, to see whether the early cut was actually a good call")
    parser.add_argument("--counterfactual-max-ticks", type=int, default=3000, help="Extended tick budget for the counterfactual continuation")
    parser.add_argument("--disable-timeout", action="store_true", help="Disable only the breakeven-timeout (EXIT_BE_ARMING_TICKS<=0, the real LossExitManager's own documented way to turn it off) -- the soft SL stays active. Answers 'what if we removed just the 90-tick rule, not the whole pre-breakeven layer'. Automatically raises --max-ticks-per-trade to --counterfactual-max-ticks unless set higher explicitly, since stalled trades can now take much longer to resolve.")
    parser.add_argument("--measure-post-be", action="store_true", help="For every reached_be trade, continue observing the same real tick stream PAST arming -- with no exit rule applied at all, just watching -- to measure real post-breakeven price excursion (peak profit reached, how long it took, the largest pullback ever seen from a running peak). Answers 'what should the post-breakeven trailing gap actually be', grounded in real data instead of a guess. Does not affect the pre-breakeven outcome/behavior at all -- purely observational.")
    parser.add_argument("--simulate-trail", action="store_true", help="For every reached_be trade, replay a fixed set of candidate post-breakeven trailing-gap rules (TRAIL_RULES) against the same real tick stream and record each rule's actual captured profit -- unlike --measure-post-be, this applies real exit logic per rule rather than just observing. Can be combined with --measure-post-be to get both the noise-floor percentiles and the rule outcomes from one tick fetch.")
    parser.add_argument("--post-be-max-ticks", type=int, default=3000, help="How many ticks past arming to observe for --measure-post-be / --simulate-trail")
    parser.add_argument("--full-lifecycle", action="store_true", help="Replay real ticks through the actual, complete ExitTrade (both managers, real precedence) from entry to a genuine final exit -- reports one real realized P&L per trade under the system as it actually exists today. Mutually exclusive with the phase-based flags above (--counterfactual/--disable-timeout/--measure-post-be/--simulate-trail are ignored when this is set).")
    parser.add_argument("--full-lifecycle-max-ticks", type=int, default=4000, help="Max real ticks fetched per trade for --full-lifecycle -- needs to cover pre-BE arming plus the full post-BE trail/cap lifecycle, not just the pre-BE window.")
    parser.add_argument("--sweep-post-be-cap", action="store_true", help="Only with --full-lifecycle: instead of using Config's single post-BE loss-cap value, replay FULL_LIFECYCLE_CAP_CANDIDATES against the same tick stream in one pass, each paired with the SAME real trailing-stop formula -- so cap retuning is tested against the real combined system, not in isolation.")
    parser.add_argument("--sweep-pre-be-threshold", action="store_true", help="Instead of using Config's single pre-breakeven soft-SL threshold, replay PRE_BE_THRESHOLD_CANDIDATES (each a real LossExitManager with a different max_loss_money, everything else at Config's real values) against the same tick stream in one pass -- Thread 2's test of whether a threshold conditional on M5-confirm state beats the flat $5 rule. Pre-BE phase only (mutually exclusive with --full-lifecycle); metadata capture (confidence/adx/m15_bias/m5_confirm/m1_entry) still included so results can be split by M5-confirm afterward.")
    parser.add_argument(
        "--mtf-entry-indicator",
        choices=["macd", "sma", "rsi", "bollinger", "stochastic"],
        default=None,
        help="Override Config.MTF_ENTRY_INDICATOR for this run only (default: whatever Config is actually set to) -- lets the real full-lifecycle P&L be compared entry-indicator-by-entry-indicator under the identical exit-strategy config, isolating that one variable.",
    )
    parser.add_argument(
        "--single-timeframe",
        action="store_true",
        help="Replay the single-timeframe strategy (M1-only, no M15 bias/M5 confirm gating) through the real exit system instead of the live MTF path -- lets the full-lifecycle P&L be compared MTF-vs-single-timeframe under the identical exit-strategy config. Default vote is macd+sma+rsi (Config.ENTRY_*_WEIGHT); use --indicators to override with a single named indicator. Mutually exclusive with --mtf-entry-indicator (that flag only affects the MTF entry layer).",
    )
    parser.add_argument(
        "--indicators",
        default=None,
        help="Only with --single-timeframe: comma-separated indicator ablation (e.g. 'macd', 'sma'); overrides the default macd+sma+rsi vote, mirroring backtest_signals.py's own --indicators flag.",
    )
    parser.add_argument(
        "--invert-signal",
        action="store_true",
        help="Trade the OPPOSITE of whatever the strategy signals (buy signal -> sell trade, sell signal -> buy trade), otherwise identical -- tests whether the entry signal has real but backwards directional information, given the full-lifecycle finding that most trades confirm the signal direction (reach breakeven) and then reverse before profit is locked in. `direction` in the CSV/summary reflects the trade actually taken, not the raw signal.",
    )
    parser.add_argument(
        "--measure-entry-excursion",
        action="store_true",
        help="For every signal, continuously observe the real tick stream from the moment of entry -- with NO exit rule applied at all, not even the pre-BE soft-SL -- to see how the trade actually progresses over its whole natural path. Tracks the running peak profit ever reached, how many ticks it took, the largest pullback ever seen from whatever the running peak was at that moment, and the final (possibly still-open) profit at the tick budget cutoff. Unlike --quick-check (fixed-horizon M1 bar high/low snapshots), this is a continuous real-tick trace with no artificial horizon. Mutually exclusive with --full-lifecycle/--sweep-*.",
    )
    parser.add_argument("--entry-excursion-max-ticks", type=int, default=4000, help="How many real ticks past entry to observe for --measure-entry-excursion")
    parser.add_argument(
        "--trail-gap-pct",
        type=float,
        default=None,
        help="Override Config.EXIT_TRAIL_GAP_PCT for this run's real ExitTrade (default: whatever Config is set to, currently 0.6 -- i.e. tolerate giving back 60%% of peak before the post-BE trail fires). Only affects --full-lifecycle (and its --sweep-post-be-cap variant).",
    )
    parser.add_argument(
        "--trail-gap-floor-money",
        type=float,
        default=None,
        help="Override Config.EXIT_TRAIL_GAP_FLOOR_MONEY for this run's real ExitTrade (default: whatever Config is set to, currently $2.0 -- this floor DOMINATES --trail-gap-pct for any peak below floor/pct, e.g. below $20 at pct=0.10, so pair a tight --trail-gap-pct with a correspondingly small floor override, e.g. 0.0, or the floor will silently override the intended percentage).",
    )
    parser.add_argument(
        "--unified-post-be-stop",
        action="store_true",
        help="Replace the real post-BE two-manager sequence (loss-manager cap, then -- only while profit stays positive -- the profit-manager trail) with a single combined stop (trigger = peak - max(--trail-gap-floor-money, --trail-gap-pct * peak)) checked every tick regardless of sign. Tests whether the real trail's '0 < profit' gate is what lets fast reversals skip past it into the separate, much looser loss cap. Pre-BE phase (arming/soft-SL/timeout) is the real, unchanged LossExitManager. Mutually exclusive with --full-lifecycle's own post-BE logic (this replaces it) and with --sweep-post-be-cap/--sweep-pre-be-threshold.",
    )
    parser.add_argument(
        "--staircase-trail",
        action="store_true",
        help="Only with --full-lifecycle: replace ProfitExitManager's percentage-of-peak trail with a staircase/ratchet trail -- as the running peak crosses each --staircase-tier-width increment, that tier level becomes the new stop; if profit falls back to or through the highest tier fully crossed, exit. Giveback is capped at just under one tier width regardless of how large the peak gets, unlike the real trail's ~60%%-of-peak (unbounded) giveback. Pre-BE phase and the post-BE loss cap are the real, unchanged LossExitManager. Mutually exclusive with --unified-post-be-stop/--chain-on-cap/--hedge-on-cap.",
    )
    parser.add_argument(
        "--staircase-tier-width",
        type=float,
        default=1.0,
        help="Dollar width of each staircase trail tier (default: $1.0).",
    )
    parser.add_argument(
        "--staircase-first-tier",
        type=float,
        default=None,
        help="Only with --staircase-trail: activation threshold for the FIRST tier, if different from --staircase-tier-width -- e.g. --staircase-first-tier 0.5 --staircase-tier-width 2.0 gives tiers at $0.5, $2.5, $4.5, ... instead of evenly-spaced $2.0, $4.0, $6.0, ... Default: same as --staircase-tier-width (original evenly-spaced behavior).",
    )
    parser.add_argument(
        "--sweep-staircase-cap",
        action="store_true",
        help="Only with --full-lifecycle --staircase-trail: instead of a single Config.EXIT_POST_BE_LOSS_CAP_MONEY value, replay FULL_LIFECYCLE_CAP_CANDIDATES against the same tick stream in one pass, each paired with the SAME staircase trail (--staircase-tier-width / --staircase-first-tier) -- so cap retuning is tested against the new trail shape, not the old percentage-of-peak one.",
    )
    parser.add_argument(
        "--sweep-staircase-tiers",
        action="store_true",
        help="Only with --full-lifecycle: instead of a single --staircase-tier-width, replay STAIRCASE_TIER_CANDIDATES against the same tick stream in one pass, each with the SAME real LossExitManager (pre-BE phase and post-BE cap unchanged) -- so tier-width retuning is tested against the real combined system, not in isolation.",
    )
    parser.add_argument(
        "--post-be-loss-cap",
        type=float,
        default=None,
        help="Override Config.EXIT_POST_BE_LOSS_CAP_MONEY for this run's real ExitTrade (default: whatever Config is set to, currently $1.0). Pair with a tight --trail-gap-pct to test 'floor the post-BE worst case at breakeven instead of a real loss' -- e.g. --post-be-loss-cap 0.0 --trail-gap-pct 0.10 --trail-gap-floor-money 0.0.",
    )
    parser.add_argument(
        "--pre-be-loss-threshold",
        type=float,
        default=None,
        help="Override Config.EXIT_MAX_LOSS_MONEY (the PRE-breakeven soft-SL threshold, currently $5.0) for this run's real ExitTrade. Unlike --sweep-pre-be-threshold (pre-BE phase only), this works in --full-lifecycle mode, so the knock-on effect of surviving longer pre-BE -- more trades reaching BE, then running the real cap/trail -- is included in the reported P&L.",
    )
    parser.add_argument(
        "--n-tick-confirmation",
        type=int,
        default=None,
        help="Require N consecutive favorable ticks after the signal candle closes before entering, mirroring the live NTickConfirmedSignalStrategy (post-fix: flat ticks do NOT count, and an unfavorable tick resets both the streak and the reference price). Confirmation must complete within 60s of the candle close, matching live's hard reset on each new M1 candle; unconfirmed signals are dropped from the trade list. The live wrapper cannot be exercised by a backtest otherwise -- it confirms via on_new_tick, which this script never calls, so setting Config.N_TICK_CONFIRMATION alone would yield zero trades.",
    )
    parser.add_argument(
        "--max-entry-spread-pips",
        type=float,
        default=None,
        help="Skip any entry whose first tick's spread exceeds this many pips. Measured cause: ~31%% of pre-BE soft-SL hits fire on tick 1, 92%% of them in the daily rollover window where spread reaches 8-14 pips (= -$16 to -$28 at 0.2 lots) -- the position is underwater on spread alone, before price moves. Skipped signals are dropped from the trade list entirely, not counted as $0 trades.",
    )
    parser.add_argument(
        "--be-arming-ticks",
        type=int,
        default=None,
        help="Override Config.EXIT_BE_ARMING_TICKS (default 90) for this run's real ExitTrade -- the number of ticks a position gets to reach breakeven before the LossExitManager closes it. 0 disables the timeout entirely. Works in --full-lifecycle mode (unlike --disable-timeout, which is a phase-based flag and is ignored there). Excursion data shows ~89%% of trades reach their unconstrained peak AFTER tick 90, so this tests whether the arming wall, not the stop distance, is what caps the pre-BE phase.",
    )
    parser.add_argument(
        "--atr-normalize",
        action="store_true",
        help="Scale the money thresholds (max_loss_money, post_be_loss_cap_money, trail_gap_floor_money) per trade by that trade's entry ATR, instead of holding them fixed in dollars: scale = clamp(entry_atr_pips / --atr-baseline-pips, 0.5, 3.0). Keeps risk constant in volatility units rather than dollars -- requires predicting nothing, unlike a regime filter. Only with --full-lifecycle.",
    )
    parser.add_argument(
        "--atr-baseline-pips",
        type=float,
        default=1.0,
        help="Only with --atr-normalize: the ATR (in pips) at which thresholds equal their configured dollar values (default: 1.0, roughly the EURUSD M1 ATR of the recent windows the current values were tuned on).",
    )
    parser.add_argument(
        "--chain-on-cap",
        action="store_true",
        help="Only with --full-lifecycle: dual-mode exit. Mode 1 (unchanged) runs until a leg's real outcome is profit_drop_after_be (hit the post-BE loss cap) -- Mode 2 then immediately opens a NEW leg in the same direction at that tick's price, with a fresh PosState (BE arming/trail/cap all reset), and keeps chaining for as long as consecutive legs keep hitting the cap. All exit rules (BE arming, pre-BE soft-SL, trail, cap) are identical for every leg -- only the re-entry-on-cap behavior is new. A leg ending any other way (trail capture, pre-BE timeout/soft-SL, tick budget exhausted) stops the chain. Reports one summed realized P&L per original signal across however many legs fired. Shares the same --full-lifecycle-max-ticks budget across the whole chain, not per leg.",
    )
    parser.add_argument(
        "--chain-max-legs",
        type=int,
        default=20,
        help="Safety cap on how many consecutive cap-hit legs --chain-on-cap will open for one original signal, in case whipsaw data would otherwise chain indefinitely within the tick budget.",
    )
    parser.add_argument(
        "--chain-flip-direction",
        action="store_true",
        help="Only with --chain-on-cap: treat a -$1 cap hit as signal invalidation and REVERSE direction on every re-entry (buy hits cap -> open sell; if that sell also hits cap -> open buy; alternating) instead of re-entering the same direction. The hypothesis: losses outweigh wins in dollar size, so accept the small -$1 loss and pivot toward the move that's actually happening rather than betting the original direction was just early.",
    )
    parser.add_argument(
        "--chain-loss-cap",
        type=float,
        default=None,
        help="Only with --chain-on-cap: use a DIFFERENT post-BE loss cap for every re-entered leg (leg 2 onward) than leg 1's real live value -- e.g. --chain-loss-cap 3.0 gives the flip more room to reach the trail before being capped again, instead of getting re-capped at the same tight -$1. Leg 1 is always the real, unchanged live cap. Default: reuse the same cap for every leg.",
    )
    parser.add_argument(
        "--hedge-on-cap",
        action="store_true",
        help="Only with --full-lifecycle (mutually exclusive with --chain-on-cap): instead of closing the original position when it hits -$1, open a SECOND position in the opposite direction at that price and hold BOTH. The original keeps running with its loss cap disabled (deliberately held, not stopped out again) but its trail still live; the new reverse leg runs under full normal rules (BE arm, pre-BE, trail, cap). An equal-size opposite pair's combined P&L is frozen tick to tick until one side closes -- this tracks both independently to their own real close and reports the summed total.",
    )
    parser.add_argument(
        "--original-loss-cap",
        type=float,
        default=None,
        help="Only with --hedge-on-cap: re-enable a REAL (wider) loss cap on the held-open original from the fork point onward, instead of leaving it fully uncapped -- e.g. --original-loss-cap 5.0 stops the original at -$5 rather than letting a persistent one-way move bleed it until the tick budget runs out. Default: original stays fully uncapped past the fork (only its trail can close it).",
    )
    return parser.parse_args()


def simulate_pre_be_phase(
    *,
    exit_trade,
    symbol: str,
    side: str,
    entry_time: datetime.datetime,
    lot: float,
    contract_size: float,
    max_ticks: int,
    fetch_ticks: int,
    sl_price_distance: float,
    run_counterfactual: bool,
    needs_conversion: bool,
    max_entry_spread_pips: Optional[float] = None,
    n_tick_confirmation: Optional[int] = None,
    candle_close_price: float = 0.0,
    pip_size: float = 0.0001,
    measure_post_be: bool = False,
    post_be_max_ticks: int = 0,
    simulate_trail: bool = False,
) -> dict:
    """Open a simulated position at the first real tick at/after
    `entry_time` and replay real historical ticks through the real
    `LossExitManager.check_exit_on_tick`, returning
    `{"outcome", "profit", "ticks_used", "entry_price", ...counterfactual fields}`.

    Fetches `fetch_ticks` ticks up front (>= `max_ticks`) so that, when
    `run_counterfactual` and the real outcome is `soft_sl`/`timed_out`, the
    counterfactual continuation (`simulate_counterfactual`) can replay the
    exact same remaining price path rather than a separately-fetched one.
    Same idea for `measure_post_be` when the real outcome is `reached_be`.
    """
    ticks = mt5.copy_ticks_from(symbol, entry_time, fetch_ticks, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return {"outcome": "no_ticks", "profit": None, "ticks_used": 0, "entry_price": None}

    if n_tick_confirmation and n_tick_confirmation > 1:
        ci = _confirm_entry_tick(
            ticks, side, candle_close_price if candle_close_price else float(ticks[0]["bid"]),
            n_tick_confirmation,
        )
        if ci is None:
            return {"outcome": "unconfirmed", "profit": None, "ticks_used": 0, "entry_price": None}
        ticks = ticks[ci:]
    entry_tick = ticks[0]
    if max_entry_spread_pips is not None:
        spread_pips = _entry_spread_pips(entry_tick, pip_size)
        if spread_pips > max_entry_spread_pips:
            return {"outcome": "skipped_wide_spread", "profit": None,
                    "ticks_used": 0, "entry_price": None, "entry_spread_pips": spread_pips}
    entry_price = float(entry_tick["ask"]) if side == "buy" else float(entry_tick["bid"])
    pos_type = 0 if side == "buy" else 1

    state = PosState(anchor=0.0, prev_price=0.0)
    loss_manager = exit_trade._loss_manager

    profit = 0.0
    result = None
    resolved_idx = len(ticks)
    for idx, t in enumerate(ticks[:max_ticks]):
        price = float(t["bid"]) if side == "buy" else float(t["ask"])
        profit = compute_profit(
            side=side, entry_price=entry_price, price=price, lot=lot,
            contract_size=contract_size, needs_conversion=needs_conversion,
        )

        position = {
            "symbol": symbol,
            "type": pos_type,
            "ticket": idx,
            "price_open": entry_price,
            "volume": lot,
            "profit": profit,
        }

        action = loss_manager.check_exit_on_tick(position, to_tick_dict(t), state)
        if action:
            outcome = "soft_sl" if action.reason == "profit_drop" else "timed_out"
            result = {"outcome": outcome, "profit": profit, "ticks_used": idx + 1, "entry_price": entry_price}
            resolved_idx = idx + 1
            break
        if getattr(state, "be_armed", False):
            result = {"outcome": "reached_be", "profit": profit, "ticks_used": idx + 1, "entry_price": entry_price}
            resolved_idx = idx + 1
            break

    if result is None:
        result = {
            "outcome": "exhausted",
            "profit": profit,
            "ticks_used": min(len(ticks), max_ticks),
            "entry_price": entry_price,
        }

    if run_counterfactual and result["outcome"] in ("soft_sl", "timed_out"):
        cf = simulate_counterfactual(
            ticks=ticks,
            start_idx=resolved_idx,
            side=side,
            entry_price=entry_price,
            lot=lot,
            contract_size=contract_size,
            sl_price_distance=sl_price_distance,
            needs_conversion=needs_conversion,
        )
        result.update(cf)

    if measure_post_be and result["outcome"] == "reached_be":
        pb = measure_post_be_excursion(
            ticks=ticks,
            arm_idx=resolved_idx,
            side=side,
            entry_price=entry_price,
            lot=lot,
            contract_size=contract_size,
            needs_conversion=needs_conversion,
            max_extra_ticks=post_be_max_ticks,
        )
        result.update(pb)

    if simulate_trail and result["outcome"] == "reached_be":
        tr = simulate_trail_rules(
            ticks=ticks,
            arm_idx=resolved_idx,
            side=side,
            entry_price=entry_price,
            lot=lot,
            contract_size=contract_size,
            needs_conversion=needs_conversion,
            max_extra_ticks=post_be_max_ticks,
            loss_manager=loss_manager,
            state=state,
            symbol=symbol,
        )
        result.update(tr)

    return result


def simulate_counterfactual(
    *,
    ticks,
    start_idx: int,
    side: str,
    entry_price: float,
    lot: float,
    contract_size: float,
    sl_price_distance: float,
    needs_conversion: bool,
) -> dict:
    """Continue the same real tick stream from `start_idx` (the point where
    the real soft-SL/timeout cut the trade) as if only the broker-side wide
    SL existed: no soft SL, no breakeven-timeout. Returns which happens
    first -- recovers to break-even, hits the broker's wide SL distance, or
    neither within the remaining fetched ticks.
    """
    cf_profit = None
    for idx in range(start_idx, len(ticks)):
        t = ticks[idx]
        price = float(t["bid"]) if side == "buy" else float(t["ask"])
        cf_profit = compute_profit(
            side=side, entry_price=entry_price, price=price, lot=lot,
            contract_size=contract_size, needs_conversion=needs_conversion,
        )
        adverse_distance = (entry_price - price) if side == "buy" else (price - entry_price)

        if cf_profit >= 0.0:
            return {
                "cf_outcome": "would_have_recovered",
                "cf_profit": cf_profit,
                "cf_extra_ticks": idx - start_idx + 1,
            }
        if adverse_distance >= sl_price_distance:
            return {
                "cf_outcome": "would_have_hit_broker_sl",
                "cf_profit": cf_profit,
                "cf_extra_ticks": idx - start_idx + 1,
            }

    return {
        "cf_outcome": "still_unresolved",
        "cf_profit": cf_profit,
        "cf_extra_ticks": len(ticks) - start_idx,
    }


def measure_post_be_excursion(
    *,
    ticks,
    arm_idx: int,
    side: str,
    entry_price: float,
    lot: float,
    contract_size: float,
    needs_conversion: bool,
    max_extra_ticks: int,
) -> dict:
    """After breakeven arms (at `arm_idx` within `ticks`), continue
    observing the same real tick stream with NO exit rule applied at all --
    purely watching what real post-breakeven price action does, to
    calibrate what a real trailing-profit gap should be sized at, instead
    of guessing. Tracks the running peak profit and the largest pullback
    ever seen from *whatever the running peak was at that moment* (not just
    the final peak) -- that's the number a trail gap actually has to survive
    to not be stopped out by ordinary noise before a new high is made.

    Returns `{"post_be_peak_profit", "post_be_ticks_to_peak",
    "post_be_max_drawdown_from_peak", "post_be_final_profit",
    "post_be_ticks_observed"}`.
    """
    end_idx = min(len(ticks), arm_idx + max_extra_ticks)

    running_peak: Optional[float] = None
    peak_profit: Optional[float] = None
    ticks_to_peak = 0
    max_drawdown = 0.0
    final_profit: Optional[float] = None

    for idx in range(arm_idx, end_idx):
        t = ticks[idx]
        price = float(t["bid"]) if side == "buy" else float(t["ask"])
        profit = compute_profit(
            side=side, entry_price=entry_price, price=price, lot=lot,
            contract_size=contract_size, needs_conversion=needs_conversion,
        )
        final_profit = profit

        if running_peak is None or profit > running_peak:
            running_peak = profit
            peak_profit = profit
            ticks_to_peak = idx - arm_idx
        else:
            drawdown = running_peak - profit
            if drawdown > max_drawdown:
                max_drawdown = drawdown

    return {
        "post_be_peak_profit": peak_profit,
        "post_be_ticks_to_peak": ticks_to_peak,
        "post_be_max_drawdown_from_peak": max_drawdown,
        "post_be_final_profit": final_profit,
        "post_be_ticks_observed": end_idx - arm_idx,
    }


def measure_entry_excursion(
    *,
    symbol: str,
    side: str,
    entry_time: datetime.datetime,
    lot: float,
    contract_size: float,
    max_ticks: int,
    needs_conversion: bool,
    max_entry_spread_pips: Optional[float] = None,
    n_tick_confirmation: Optional[int] = None,
    candle_close_price: float = 0.0,
    pip_size: float = 0.0001,
) -> dict:
    """Open a simulated position and continuously observe the real tick
    stream from the moment of entry -- with NO exit rule applied at all,
    not even the pre-BE soft-SL -- to see how the trade actually
    progresses over its whole natural path. Mirrors
    `measure_post_be_excursion`'s running-peak/drawdown-from-peak
    tracking, but starts at tick 0 (entry) instead of at breakeven
    arming, and applies no exit logic whatsoever -- pure observation of
    what real price does after this specific entry, unfiltered by any
    strategy decision.

    Unlike `--quick-check` (which snapshots M1 candle high/low extremes
    over a fixed few-minute horizon, run as separate independent calls
    per horizon), this replays the actual sequential tick stream once,
    continuously, for up to `max_ticks` -- a real trace of one trade's
    progression, not a series of disconnected fixed-horizon photographs.

    Returns `{"entry_peak_profit", "entry_ticks_to_peak",
    "entry_max_drawdown_from_peak", "entry_final_profit",
    "entry_ticks_observed", "entry_price"}`.
    """
    ticks = mt5.copy_ticks_from(symbol, entry_time, max_ticks, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return {
            "entry_peak_profit": None,
            "entry_ticks_to_peak": 0,
            "entry_max_drawdown_from_peak": None,
            "entry_final_profit": None,
            "entry_ticks_observed": 0,
            "entry_price": None,
        }

    if n_tick_confirmation and n_tick_confirmation > 1:
        ci = _confirm_entry_tick(
            ticks, side, candle_close_price if candle_close_price else float(ticks[0]["bid"]),
            n_tick_confirmation,
        )
        if ci is None:
            return {"outcome": "unconfirmed", "profit": None, "ticks_used": 0, "entry_price": None}
        ticks = ticks[ci:]
    entry_tick = ticks[0]
    if max_entry_spread_pips is not None:
        spread_pips = _entry_spread_pips(entry_tick, pip_size)
        if spread_pips > max_entry_spread_pips:
            return {"outcome": "skipped_wide_spread", "profit": None,
                    "ticks_used": 0, "entry_price": None, "entry_spread_pips": spread_pips}
    entry_price = float(entry_tick["ask"]) if side == "buy" else float(entry_tick["bid"])
    end_idx = min(len(ticks), max_ticks)

    running_peak: Optional[float] = None
    peak_profit: Optional[float] = None
    ticks_to_peak = 0
    max_drawdown = 0.0
    final_profit: Optional[float] = None

    for idx in range(end_idx):
        t = ticks[idx]
        price = float(t["bid"]) if side == "buy" else float(t["ask"])
        profit = compute_profit(
            side=side, entry_price=entry_price, price=price, lot=lot,
            contract_size=contract_size, needs_conversion=needs_conversion,
        )
        final_profit = profit

        if running_peak is None or profit > running_peak:
            running_peak = profit
            peak_profit = profit
            ticks_to_peak = idx
        else:
            drawdown = running_peak - profit
            if drawdown > max_drawdown:
                max_drawdown = drawdown

    return {
        "entry_peak_profit": peak_profit,
        "entry_ticks_to_peak": ticks_to_peak,
        "entry_max_drawdown_from_peak": max_drawdown,
        "entry_final_profit": final_profit,
        "entry_ticks_observed": end_idx,
        "entry_price": entry_price,
    }


def simulate_trail_rules(
    *,
    ticks,
    arm_idx: int,
    side: str,
    entry_price: float,
    lot: float,
    contract_size: float,
    needs_conversion: bool,
    max_extra_ticks: int,
    loss_manager,
    state: PosState,
    symbol: str,
) -> dict:
    """After breakeven arms (at `arm_idx`), replay every rule in
    `TRAIL_RULES` against the same real tick stream in a single pass,
    tracking one shared running peak profit and, per rule, a dynamic
    trigger = peak - gap(peak). The rule only actively enforces once
    that trigger is positive (peak has grown past its own gap) --
    while the computed trigger is <= $0, the trail stays inactive
    rather than force-exiting the moment a small peak's trigger math
    goes negative.

    That inactive window is NOT actually unprotected in production,
    though: the real, unmodified `LossExitManager.check_exit_on_tick`
    (Thread 4's existing post-BE loss cap -- `Config.EXIT_POST_BE_LOSS_CAP_MONEY`
    force-close and the `be_recovered_after_unprofit` lock-in) keeps
    running every tick in parallel, via the same `state` object already
    carrying `be_armed=True` from the pre-breakeven phase. It's
    rule-agnostic (based on raw profit history, not on any trail rule),
    so its first-trigger point is computed once and shared across all
    5 candidates. Each rule's *effective* outcome is whichever of (that
    rule's own trigger, the real loss manager's trigger) comes first --
    answering "how would this candidate actually behave with today's
    existing safety net still running underneath it," not "how would it
    behave in a vacuum with nothing else protecting the trade."

    A rule with no trigger at all (its own, or the loss manager's)
    within `max_extra_ticks` is marked not-triggered and its captured
    profit is whatever profit was observed at the end of the window
    (matches `post_be_final_profit`'s convention for the no-exit case).

    Also replays `CAP_THRESHOLDS` (Thread 4 candidate values for the real
    loss manager's `Config.EXIT_POST_BE_LOSS_CAP_MONEY`) against the same
    raw profit series, independent of any trail rule or the real loss
    manager -- simple "exit the instant profit drops to/below this flat
    threshold" checks, to compare alternate cap sizes against each other
    on equal footing.

    Returns `{"trail_<name>_profit", "trail_<name>_ticks",
    "trail_<name>_triggered", "trail_<name>_lm_capped"}` for every name
    in `TRAIL_RULES`; `{"cap<N>_profit", "cap<N>_ticks",
    "cap<N>_triggered"}` for every threshold in `CAP_THRESHOLDS`; plus
    `"trail_lm_profit"`/`"trail_lm_ticks"`/`"trail_lm_triggered"` for the
    real loss manager's own outcome.
    """
    end_idx = min(len(ticks), arm_idx + max_extra_ticks)
    pos_type = 0 if side == "buy" else 1

    running_peak: Optional[float] = None
    triggered = {name: False for name, _ in TRAIL_RULES}
    rule_profit: dict[str, Optional[float]] = {name: None for name, _ in TRAIL_RULES}
    rule_ticks: dict[str, Optional[int]] = {name: None for name, _ in TRAIL_RULES}
    activation_ticks: dict[str, Optional[int]] = {name: None for name, _ in TRAIL_RULES}

    lm_triggered = False
    lm_profit: Optional[float] = None
    lm_ticks: Optional[int] = None
    lm_reason: Optional[str] = None
    lm_trigger_abs_idx: Optional[int] = None

    gap_recovered = False
    gap_ticks_to_recover: Optional[int] = None
    gap_min_profit_after_cap: Optional[float] = None

    cap_triggered = {c: False for c in CAP_THRESHOLDS}
    cap_profit: dict[float, Optional[float]] = {c: None for c in CAP_THRESHOLDS}
    cap_ticks: dict[float, Optional[int]] = {c: None for c in CAP_THRESHOLDS}

    profit = 0.0
    for idx in range(arm_idx, end_idx):
        t = ticks[idx]
        price = float(t["bid"]) if side == "buy" else float(t["ask"])
        profit = compute_profit(
            side=side, entry_price=entry_price, price=price, lot=lot,
            contract_size=contract_size, needs_conversion=needs_conversion,
        )

        if running_peak is None or profit > running_peak:
            running_peak = profit

        for c in CAP_THRESHOLDS:
            if not cap_triggered[c] and profit <= c:
                cap_triggered[c] = True
                cap_profit[c] = profit
                cap_ticks[c] = idx - arm_idx

        for name, gap_fn in TRAIL_RULES:
            if triggered[name]:
                continue
            trigger_level = running_peak - gap_fn(running_peak)
            if trigger_level > 0.0:
                if activation_ticks[name] is None:
                    activation_ticks[name] = idx - arm_idx
                if profit <= trigger_level:
                    triggered[name] = True
                    rule_profit[name] = profit
                    rule_ticks[name] = idx - arm_idx

        if not lm_triggered:
            position = {
                "symbol": symbol,
                "type": pos_type,
                "ticket": idx,
                "price_open": entry_price,
                "volume": lot,
                "profit": profit,
            }
            action = loss_manager.check_exit_on_tick(position, to_tick_dict(t), state)
            if action:
                lm_triggered = True
                lm_profit = profit
                lm_ticks = idx - arm_idx
                lm_reason = action.reason
                lm_trigger_abs_idx = idx
        elif lm_trigger_abs_idx is not None and idx > lm_trigger_abs_idx:
            # Gap-reversal counterfactual: what real price did AFTER the real
            # post-BE loss cap would have force-closed the trade -- continuing
            # to watch with no exit rule applied, same as --measure-post-be
            # but anchored to the cap point instead of the breakeven-arming
            # point.
            if gap_min_profit_after_cap is None or profit < gap_min_profit_after_cap:
                gap_min_profit_after_cap = profit
            if not gap_recovered and profit > 0.0:
                gap_recovered = True
                gap_ticks_to_recover = idx - lm_trigger_abs_idx

    for name, _ in TRAIL_RULES:
        if not triggered[name]:
            rule_profit[name] = profit
            rule_ticks[name] = end_idx - arm_idx
    if not lm_triggered:
        lm_profit = profit
        lm_ticks = end_idx - arm_idx
    for c in CAP_THRESHOLDS:
        if not cap_triggered[c]:
            cap_profit[c] = profit
            cap_ticks[c] = end_idx - arm_idx

    result = {
        "trail_lm_profit": lm_profit,
        "trail_lm_ticks": lm_ticks,
        "trail_lm_triggered": lm_triggered,
        "trail_lm_reason": lm_reason,
        "gap_recovered": gap_recovered if lm_triggered else None,
        "gap_ticks_to_recover": gap_ticks_to_recover,
        "gap_min_profit_after_cap": gap_min_profit_after_cap,
        "gap_final_profit": profit if lm_triggered else None,
    }
    for name, _ in TRAIL_RULES:
        own_ticks = rule_ticks[name] if triggered[name] else None
        use_lm = lm_triggered and (own_ticks is None or lm_ticks < own_ticks)
        # "in the gap" = the real loss manager fired before this rule ever
        # started watching at all (activation_ticks[name] is None or later
        # than lm_ticks) -- as opposed to firing after the rule was already
        # active but before the rule's own trigger caught it.
        in_gap = use_lm and (activation_ticks[name] is None or lm_ticks < activation_ticks[name])
        result[f"trail_{name}_profit"] = lm_profit if use_lm else rule_profit[name]
        result[f"trail_{name}_ticks"] = lm_ticks if use_lm else rule_ticks[name]
        result[f"trail_{name}_triggered"] = True if use_lm else triggered[name]
        result[f"trail_{name}_lm_capped"] = use_lm
        result[f"trail_{name}_lm_capped_in_gap"] = in_gap
        result[f"trail_{name}_activation_ticks"] = activation_ticks[name]
    for c in CAP_THRESHOLDS:
        tag = f"cap{int(abs(c))}"
        result[f"{tag}_profit"] = cap_profit[c]
        result[f"{tag}_ticks"] = cap_ticks[c]
        result[f"{tag}_triggered"] = cap_triggered[c]
    return result


def simulate_full_lifecycle(
    *,
    exit_trade,
    symbol: str,
    side: str,
    entry_time: datetime.datetime,
    lot: float,
    contract_size: float,
    max_ticks: int,
    needs_conversion: bool,
    max_entry_spread_pips: Optional[float] = None,
    n_tick_confirmation: Optional[int] = None,
    candle_close_price: float = 0.0,
    pip_size: float = 0.0001,
) -> dict:
    """Open a simulated position and replay real historical ticks through
    the actual, complete `ExitTrade` -- `exit_trade._loss_manager` THEN (if
    no loss action) `exit_trade._profit_manager`, the same precedence
    `ExitTrade.on_tick` itself uses, gated on `config.profit_exits_on_tick`
    (default `True`). One `PosState` carries continuously across the whole
    trade -- pre-BE arming through post-BE trailing/cap -- no phase
    boundary, unlike every other function in this script.

    Returns `{"outcome": <real exit reason, or "exhausted"/"no_ticks">,
    "profit", "ticks_used", "entry_price"}`.
    """
    ticks = mt5.copy_ticks_from(symbol, entry_time, max_ticks, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return {"outcome": "no_ticks", "profit": None, "ticks_used": 0, "entry_price": None}

    if n_tick_confirmation and n_tick_confirmation > 1:
        ci = _confirm_entry_tick(
            ticks, side, candle_close_price if candle_close_price else float(ticks[0]["bid"]),
            n_tick_confirmation,
        )
        if ci is None:
            return {"outcome": "unconfirmed", "profit": None, "ticks_used": 0, "entry_price": None}
        ticks = ticks[ci:]
    entry_tick = ticks[0]
    if max_entry_spread_pips is not None:
        spread_pips = _entry_spread_pips(entry_tick, pip_size)
        if spread_pips > max_entry_spread_pips:
            return {"outcome": "skipped_wide_spread", "profit": None,
                    "ticks_used": 0, "entry_price": None, "entry_spread_pips": spread_pips}
    entry_price = float(entry_tick["ask"]) if side == "buy" else float(entry_tick["bid"])
    pos_type = 0 if side == "buy" else 1

    state = PosState(anchor=0.0, prev_price=0.0)
    loss_manager = exit_trade._loss_manager
    profit_manager = exit_trade._profit_manager
    profit_exits_on_tick = bool(getattr(exit_trade._config, "profit_exits_on_tick", True))

    profit = 0.0
    outcome = None
    idx = -1
    be_arm_ticks: Optional[int] = None
    for idx, t in enumerate(ticks[:max_ticks]):
        price = float(t["bid"]) if side == "buy" else float(t["ask"])
        profit = compute_profit(
            side=side, entry_price=entry_price, price=price, lot=lot,
            contract_size=contract_size, needs_conversion=needs_conversion,
        )

        position = {
            "symbol": symbol,
            "type": pos_type,
            "ticket": idx,
            "price_open": entry_price,
            "volume": lot,
            "profit": profit,
        }

        was_armed = getattr(state, "be_armed", False)
        tick_dict = to_tick_dict(t)
        action = loss_manager.check_exit_on_tick(position, tick_dict, state)
        if action:
            outcome = action.reason
            break

        if profit_exits_on_tick:
            action = profit_manager.check_exit_on_tick(position, tick_dict, state)
            if action:
                outcome = action.reason
                break

        if be_arm_ticks is None and not was_armed and getattr(state, "be_armed", False):
            be_arm_ticks = idx

    if outcome is None:
        outcome = "exhausted"

    # Counterfactual: for a real post-BE cap exit specifically, did price
    # ever recover to breakeven (or better) afterward if we'd just kept
    # watching with no exit rule applied? The ticks are already fetched
    # (mt5.copy_ticks_from pulled the full max_ticks budget up front,
    # independent of where the real replay actually stopped), so this is
    # pure post-hoc observation of the same tick stream, not a new fetch.
    cf_recovered_to_be: Optional[bool] = None
    cf_ticks_to_recover: Optional[int] = None
    cf_min_profit_after_exit: Optional[float] = None
    # Same idea as cf_recovered_to_be, but a lower bar: did price ever
    # climb back to -$1 or better (not all the way to breakeven)? A
    # trade can clear this without clearing cf_recovered_to_be.
    cf_recovered_to_neg1: Optional[bool] = None
    cf_ticks_to_recover_neg1: Optional[int] = None
    # For trades that DO recover (above): once profit first crosses back to
    # >= $0, does it then reverse again (dip back below $0 a second time),
    # or keep climbing? Tracks the running peak reached after that first
    # recovery point, and whether/when profit falls back below $0 again.
    cf_post_recovery_peak: Optional[float] = None
    cf_reversed_after_recovery: Optional[bool] = None
    cf_ticks_to_reversal_after_recovery: Optional[int] = None
    if outcome == "profit_drop_after_be":
        cf_min = profit
        recovered = False
        recovery_idx: Optional[int] = None
        ticks_to_recover = None
        post_recovery_peak: Optional[float] = None
        reversed_after_recovery = False
        ticks_to_reversal_after_recovery = None
        recovered_neg1 = False
        ticks_to_recover_neg1 = None
        for j in range(idx + 1, len(ticks)):
            t2 = ticks[j]
            price2 = float(t2["bid"]) if side == "buy" else float(t2["ask"])
            profit2 = compute_profit(
                side=side, entry_price=entry_price, price=price2, lot=lot,
                contract_size=contract_size, needs_conversion=needs_conversion,
            )
            if profit2 < cf_min:
                cf_min = profit2
            if not recovered_neg1 and profit2 >= -1.0:
                recovered_neg1 = True
                ticks_to_recover_neg1 = j - idx
            if not recovered and profit2 >= 0.0:
                recovered = True
                ticks_to_recover = j - idx
                recovery_idx = j
                post_recovery_peak = profit2
            elif recovered:
                if profit2 > post_recovery_peak:
                    post_recovery_peak = profit2
                if not reversed_after_recovery and profit2 < 0.0:
                    reversed_after_recovery = True
                    ticks_to_reversal_after_recovery = j - recovery_idx
        cf_recovered_to_be = recovered
        cf_ticks_to_recover = ticks_to_recover
        cf_min_profit_after_exit = cf_min
        cf_recovered_to_neg1 = recovered_neg1
        cf_ticks_to_recover_neg1 = ticks_to_recover_neg1
        if recovered:
            cf_post_recovery_peak = post_recovery_peak
            cf_reversed_after_recovery = reversed_after_recovery
            cf_ticks_to_reversal_after_recovery = ticks_to_reversal_after_recovery

    return {
        "outcome": outcome,
        "profit": profit,
        "ticks_used": idx + 1,
        "entry_price": entry_price,
        "cf_recovered_to_be": cf_recovered_to_be,
        "cf_ticks_to_recover": cf_ticks_to_recover,
        "cf_min_profit_after_exit": cf_min_profit_after_exit,
        "cf_recovered_to_neg1": cf_recovered_to_neg1,
        "cf_ticks_to_recover_neg1": cf_ticks_to_recover_neg1,
        "cf_post_recovery_peak": cf_post_recovery_peak,
        "cf_reversed_after_recovery": cf_reversed_after_recovery,
        "cf_ticks_to_reversal_after_recovery": cf_ticks_to_reversal_after_recovery,
        # Real running peak profit reached post-breakeven (None if BE never
        # armed, i.e. outcome in {"profit_drop", "failed_to_reach_be"}) --
        # lets `profit_drop_after_be` outcomes be split by how far above
        # breakeven the trade actually got before reversing into the loss
        # cap, rather than just knowing that it did.
        "post_be_peak_profit": getattr(state, "best_profit", None),
        # Tick index (0-based) at which breakeven first armed, None if it
        # never did -- how long it actually took, for trades that got there.
        "be_arm_ticks": be_arm_ticks,
    }


def simulate_full_lifecycle_staircase_trail(
    *,
    exit_trade,
    symbol: str,
    side: str,
    entry_time: datetime.datetime,
    lot: float,
    contract_size: float,
    max_ticks: int,
    needs_conversion: bool,
    max_entry_spread_pips: Optional[float] = None,
    n_tick_confirmation: Optional[int] = None,
    candle_close_price: float = 0.0,
    pip_size: float = 0.0001,
    tier_width: float,
    first_tier: Optional[float] = None,
) -> dict:
    """Same pre-BE phase as `simulate_full_lifecycle` (the real
    `LossExitManager.check_exit_on_tick` handles arming/soft-SL/timeout
    unchanged, and still owns the post-BE loss cap for negative
    excursions), but replaces `ProfitExitManager`'s percentage-of-peak
    trail with a staircase/ratchet trail: as the running peak crosses
    each `tier_width` increment past `first_tier`, that tier level
    becomes the new stop. If profit falls back to or through the highest
    tier fully crossed, exit (`reason="staircase_trail_breach"`). Same
    `0 < profit < peak` gating as the real trail (only fires while still
    positive -- a reversal straight through zero is caught by the real
    loss-manager cap instead, unchanged).

    `first_tier` (default: `tier_width`, i.e. the original evenly-spaced
    behavior) sets a DIFFERENT activation threshold than the step size --
    e.g. `first_tier=0.5, tier_width=2.0` gives tiers at $0.5, $2.5,
    $4.5, ... instead of $2.0, $4.0, $6.0, ... -- lets the trail engage
    (and start protecting) much earlier than its steady-state step size,
    directly targeting the `first_tier < peak <= tier_width` population
    that's otherwise 100% left to the separate loss cap.

    Unlike the real trail's giveback (which grows with the peak, ~60% of
    whatever peak is reached), the staircase's giveback is capped at
    just under one `tier_width`, however large the peak gets -- directly
    targeting the finding that the real trail leaves ~70% of every real
    peak uncaptured on large moves.

    Returns the same shape as `simulate_full_lifecycle`.
    """
    first_tier = tier_width if first_tier is None else first_tier
    ticks = mt5.copy_ticks_from(symbol, entry_time, max_ticks, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return {"outcome": "no_ticks", "profit": None, "ticks_used": 0, "entry_price": None}

    if n_tick_confirmation and n_tick_confirmation > 1:
        ci = _confirm_entry_tick(
            ticks, side, candle_close_price if candle_close_price else float(ticks[0]["bid"]),
            n_tick_confirmation,
        )
        if ci is None:
            return {"outcome": "unconfirmed", "profit": None, "ticks_used": 0, "entry_price": None}
        ticks = ticks[ci:]
    entry_tick = ticks[0]
    if max_entry_spread_pips is not None:
        spread_pips = _entry_spread_pips(entry_tick, pip_size)
        if spread_pips > max_entry_spread_pips:
            return {"outcome": "skipped_wide_spread", "profit": None,
                    "ticks_used": 0, "entry_price": None, "entry_spread_pips": spread_pips}
    entry_price = float(entry_tick["ask"]) if side == "buy" else float(entry_tick["bid"])
    pos_type = 0 if side == "buy" else 1

    state = PosState(anchor=0.0, prev_price=0.0)
    loss_manager = exit_trade._loss_manager
    profit_exits_on_tick = bool(getattr(exit_trade._config, "profit_exits_on_tick", True))

    profit = 0.0
    outcome = None
    idx = -1
    be_arm_ticks: Optional[int] = None
    best_profit: Optional[float] = None
    for idx, t in enumerate(ticks[:max_ticks]):
        price = float(t["bid"]) if side == "buy" else float(t["ask"])
        profit = compute_profit(
            side=side, entry_price=entry_price, price=price, lot=lot,
            contract_size=contract_size, needs_conversion=needs_conversion,
        )

        position = {
            "symbol": symbol,
            "type": pos_type,
            "ticket": idx,
            "price_open": entry_price,
            "volume": lot,
            "profit": profit,
        }

        was_armed = getattr(state, "be_armed", False)
        tick_dict = to_tick_dict(t)
        action = loss_manager.check_exit_on_tick(position, tick_dict, state)
        if action:
            outcome = action.reason
            break

        if not was_armed and getattr(state, "be_armed", False):
            be_arm_ticks = idx
            best_profit = profit

        if profit_exits_on_tick and getattr(state, "be_armed", False):
            if best_profit is None or profit > best_profit:
                best_profit = profit
            if tier_width > 0.0 and 0.0 < profit < best_profit and best_profit >= first_tier:
                current_tier = first_tier + (int((best_profit - first_tier) / tier_width)) * tier_width
                if current_tier > 0.0 and profit <= current_tier:
                    outcome = "staircase_trail_breach"
                    break

    if outcome is None:
        outcome = "exhausted"

    return {
        "outcome": outcome,
        "profit": profit,
        "ticks_used": idx + 1,
        "entry_price": entry_price,
        "post_be_peak_profit": best_profit,
        "be_arm_ticks": be_arm_ticks,
    }


def simulate_full_lifecycle_staircase_sweep(
    *,
    exit_trade,
    symbol: str,
    side: str,
    entry_time: datetime.datetime,
    lot: float,
    contract_size: float,
    max_ticks: int,
    needs_conversion: bool,
    max_entry_spread_pips: Optional[float] = None,
    n_tick_confirmation: Optional[int] = None,
    candle_close_price: float = 0.0,
    pip_size: float = 0.0001,
    tier_widths: list,
) -> dict:
    """Like `simulate_full_lifecycle_staircase_trail`, but replays every
    candidate `tier_width` against the same real tick stream in a single
    pass -- each candidate has its own independent `PosState`, but the
    SAME real `LossExitManager` (pre-BE phase and the post-BE loss cap
    are unchanged and identical across candidates; only the trail's tier
    width varies). A candidate stops updating once it resolves; others
    continue independently over the same price path.

    Returns `{"<label>_outcome", "<label>_profit", "<label>_ticks"}` for
    every width in `tier_widths`, label = `tier{width with '.' -> '_'}`.
    """
    labels = [f"tier{str(w).replace('.', '_')}" for w in tier_widths]
    ticks = mt5.copy_ticks_from(symbol, entry_time, max_ticks, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        result = {}
        for label in labels:
            result[f"{label}_outcome"] = "no_ticks"
            result[f"{label}_profit"] = None
            result[f"{label}_ticks"] = 0
        return result

    if n_tick_confirmation and n_tick_confirmation > 1:
        ci = _confirm_entry_tick(
            ticks, side, candle_close_price if candle_close_price else float(ticks[0]["bid"]),
            n_tick_confirmation,
        )
        if ci is None:
            return {"outcome": "unconfirmed", "profit": None, "ticks_used": 0, "entry_price": None}
        ticks = ticks[ci:]
    entry_tick = ticks[0]
    if max_entry_spread_pips is not None:
        spread_pips = _entry_spread_pips(entry_tick, pip_size)
        if spread_pips > max_entry_spread_pips:
            return {"outcome": "skipped_wide_spread", "profit": None,
                    "ticks_used": 0, "entry_price": None, "entry_spread_pips": spread_pips}
    entry_price = float(entry_tick["ask"]) if side == "buy" else float(entry_tick["bid"])
    pos_type = 0 if side == "buy" else 1
    loss_manager = exit_trade._loss_manager
    profit_exits_on_tick = bool(getattr(exit_trade._config, "profit_exits_on_tick", True))

    states = {label: PosState(anchor=0.0, prev_price=0.0) for label in labels}
    best_profit: dict[str, Optional[float]] = {label: None for label in labels}
    resolved = {label: False for label in labels}
    outcome: dict[str, Optional[str]] = {label: None for label in labels}
    result_profit: dict[str, Optional[float]] = {label: None for label in labels}
    result_ticks: dict[str, Optional[int]] = {label: None for label in labels}

    profit = 0.0
    for idx, t in enumerate(ticks[:max_ticks]):
        if all(resolved.values()):
            break
        price = float(t["bid"]) if side == "buy" else float(t["ask"])
        profit = compute_profit(
            side=side, entry_price=entry_price, price=price, lot=lot,
            contract_size=contract_size, needs_conversion=needs_conversion,
        )
        position = {
            "symbol": symbol, "type": pos_type, "ticket": idx,
            "price_open": entry_price, "volume": lot, "profit": profit,
        }
        tick_dict = to_tick_dict(t)

        for label, tier_width in zip(labels, tier_widths):
            if resolved[label]:
                continue
            state = states[label]
            was_armed = getattr(state, "be_armed", False)
            action = loss_manager.check_exit_on_tick(position, tick_dict, state)
            if action:
                resolved[label] = True
                outcome[label] = action.reason
                result_profit[label] = profit
                result_ticks[label] = idx + 1
                continue
            if not was_armed and getattr(state, "be_armed", False):
                best_profit[label] = profit
            if profit_exits_on_tick and getattr(state, "be_armed", False):
                if best_profit[label] is None or profit > best_profit[label]:
                    best_profit[label] = profit
                if tier_width > 0.0 and 0.0 < profit < best_profit[label]:
                    current_tier = (int(best_profit[label] / tier_width)) * tier_width
                    if current_tier > 0.0 and profit <= current_tier:
                        resolved[label] = True
                        outcome[label] = "staircase_trail_breach"
                        result_profit[label] = profit
                        result_ticks[label] = idx + 1

    for label in labels:
        if not resolved[label]:
            outcome[label] = "exhausted"
            result_profit[label] = profit
            result_ticks[label] = min(len(ticks), max_ticks)

    result = {}
    for label in labels:
        result[f"{label}_outcome"] = outcome[label]
        result[f"{label}_profit"] = result_profit[label]
        result[f"{label}_ticks"] = result_ticks[label]
    return result


def simulate_full_lifecycle_staircase_cap_sweep(
    *,
    exit_trades: list,
    symbol: str,
    side: str,
    entry_time: datetime.datetime,
    lot: float,
    contract_size: float,
    max_ticks: int,
    needs_conversion: bool,
    max_entry_spread_pips: Optional[float] = None,
    n_tick_confirmation: Optional[int] = None,
    candle_close_price: float = 0.0,
    pip_size: float = 0.0001,
    tier_width: float,
    first_tier: Optional[float] = None,
) -> dict:
    """Like `simulate_full_lifecycle_cap_sweep`, but every candidate uses
    the SAME staircase trail (`tier_width`/`first_tier`, fixed across all
    candidates) instead of each candidate's own real `ProfitExitManager`
    -- only the post-BE loss cap value (each candidate's own
    `LossExitManager`) varies. Tests cap retuning against the NEW trail
    shape rather than the original percentage-of-peak one.

    Returns `{"<label>_outcome", "<label>_profit", "<label>_ticks"}` for
    every `(label, exit_trade)` pair in `exit_trades`.
    """
    first_tier = tier_width if first_tier is None else first_tier
    labels = [label for label, _ in exit_trades]
    ticks = mt5.copy_ticks_from(symbol, entry_time, max_ticks, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        result = {}
        for label in labels:
            result[f"{label}_outcome"] = "no_ticks"
            result[f"{label}_profit"] = None
            result[f"{label}_ticks"] = 0
        return result

    if n_tick_confirmation and n_tick_confirmation > 1:
        ci = _confirm_entry_tick(
            ticks, side, candle_close_price if candle_close_price else float(ticks[0]["bid"]),
            n_tick_confirmation,
        )
        if ci is None:
            return {"outcome": "unconfirmed", "profit": None, "ticks_used": 0, "entry_price": None}
        ticks = ticks[ci:]
    entry_tick = ticks[0]
    if max_entry_spread_pips is not None:
        spread_pips = _entry_spread_pips(entry_tick, pip_size)
        if spread_pips > max_entry_spread_pips:
            return {"outcome": "skipped_wide_spread", "profit": None,
                    "ticks_used": 0, "entry_price": None, "entry_spread_pips": spread_pips}
    entry_price = float(entry_tick["ask"]) if side == "buy" else float(entry_tick["bid"])
    pos_type = 0 if side == "buy" else 1

    states = {label: PosState(anchor=0.0, prev_price=0.0) for label in labels}
    best_profit: dict[str, Optional[float]] = {label: None for label in labels}
    resolved = {label: False for label in labels}
    outcome: dict[str, Optional[str]] = {label: None for label in labels}
    result_profit: dict[str, Optional[float]] = {label: None for label in labels}
    result_ticks: dict[str, Optional[int]] = {label: None for label in labels}
    managers = {
        label: (et._loss_manager, bool(getattr(et._config, "profit_exits_on_tick", True)))
        for label, et in exit_trades
    }

    profit = 0.0
    for idx, t in enumerate(ticks[:max_ticks]):
        if all(resolved.values()):
            break
        price = float(t["bid"]) if side == "buy" else float(t["ask"])
        profit = compute_profit(
            side=side, entry_price=entry_price, price=price, lot=lot,
            contract_size=contract_size, needs_conversion=needs_conversion,
        )
        position = {
            "symbol": symbol, "type": pos_type, "ticket": idx,
            "price_open": entry_price, "volume": lot, "profit": profit,
        }
        tick_dict = to_tick_dict(t)

        for label in labels:
            if resolved[label]:
                continue
            loss_manager, profit_exits_on_tick = managers[label]
            state = states[label]
            was_armed = getattr(state, "be_armed", False)
            action = loss_manager.check_exit_on_tick(position, tick_dict, state)
            if action:
                resolved[label] = True
                outcome[label] = action.reason
                result_profit[label] = profit
                result_ticks[label] = idx + 1
                continue
            if not was_armed and getattr(state, "be_armed", False):
                best_profit[label] = profit
            if profit_exits_on_tick and getattr(state, "be_armed", False):
                if best_profit[label] is None or profit > best_profit[label]:
                    best_profit[label] = profit
                if tier_width > 0.0 and 0.0 < profit < best_profit[label] and best_profit[label] >= first_tier:
                    current_tier = first_tier + (int((best_profit[label] - first_tier) / tier_width)) * tier_width
                    if current_tier > 0.0 and profit <= current_tier:
                        resolved[label] = True
                        outcome[label] = "staircase_trail_breach"
                        result_profit[label] = profit
                        result_ticks[label] = idx + 1

    for label in labels:
        if not resolved[label]:
            outcome[label] = "exhausted"
            result_profit[label] = profit
            result_ticks[label] = min(len(ticks), max_ticks)

    result = {}
    for label in labels:
        result[f"{label}_outcome"] = outcome[label]
        result[f"{label}_profit"] = result_profit[label]
        result[f"{label}_ticks"] = result_ticks[label]
    return result


def simulate_chained_legs(
    *,
    exit_trade,
    symbol: str,
    side: str,
    entry_time: datetime.datetime,
    lot: float,
    contract_size: float,
    max_ticks: int,
    needs_conversion: bool,
    max_legs: int = 20,
    flip_direction: bool = False,
    chained_exit_trade=None,
) -> dict:
    """Dual-mode exit: identical to `simulate_full_lifecycle` (same real
    `ExitTrade`, same BE-arming/pre-BE soft-SL/trail/cap rules, unchanged)
    for a single leg -- but when a leg's real outcome is specifically
    `profit_drop_after_be` (hit the post-BE loss cap), immediately opens a
    NEW leg at that same tick's price, with a fresh `PosState` (BE
    arming/trail/cap all reset), and keeps replaying the SAME shared tick
    stream from there. Chains for as long as consecutive legs keep hitting
    the cap (bounded by `max_legs`, a safety cap against pathological
    whipsaw data); stops the moment a leg ends any other way (trail
    capture, pre-BE timeout/soft-SL, or the tick budget runs out).

    `flip_direction` controls what "that direction" means for the
    re-entry: `False` re-enters the SAME direction as the leg that just
    got capped (the original, naive reading); `True` (the actual
    hypothesis under test) treats a cap hit as signal invalidation and
    REVERSES direction on every re-entry -- buy hits -$1 -> open sell;
    if that sell also hits -$1 -> open buy; alternating for as long as
    the chain continues. Either way only `profit_drop_after_be` triggers
    a re-entry -- a leg that fails to reach breakeven at all
    (`failed_to_reach_be`) or hits the pre-BE soft SL (`profit_drop`) is
    treated as terminal, not a signal to keep chaining.

    `chained_exit_trade`, if given, is used for every RE-ENTERED leg
    (leg 2 onward) instead of `exit_trade` -- lets the re-entry run under
    a different (typically looser) post-BE loss cap than leg 1's real
    live value, to test whether the flip just needs more room to reach
    the trail rather than being re-capped at the same tight `-$1`. Leg 1
    always uses `exit_trade` (the real, live-Config cap) unchanged.

    Reports ONE summed realized P&L across however many legs fired, plus
    `|`-joined per-leg outcome/profit/side breakdowns for inspection.
    Same shared tick budget as a single `simulate_full_lifecycle` call --
    chaining does not fetch extra ticks, it spends the existing budget
    across more legs.
    """
    ticks = mt5.copy_ticks_from(symbol, entry_time, max_ticks, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return {
            "outcome": "no_ticks", "profit": None, "ticks_used": 0, "entry_price": None,
            "num_legs": 0, "leg_outcomes": "", "leg_profits": "", "leg_sides": "",
        }

    leg_outcomes: list[str] = []
    leg_profits: list[float] = []
    leg_sides: list[str] = []
    total_profit = 0.0
    first_entry_price: Optional[float] = None
    final_outcome = "exhausted"
    total_ticks_used = 0
    start_idx = 0
    current_side = side

    while start_idx < len(ticks) and len(leg_outcomes) < max_legs:
        leg_exit_trade = exit_trade if len(leg_outcomes) == 0 or chained_exit_trade is None else chained_exit_trade
        loss_manager = leg_exit_trade._loss_manager
        profit_manager = leg_exit_trade._profit_manager
        profit_exits_on_tick = bool(getattr(leg_exit_trade._config, "profit_exits_on_tick", True))

        pos_type = 0 if current_side == "buy" else 1
        entry_tick = ticks[start_idx]
        entry_price = float(entry_tick["ask"]) if current_side == "buy" else float(entry_tick["bid"])
        if first_entry_price is None:
            first_entry_price = entry_price

        state = PosState(anchor=0.0, prev_price=0.0)
        leg_profit = 0.0
        leg_outcome: Optional[str] = None
        end_idx = start_idx

        for idx in range(start_idx, len(ticks)):
            t = ticks[idx]
            price = float(t["bid"]) if current_side == "buy" else float(t["ask"])
            leg_profit = compute_profit(
                side=current_side, entry_price=entry_price, price=price, lot=lot,
                contract_size=contract_size, needs_conversion=needs_conversion,
            )
            position = {
                "symbol": symbol, "type": pos_type, "ticket": idx,
                "price_open": entry_price, "volume": lot, "profit": leg_profit,
            }
            tick_dict = to_tick_dict(t)
            action = loss_manager.check_exit_on_tick(position, tick_dict, state)
            if action:
                leg_outcome = action.reason
                end_idx = idx
                break
            if profit_exits_on_tick:
                action = profit_manager.check_exit_on_tick(position, tick_dict, state)
                if action:
                    leg_outcome = action.reason
                    end_idx = idx
                    break
        else:
            leg_outcome = "exhausted"
            end_idx = len(ticks) - 1

        leg_outcomes.append(leg_outcome)
        leg_profits.append(leg_profit)
        leg_sides.append(current_side)
        total_profit += leg_profit
        final_outcome = leg_outcome
        total_ticks_used = end_idx + 1

        if leg_outcome == "profit_drop_after_be" and end_idx + 1 < len(ticks):
            start_idx = end_idx + 1
            if flip_direction:
                current_side = "sell" if current_side == "buy" else "buy"
            continue
        break

    if len(leg_outcomes) >= max_legs and leg_outcomes[-1] == "profit_drop_after_be":
        final_outcome = "max_legs_reached"

    return {
        "outcome": final_outcome,
        "profit": total_profit,
        "ticks_used": total_ticks_used,
        "entry_price": first_entry_price,
        "num_legs": len(leg_outcomes),
        "leg_outcomes": "|".join(leg_outcomes),
        "leg_profits": "|".join(f"{p:.2f}" for p in leg_profits),
        "leg_sides": "|".join(leg_sides),
    }


def simulate_hedge_on_cap(
    *,
    exit_trade,
    hedge_exit_trade=None,
    original_exit_trade=None,
    symbol: str,
    side: str,
    entry_time: datetime.datetime,
    lot: float,
    contract_size: float,
    max_ticks: int,
    needs_conversion: bool,
    max_entry_spread_pips: Optional[float] = None,
    n_tick_confirmation: Optional[int] = None,
    candle_close_price: float = 0.0,
    pip_size: float = 0.0001,
) -> dict:
    """Hedge, not sequential re-entry: runs the original position normally
    (real ExitTrade -- BE arm, pre-BE soft-SL/timeout, trail, cap) until
    the FIRST time it would hit the post-BE loss cap. At that exact tick,
    instead of closing it, opens a SECOND position in the opposite
    direction at the same price (a fresh `PosState`, the real ExitTrade
    rules -- BE arm/pre-BE/trail/cap, unchanged), while the original
    keeps running with its own trailing stop still live, so it can still
    close for a real profit if price fully recovers.

    From that fork point, an equal-size buy+sell pair's COMBINED P&L is
    mathematically frozen tick to tick -- one side's gain is exactly the
    other's loss -- until one side actually closes. This function tracks
    both independently from the fork, each closing on its own terms (or
    marked at the tick budget cutoff if still open when ticks run out),
    and reports each leg's own final outcome/profit plus the summed
    total. If the original never hits the cap at all, no hedge opens and
    this is identical to `simulate_full_lifecycle` for that trade.

    `hedge_exit_trade`, if given, is used for the hedge leg instead of
    `exit_trade` (e.g. to give the hedge its own different cap/trail);
    defaults to reusing `exit_trade` for both.

    `original_exit_trade`, if given, re-enables a REAL (wider) loss cap
    on the original from the fork point onward, instead of leaving it
    fully uncapped -- e.g. a `-$5`/`-$10` floor so a persistent one-way
    move can't bleed the held-open original indefinitely while the tick
    budget runs out (`exhausted`, marked at an arbitrary cutoff price).
    Default `None` keeps the original fully uncapped past the fork (only
    the trail can close it), the original behavior.
    """
    ticks = mt5.copy_ticks_from(symbol, entry_time, max_ticks, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return {
            "outcome": "no_ticks", "profit": None, "ticks_used": 0, "entry_price": None,
            "hedge_opened": False, "leg_outcomes": "", "leg_profits": "", "leg_sides": "",
        }

    hedge_exit_trade = hedge_exit_trade or exit_trade
    loss_manager = exit_trade._loss_manager
    profit_manager = exit_trade._profit_manager
    profit_exits_on_tick = bool(getattr(exit_trade._config, "profit_exits_on_tick", True))

    if n_tick_confirmation and n_tick_confirmation > 1:
        ci = _confirm_entry_tick(
            ticks, side, candle_close_price if candle_close_price else float(ticks[0]["bid"]),
            n_tick_confirmation,
        )
        if ci is None:
            return {"outcome": "unconfirmed", "profit": None, "ticks_used": 0, "entry_price": None}
        ticks = ticks[ci:]
    entry_tick = ticks[0]
    if max_entry_spread_pips is not None:
        spread_pips = _entry_spread_pips(entry_tick, pip_size)
        if spread_pips > max_entry_spread_pips:
            return {"outcome": "skipped_wide_spread", "profit": None,
                    "ticks_used": 0, "entry_price": None, "entry_spread_pips": spread_pips}
    entry_price = float(entry_tick["ask"]) if side == "buy" else float(entry_tick["bid"])
    pos_type = 0 if side == "buy" else 1
    state = PosState(anchor=0.0, prev_price=0.0)

    profit = 0.0
    outcome: Optional[str] = None
    fork_idx: Optional[int] = None
    idx = -1
    for idx, t in enumerate(ticks):
        price = float(t["bid"]) if side == "buy" else float(t["ask"])
        profit = compute_profit(
            side=side, entry_price=entry_price, price=price, lot=lot,
            contract_size=contract_size, needs_conversion=needs_conversion,
        )
        position = {
            "symbol": symbol, "type": pos_type, "ticket": idx,
            "price_open": entry_price, "volume": lot, "profit": profit,
        }
        tick_dict = to_tick_dict(t)
        action = loss_manager.check_exit_on_tick(position, tick_dict, state)
        if action:
            outcome = action.reason
            break
        if profit_exits_on_tick:
            action = profit_manager.check_exit_on_tick(position, tick_dict, state)
            if action:
                outcome = action.reason
                break
    else:
        outcome = "exhausted"

    if outcome != "profit_drop_after_be" or idx + 1 >= len(ticks):
        # Never hit the cap (or ran out of ticks right at the cap tick) --
        # no hedge opens, identical to a single simulate_full_lifecycle leg.
        return {
            "outcome": outcome,
            "profit": profit,
            "ticks_used": idx + 1,
            "entry_price": entry_price,
            "hedge_opened": False,
            "leg_outcomes": outcome,
            "leg_profits": f"{profit:.2f}",
            "leg_sides": side,
        }

    fork_idx = idx + 1
    hedge_side = "sell" if side == "buy" else "buy"
    hedge_entry_tick = ticks[fork_idx]
    hedge_entry_price = float(hedge_entry_tick["ask"]) if hedge_side == "buy" else float(hedge_entry_tick["bid"])
    hedge_pos_type = 0 if hedge_side == "buy" else 1
    hedge_state = PosState(anchor=0.0, prev_price=0.0)
    hedge_loss_manager = hedge_exit_trade._loss_manager
    hedge_profit_manager = hedge_exit_trade._profit_manager
    hedge_profit_exits_on_tick = bool(getattr(hedge_exit_trade._config, "profit_exits_on_tick", True))
    original_loss_manager = original_exit_trade._loss_manager if original_exit_trade is not None else None

    original_closed = False
    original_final_profit = profit  # value at the fork tick; updated below
    original_outcome = "held_open"
    hedge_closed = False
    hedge_final_profit = 0.0
    hedge_outcome: Optional[str] = None
    last_idx = fork_idx - 1

    for idx2 in range(fork_idx, len(ticks)):
        t = ticks[idx2]
        last_idx = idx2

        if not original_closed:
            orig_price = float(t["bid"]) if side == "buy" else float(t["ask"])
            orig_profit = compute_profit(
                side=side, entry_price=entry_price, price=orig_price, lot=lot,
                contract_size=contract_size, needs_conversion=needs_conversion,
            )
            original_final_profit = orig_profit
            orig_position = {
                "symbol": symbol, "type": pos_type, "ticket": idx2,
                "price_open": entry_price, "volume": lot, "profit": orig_profit,
            }
            tick_dict = to_tick_dict(t)
            action = None
            if original_loss_manager is not None:
                # A real, wider cap re-enabled for the held-open original --
                # bounds the tail risk instead of leaving it fully uncapped.
                action = original_loss_manager.check_exit_on_tick(orig_position, tick_dict, state)
            if action:
                original_outcome = action.reason
                original_closed = True
            elif profit_exits_on_tick:
                action = profit_manager.check_exit_on_tick(orig_position, tick_dict, state)
                if action:
                    original_outcome = action.reason
                    original_closed = True

        if not hedge_closed:
            hedge_price = float(t["bid"]) if hedge_side == "buy" else float(t["ask"])
            hedge_profit = compute_profit(
                side=hedge_side, entry_price=hedge_entry_price, price=hedge_price, lot=lot,
                contract_size=contract_size, needs_conversion=needs_conversion,
            )
            hedge_final_profit = hedge_profit
            hedge_position = {
                "symbol": symbol, "type": hedge_pos_type, "ticket": idx2,
                "price_open": hedge_entry_price, "volume": lot, "profit": hedge_profit,
            }
            tick_dict = to_tick_dict(t)
            action = hedge_loss_manager.check_exit_on_tick(hedge_position, tick_dict, hedge_state)
            if action:
                hedge_outcome = action.reason
                hedge_closed = True
            elif hedge_profit_exits_on_tick:
                action = hedge_profit_manager.check_exit_on_tick(hedge_position, tick_dict, hedge_state)
                if action:
                    hedge_outcome = action.reason
                    hedge_closed = True

        if original_closed and hedge_closed:
            break

    if not original_closed:
        original_outcome = "exhausted"
    if hedge_outcome is None:
        hedge_outcome = "exhausted"

    total_profit = original_final_profit + hedge_final_profit

    return {
        "outcome": f"{original_outcome}+{hedge_outcome}",
        "profit": total_profit,
        "ticks_used": last_idx + 1,
        "entry_price": entry_price,
        "hedge_opened": True,
        "leg_outcomes": f"{original_outcome}|{hedge_outcome}",
        "leg_profits": f"{original_final_profit:.2f}|{hedge_final_profit:.2f}",
        "leg_sides": f"{side}|{hedge_side}",
    }


def simulate_full_lifecycle_unified_stop(
    *,
    exit_trade,
    symbol: str,
    side: str,
    entry_time: datetime.datetime,
    lot: float,
    contract_size: float,
    max_ticks: int,
    needs_conversion: bool,
    max_entry_spread_pips: Optional[float] = None,
    n_tick_confirmation: Optional[int] = None,
    candle_close_price: float = 0.0,
    pip_size: float = 0.0001,
    trail_gap_pct: float,
    trail_gap_floor_money: float,
) -> dict:
    """Same pre-BE phase as `simulate_full_lifecycle` (the real
    `LossExitManager.check_exit_on_tick` handles arming/soft-SL/timeout
    unchanged), but replaces the POST-BE phase's real two-manager
    sequence (`LossExitManager`'s -$1 cap, then -- only while profit is
    still positive -- `ProfitExitManager`'s trail) with a single
    combined stop: `trigger = peak - max(trail_gap_floor_money,
    trail_gap_pct * peak)`, checked every tick regardless of sign.

    This tests a specific hypothesis: the real trail can only fire while
    `0 < profit < peak` (see `ProfitExitManager.check_exit_on_tick`) --
    if a fast tick-to-tick move jumps straight from just-above-trigger to
    already non-positive in one step (easy when the trigger window is a
    few cents wide for a small peak), the real trail's check never runs
    that tick, and only the separate loss-manager cap (waiting much
    lower, at -post_be_loss_cap_money) can catch it. A unified check with
    no positivity requirement would instead catch that same tick at
    wherever it actually landed -- likely still a loss, but a much
    smaller one than falling all the way to the cap.

    Returns the same shape as `simulate_full_lifecycle`, with `outcome`
    in {"failed_to_reach_be", "profit_drop"} (real pre-BE outcomes,
    unchanged), "unified_stop" (the new combined post-BE exit), or
    "exhausted".
    """
    ticks = mt5.copy_ticks_from(symbol, entry_time, max_ticks, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return {"outcome": "no_ticks", "profit": None, "ticks_used": 0, "entry_price": None}

    if n_tick_confirmation and n_tick_confirmation > 1:
        ci = _confirm_entry_tick(
            ticks, side, candle_close_price if candle_close_price else float(ticks[0]["bid"]),
            n_tick_confirmation,
        )
        if ci is None:
            return {"outcome": "unconfirmed", "profit": None, "ticks_used": 0, "entry_price": None}
        ticks = ticks[ci:]
    entry_tick = ticks[0]
    if max_entry_spread_pips is not None:
        spread_pips = _entry_spread_pips(entry_tick, pip_size)
        if spread_pips > max_entry_spread_pips:
            return {"outcome": "skipped_wide_spread", "profit": None,
                    "ticks_used": 0, "entry_price": None, "entry_spread_pips": spread_pips}
    entry_price = float(entry_tick["ask"]) if side == "buy" else float(entry_tick["bid"])
    pos_type = 0 if side == "buy" else 1

    state = PosState(anchor=0.0, prev_price=0.0)
    loss_manager = exit_trade._loss_manager

    profit = 0.0
    outcome = None
    idx = -1
    be_arm_ticks: Optional[int] = None
    peak: Optional[float] = None
    for idx, t in enumerate(ticks[:max_ticks]):
        price = float(t["bid"]) if side == "buy" else float(t["ask"])
        profit = compute_profit(
            side=side, entry_price=entry_price, price=price, lot=lot,
            contract_size=contract_size, needs_conversion=needs_conversion,
        )

        if not getattr(state, "be_armed", False):
            position = {
                "symbol": symbol,
                "type": pos_type,
                "ticket": idx,
                "price_open": entry_price,
                "volume": lot,
                "profit": profit,
            }
            action = loss_manager.check_exit_on_tick(position, to_tick_dict(t), state)
            if action:
                outcome = action.reason
                break
            if getattr(state, "be_armed", False):
                be_arm_ticks = idx
                peak = profit
            continue

        if peak is None or profit > peak:
            peak = profit
        gap = max(trail_gap_floor_money, trail_gap_pct * peak)
        trigger = peak - gap
        if profit <= trigger:
            outcome = "unified_stop"
            break

    if outcome is None:
        outcome = "exhausted"

    return {
        "outcome": outcome,
        "profit": profit,
        "ticks_used": idx + 1,
        "entry_price": entry_price,
        "post_be_peak_profit": peak,
        "be_arm_ticks": be_arm_ticks,
    }



def simulate_full_lifecycle_cap_sweep(
    *,
    exit_trades: list[tuple[str, Any]],
    symbol: str,
    side: str,
    entry_time: datetime.datetime,
    lot: float,
    contract_size: float,
    max_ticks: int,
    needs_conversion: bool,
    max_entry_spread_pips: Optional[float] = None,
    n_tick_confirmation: Optional[int] = None,
    candle_close_price: float = 0.0,
    pip_size: float = 0.0001,
) -> dict:
    """Like `simulate_full_lifecycle`, but replays every `(label,
    exit_trade)` pair in `exit_trades` against the same real tick stream in
    a single pass -- each candidate has its own independent `PosState`, own
    `LossExitManager` (a different `post_be_loss_cap_money`), and the SAME
    real `ProfitExitManager` trail formula, so only the cap value varies
    and every candidate's outcome reflects the real, complete combined
    system rather than the cap in isolation. A candidate stops updating
    once it resolves (its own loss or profit exit); others continue
    independently over the same price path.

    Returns `{"<label>_outcome", "<label>_profit", "<label>_ticks"}` for
    every label in `exit_trades`.
    """
    ticks = mt5.copy_ticks_from(symbol, entry_time, max_ticks, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        result = {}
        for label, _ in exit_trades:
            result[f"{label}_outcome"] = "no_ticks"
            result[f"{label}_profit"] = None
            result[f"{label}_ticks"] = 0
        return result

    if n_tick_confirmation and n_tick_confirmation > 1:
        ci = _confirm_entry_tick(
            ticks, side, candle_close_price if candle_close_price else float(ticks[0]["bid"]),
            n_tick_confirmation,
        )
        if ci is None:
            return {"outcome": "unconfirmed", "profit": None, "ticks_used": 0, "entry_price": None}
        ticks = ticks[ci:]
    entry_tick = ticks[0]
    if max_entry_spread_pips is not None:
        spread_pips = _entry_spread_pips(entry_tick, pip_size)
        if spread_pips > max_entry_spread_pips:
            return {"outcome": "skipped_wide_spread", "profit": None,
                    "ticks_used": 0, "entry_price": None, "entry_spread_pips": spread_pips}
    entry_price = float(entry_tick["ask"]) if side == "buy" else float(entry_tick["bid"])
    pos_type = 0 if side == "buy" else 1

    states = {label: PosState(anchor=0.0, prev_price=0.0) for label, _ in exit_trades}
    resolved = {label: False for label, _ in exit_trades}
    outcome: dict[str, Optional[str]] = {label: None for label, _ in exit_trades}
    result_profit: dict[str, Optional[float]] = {label: None for label, _ in exit_trades}
    result_ticks: dict[str, Optional[int]] = {label: None for label, _ in exit_trades}
    managers = {
        label: (
            et._loss_manager,
            et._profit_manager,
            bool(getattr(et._config, "profit_exits_on_tick", True)),
        )
        for label, et in exit_trades
    }

    profit = 0.0
    for idx, t in enumerate(ticks[:max_ticks]):
        if all(resolved.values()):
            break
        price = float(t["bid"]) if side == "buy" else float(t["ask"])
        profit = compute_profit(
            side=side, entry_price=entry_price, price=price, lot=lot,
            contract_size=contract_size, needs_conversion=needs_conversion,
        )
        position = {
            "symbol": symbol, "type": pos_type, "ticket": idx,
            "price_open": entry_price, "volume": lot, "profit": profit,
        }
        tick_dict = to_tick_dict(t)

        for label, _ in exit_trades:
            if resolved[label]:
                continue
            loss_manager, profit_manager, profit_exits_on_tick = managers[label]
            state = states[label]
            action = loss_manager.check_exit_on_tick(position, tick_dict, state)
            if action:
                resolved[label] = True
                outcome[label] = action.reason
                result_profit[label] = profit
                result_ticks[label] = idx + 1
                continue
            if profit_exits_on_tick:
                action = profit_manager.check_exit_on_tick(position, tick_dict, state)
                if action:
                    resolved[label] = True
                    outcome[label] = action.reason
                    result_profit[label] = profit
                    result_ticks[label] = idx + 1

    for label, _ in exit_trades:
        if not resolved[label]:
            outcome[label] = "exhausted"
            result_profit[label] = profit
            result_ticks[label] = min(len(ticks), max_ticks)

    result = {}
    for label, _ in exit_trades:
        result[f"{label}_outcome"] = outcome[label]
        result[f"{label}_profit"] = result_profit[label]
        result[f"{label}_ticks"] = result_ticks[label]
    return result


def _pre_be_threshold_label(c: float) -> str:
    return f"pre{str(c).replace('.', '_')}"


def simulate_pre_be_threshold_sweep(
    *,
    exit_trades: list[tuple[str, Any]],
    symbol: str,
    side: str,
    entry_time: datetime.datetime,
    lot: float,
    contract_size: float,
    max_ticks: int,
    needs_conversion: bool,
    max_entry_spread_pips: Optional[float] = None,
    n_tick_confirmation: Optional[int] = None,
    candle_close_price: float = 0.0,
    pip_size: float = 0.0001,
) -> dict:
    """Thread 2 (docs/exit-strategy-open-threads.md): replays every
    `(label, exit_trade)` pair in `exit_trades` -- each a full `ExitTrade`
    built with a different `max_loss_money`, everything else at Config's
    real values -- against the same real tick stream in a single pass,
    using only the real `LossExitManager`'s pre-breakeven branch (soft-SL
    money threshold + the arming-ticks timeout). Deliberately reuses the
    real manager rather than reimplementing that state machine: the
    arming-ticks timeout check happens on the tick *after* the last
    soft-SL-checked one, not the same tick, a timing subtlety not worth
    risking a subtly-wrong reimplementation of for money-moving logic.

    Stops each candidate the moment it resolves: an exit action
    (`profit_drop` -> "soft_sl", `failed_to_reach_be` -> "timed_out"), or
    `state.be_armed` becoming true with no action ("reached_be") --
    mirrors `simulate_pre_be_phase`'s own stopping rule. Post-breakeven
    behavior is out of scope here, same as every other pre-BE-only
    simulation in this script.

    Whether/when breakeven arms is identical for every candidate
    (`is_break_even` only looks at profit >= 0.0, independent of any
    threshold) -- only each candidate's own soft-SL breach point can
    differ, so every candidate shares the same underlying price path and
    breakeven timing, just resolving earlier or later depending on its
    own threshold.

    Returns `{"<label>_outcome", "<label>_profit", "<label>_ticks"}` for
    every label in `exit_trades`.
    """
    ticks = mt5.copy_ticks_from(symbol, entry_time, max_ticks, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        result = {}
        for label, _ in exit_trades:
            result[f"{label}_outcome"] = "no_ticks"
            result[f"{label}_profit"] = None
            result[f"{label}_ticks"] = 0
        return result

    if n_tick_confirmation and n_tick_confirmation > 1:
        ci = _confirm_entry_tick(
            ticks, side, candle_close_price if candle_close_price else float(ticks[0]["bid"]),
            n_tick_confirmation,
        )
        if ci is None:
            return {"outcome": "unconfirmed", "profit": None, "ticks_used": 0, "entry_price": None}
        ticks = ticks[ci:]
    entry_tick = ticks[0]
    if max_entry_spread_pips is not None:
        spread_pips = _entry_spread_pips(entry_tick, pip_size)
        if spread_pips > max_entry_spread_pips:
            return {"outcome": "skipped_wide_spread", "profit": None,
                    "ticks_used": 0, "entry_price": None, "entry_spread_pips": spread_pips}
    entry_price = float(entry_tick["ask"]) if side == "buy" else float(entry_tick["bid"])
    pos_type = 0 if side == "buy" else 1

    states = {label: PosState(anchor=0.0, prev_price=0.0) for label, _ in exit_trades}
    resolved = {label: False for label, _ in exit_trades}
    outcome: dict[str, Optional[str]] = {label: None for label, _ in exit_trades}
    result_profit: dict[str, Optional[float]] = {label: None for label, _ in exit_trades}
    result_ticks: dict[str, Optional[int]] = {label: None for label, _ in exit_trades}
    loss_managers = {label: et._loss_manager for label, et in exit_trades}

    profit = 0.0
    for idx, t in enumerate(ticks[:max_ticks]):
        if all(resolved.values()):
            break
        price = float(t["bid"]) if side == "buy" else float(t["ask"])
        profit = compute_profit(
            side=side, entry_price=entry_price, price=price, lot=lot,
            contract_size=contract_size, needs_conversion=needs_conversion,
        )
        position = {
            "symbol": symbol, "type": pos_type, "ticket": idx,
            "price_open": entry_price, "volume": lot, "profit": profit,
        }
        tick_dict = to_tick_dict(t)

        for label, _ in exit_trades:
            if resolved[label]:
                continue
            loss_manager = loss_managers[label]
            state = states[label]
            action = loss_manager.check_exit_on_tick(position, tick_dict, state)
            if action:
                resolved[label] = True
                outcome[label] = "soft_sl" if action.reason == "profit_drop" else "timed_out"
                result_profit[label] = profit
                result_ticks[label] = idx + 1
                continue
            if getattr(state, "be_armed", False):
                resolved[label] = True
                outcome[label] = "reached_be"
                result_profit[label] = profit
                result_ticks[label] = idx + 1

    for label, _ in exit_trades:
        if not resolved[label]:
            outcome[label] = "exhausted"
            result_profit[label] = profit
            result_ticks[label] = min(len(ticks), max_ticks)

    result = {}
    for label, _ in exit_trades:
        result[f"{label}_outcome"] = outcome[label]
        result[f"{label}_profit"] = result_profit[label]
        result[f"{label}_ticks"] = result_ticks[label]
    return result


def run(
    symbol: str,
    weeks: float,
    start_pos: int,
    lot: float,
    max_ticks: int,
    run_counterfactual: bool,
    counterfactual_max_ticks: int,
    disable_timeout: bool,
    measure_post_be: bool,
    post_be_max_ticks: int,
    simulate_trail: bool = False,
    full_lifecycle: bool = False,
    full_lifecycle_max_ticks: int = 4000,
    sweep_post_be_cap: bool = False,
    sweep_pre_be_threshold: bool = False,
    config: Any = Config,
    invert_signal: bool = False,
    do_measure_entry_excursion: bool = False,
    entry_excursion_max_ticks: int = 4000,
    trail_gap_pct: Optional[float] = None,
    trail_gap_floor_money: Optional[float] = None,
    post_be_loss_cap: Optional[float] = None,
    pre_be_loss_threshold: Optional[float] = None,
    be_arming_ticks: Optional[int] = None,
    max_entry_spread_pips: Optional[float] = None,
    n_tick_confirmation: Optional[int] = None,
    atr_normalize: bool = False,
    atr_baseline_pips: float = 1.0,
    unified_post_be_stop: bool = False,
    chain_on_cap: bool = False,
    chain_max_legs: int = 20,
    chain_flip_direction: bool = False,
    chain_loss_cap: Optional[float] = None,
    hedge_on_cap: bool = False,
    original_loss_cap: Optional[float] = None,
    single_timeframe: bool = False,
    indicator_names: Optional[list] = None,
    staircase_trail: bool = False,
    staircase_tier_width: float = 1.0,
    staircase_first_tier: Optional[float] = None,
    sweep_staircase_tiers: bool = False,
    sweep_staircase_cap: bool = False,
    start_date: Optional[datetime.datetime] = None,
    end_date: Optional[datetime.datetime] = None,
) -> None:
    if not mt5.initialize():
        print("MT5 initialization failed.")
        sys.exit(1)

    if invert_signal:
        print(f"[{symbol}] --invert-signal is ON: trading the OPPOSITE of every generated signal.")

    if disable_timeout:
        max_ticks = max(max_ticks, counterfactual_max_ticks)

    market_data = MarketData()
    broker = Broker(TradingMode.LIVE)
    risk_manager = create_risk_manager(broker)
    contract_size = broker.get_lot_value(symbol)
    sl_pips = float(getattr(Config, "DEFAULT_SL_PIPS", 5.0) or 5.0)
    pip_size = broker.get_pip_size(symbol)
    sl_price_distance = pip_size * sl_pips
    fetch_ticks = max_ticks
    if run_counterfactual:
        fetch_ticks = max(fetch_ticks, counterfactual_max_ticks)
    if measure_post_be or simulate_trail:
        fetch_ticks = max(fetch_ticks, max_ticks + post_be_max_ticks)

    account_info = mt5.account_info()
    account_currency = account_info.currency if account_info else "USD"
    needs_conversion = profit_needs_conversion(symbol, account_currency)
    if needs_conversion:
        print(f"[{symbol}] profit currency differs from account currency ({account_currency}) -- converting via live tick price each tick.")

    tf_entry = getattr(Config, "TF_ENTRY", mt5.TIMEFRAME_M1)
    tf_confirm = getattr(Config, "TF_CONFIRM", mt5.TIMEFRAME_M5)
    tf_bias = getattr(Config, "TF_BIAS", mt5.TIMEFRAME_M15)
    entry_seconds = TF_SECONDS[tf_entry]

    m1_count = int(weeks * M1_BARS_PER_TRADING_WEEK)
    warmup_start: Optional[datetime.datetime] = None
    if start_date is not None:
        # Pinned window: fetch a warmup margin BEFORE the requested start so the
        # first signals have the same indicator history as any other candle,
        # then skip those warmup candles in the signal loop below.
        end = end_date or (start_date + datetime.timedelta(weeks=weeks))
        warmup_start = start_date - datetime.timedelta(days=WARMUP_DAYS)
        m1_candles = market_data.get_candles_range(symbol, tf_entry, warmup_start, end)
        m5_candles = market_data.get_candles_range(symbol, tf_confirm, warmup_start, end)
        m15_candles = market_data.get_candles_range(symbol, tf_bias, warmup_start, end)
        for seq in (m1_candles, m5_candles, m15_candles):
            for c in seq:
                c["symbol"] = symbol
    else:
        m1_candles = fetch_history(market_data, symbol, tf_entry, m1_count, start_pos)
        m5_count = max(int(m1_count / 5), 100)
        m15_count = max(int(m1_count / 15), 100)
        m5_start_pos = max(1, int(start_pos / 5))
        m15_start_pos = max(1, int(start_pos / 15))
        m5_candles = fetch_history(market_data, symbol, tf_confirm, m5_count, m5_start_pos)
        m15_candles = fetch_history(market_data, symbol, tf_bias, m15_count, m15_start_pos)
    if not m5_candles or not m15_candles:
        print(f"No historical M5/M15 candles returned for {symbol}.")
        return

    _signal_candles = [c for c in m1_candles if start_date is None or c["time"] >= start_date]
    if _signal_candles:
        print(
            f"[{symbol}] window: {_signal_candles[0]['time']} .. {_signal_candles[-1]['time']} "
            f"({len(_signal_candles)} M1 candles"
            + (f", pinned via --start-date" if start_date is not None
               else f", via --start-pos {start_pos} -- DRIFTS between runs")
            + ")"
        )

    m5_close_times = [c["time"] + datetime.timedelta(seconds=TF_SECONDS[tf_confirm]) for c in m5_candles]
    m15_close_times = [c["time"] + datetime.timedelta(seconds=TF_SECONDS[tf_bias]) for c in m15_candles]

    indicators = (
        {name: build_indicator(name, config) for name in indicator_names}
        if single_timeframe and indicator_names
        else None
    )
    strategy = strategy_factory(
        config=config, indicators=indicators, use_multi=not single_timeframe, symbol=symbol
    )
    exit_overrides: dict[str, Any] = {}
    if disable_timeout:
        exit_overrides["be_arming_ticks"] = 0
    if trail_gap_pct is not None:
        exit_overrides["trail_gap_pct"] = trail_gap_pct
    if trail_gap_floor_money is not None:
        exit_overrides["trail_gap_floor_money"] = trail_gap_floor_money
    if post_be_loss_cap is not None:
        exit_overrides["post_be_loss_cap_money"] = post_be_loss_cap
    if pre_be_loss_threshold is not None:
        exit_overrides["max_loss_money"] = pre_be_loss_threshold
    if be_arming_ticks is not None:
        exit_overrides["be_arming_ticks"] = be_arming_ticks
    _scaled_exit_trade_cache: dict[float, Any] = {}
    exit_config = ExitTradeConfig(**exit_overrides) if exit_overrides else None
    resolved_trail_gap_pct = (
        trail_gap_pct if trail_gap_pct is not None
        else float(getattr(Config, "EXIT_TRAIL_GAP_PCT", 0.6) or 0.6)
    )
    resolved_trail_gap_floor_money = (
        trail_gap_floor_money if trail_gap_floor_money is not None
        else float(getattr(Config, "EXIT_TRAIL_GAP_FLOOR_MONEY", 2.0) or 2.0)
    )
    exit_trade = create_exit_trade(broker=broker, risk_manager=risk_manager, config=exit_config)

    chained_exit_trade = None
    if chain_on_cap and chain_loss_cap is not None:
        chained_overrides = dict(exit_overrides)
        chained_overrides["post_be_loss_cap_money"] = chain_loss_cap
        chained_exit_trade = create_exit_trade(
            broker=broker, risk_manager=risk_manager, config=ExitTradeConfig(**chained_overrides)
        )

    original_exit_trade = None
    if hedge_on_cap and original_loss_cap is not None:
        original_overrides = dict(exit_overrides)
        original_overrides["post_be_loss_cap_money"] = original_loss_cap
        original_exit_trade = create_exit_trade(
            broker=broker, risk_manager=risk_manager, config=ExitTradeConfig(**original_overrides)
        )

    cap_sweep_trades: list[tuple[str, Any]] = []
    if full_lifecycle and (sweep_post_be_cap or sweep_staircase_cap):
        for c in FULL_LIFECYCLE_CAP_CANDIDATES:
            label = f"cap{str(c).replace('.', '_')}"
            cfg = ExitTradeConfig(post_be_loss_cap_money=c)
            cap_sweep_trades.append((label, create_exit_trade(broker=broker, risk_manager=risk_manager, config=cfg)))

    pre_be_sweep_trades: list[tuple[str, Any]] = []
    if sweep_pre_be_threshold:
        for c in PRE_BE_THRESHOLD_CANDIDATES:
            label = _pre_be_threshold_label(c)
            cfg = ExitTradeConfig(max_loss_money=c)
            pre_be_sweep_trades.append((label, create_exit_trade(broker=broker, risk_manager=risk_manager, config=cfg)))

    results: list[dict] = []

    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            for i, m1_candle in enumerate(m1_candles):
                if warmup_start is not None and m1_candle["time"] < start_date:
                    continue
                closed_by = m1_candle["time"] + datetime.timedelta(seconds=entry_seconds)
                m5_ptr = bisect.bisect_right(m5_close_times, closed_by)
                m15_ptr = bisect.bisect_right(m15_close_times, closed_by)
                if m5_ptr == 0 or m15_ptr == 0:
                    continue

                if single_timeframe:
                    signal = strategy.generate_signal(m1_candles[: i + 1])
                else:
                    candles_by_tf = {
                        tf_entry: m1_candles[: i + 1],
                        tf_confirm: m5_candles[:m5_ptr],
                        tf_bias: m15_candles[:m15_ptr],
                    }
                    signal = strategy.generate_signal(candles_by_tf)
                final_signal = (signal.get("final_signal") or "hold").lower()
                if final_signal not in ("buy", "sell"):
                    continue
                if invert_signal:
                    final_signal = "sell" if final_signal == "buy" else "buy"

                # ATR at entry is needed BEFORE the sim when --atr-normalize is on
                # (it sizes that trade's thresholds); it is also logged as metadata
                # for every run, so compute it once here either way.
                atr_value = calculate_atr(
                    m1_candles[: i + 1],
                    period=int(getattr(config, "ENTRY_ATR_PERIOD", 14) or 14),
                    logger=_metadata_logger,
                )
                trade_exit_trade = exit_trade
                atr_scale = None
                if atr_normalize and full_lifecycle:
                    atr_scale = _atr_scale_factor(atr_value, pip_size, atr_baseline_pips)
                    trade_exit_trade = _scaled_exit_trade(
                        atr_scale,
                        base_overrides=exit_overrides,
                        broker=broker,
                        risk_manager=risk_manager,
                        cache=_scaled_exit_trade_cache,
                    )

                if full_lifecycle and sweep_staircase_cap:
                    sim = simulate_full_lifecycle_staircase_cap_sweep(
                        exit_trades=cap_sweep_trades,
                        symbol=symbol,
                        side=final_signal,
                        entry_time=closed_by,
                        lot=lot,
                        contract_size=contract_size,
                        max_ticks=full_lifecycle_max_ticks,
                        needs_conversion=needs_conversion,
                        tier_width=staircase_tier_width,
                        first_tier=staircase_first_tier,
                    )
                elif full_lifecycle and sweep_post_be_cap:
                    sim = simulate_full_lifecycle_cap_sweep(
                        exit_trades=cap_sweep_trades,
                        symbol=symbol,
                        side=final_signal,
                        entry_time=closed_by,
                        lot=lot,
                        contract_size=contract_size,
                        max_ticks=full_lifecycle_max_ticks,
                        needs_conversion=needs_conversion,
                    )
                elif sweep_pre_be_threshold:
                    sim = simulate_pre_be_threshold_sweep(
                        exit_trades=pre_be_sweep_trades,
                        symbol=symbol,
                        side=final_signal,
                        entry_time=closed_by,
                        lot=lot,
                        contract_size=contract_size,
                        max_ticks=max_ticks,
                        needs_conversion=needs_conversion,
                    )
                elif unified_post_be_stop:
                    sim = simulate_full_lifecycle_unified_stop(
                        exit_trade=exit_trade,
                        symbol=symbol,
                        side=final_signal,
                        entry_time=closed_by,
                        lot=lot,
                        contract_size=contract_size,
                        max_ticks=full_lifecycle_max_ticks,
                        needs_conversion=needs_conversion,
                        trail_gap_pct=resolved_trail_gap_pct,
                        trail_gap_floor_money=resolved_trail_gap_floor_money,
                    )
                elif full_lifecycle and sweep_staircase_tiers:
                    sim = simulate_full_lifecycle_staircase_sweep(
                        exit_trade=exit_trade,
                        symbol=symbol,
                        side=final_signal,
                        entry_time=closed_by,
                        lot=lot,
                        contract_size=contract_size,
                        max_ticks=full_lifecycle_max_ticks,
                        needs_conversion=needs_conversion,
                        tier_widths=STAIRCASE_TIER_CANDIDATES,
                    )
                elif full_lifecycle and staircase_trail:
                    sim = simulate_full_lifecycle_staircase_trail(
                        exit_trade=trade_exit_trade,
                        symbol=symbol,
                        side=final_signal,
                        entry_time=closed_by,
                        lot=lot,
                        contract_size=contract_size,
                        max_ticks=full_lifecycle_max_ticks,
                        needs_conversion=needs_conversion,
                        max_entry_spread_pips=max_entry_spread_pips,
                        pip_size=pip_size,
                        n_tick_confirmation=n_tick_confirmation,
                        candle_close_price=float(m1_candle.get("close") or 0.0),
                        tier_width=staircase_tier_width,
                        first_tier=staircase_first_tier,
                    )
                elif full_lifecycle and chain_on_cap:
                    sim = simulate_chained_legs(
                        exit_trade=exit_trade,
                        symbol=symbol,
                        side=final_signal,
                        entry_time=closed_by,
                        lot=lot,
                        contract_size=contract_size,
                        max_ticks=full_lifecycle_max_ticks,
                        needs_conversion=needs_conversion,
                        max_legs=chain_max_legs,
                        flip_direction=chain_flip_direction,
                        chained_exit_trade=chained_exit_trade,
                    )
                elif full_lifecycle and hedge_on_cap:
                    sim = simulate_hedge_on_cap(
                        exit_trade=exit_trade,
                        original_exit_trade=original_exit_trade,
                        symbol=symbol,
                        side=final_signal,
                        entry_time=closed_by,
                        lot=lot,
                        contract_size=contract_size,
                        max_ticks=full_lifecycle_max_ticks,
                        needs_conversion=needs_conversion,
                    )
                elif full_lifecycle:
                    sim = simulate_full_lifecycle(
                        exit_trade=trade_exit_trade,
                        symbol=symbol,
                        side=final_signal,
                        entry_time=closed_by,
                        lot=lot,
                        contract_size=contract_size,
                        max_ticks=full_lifecycle_max_ticks,
                        needs_conversion=needs_conversion,
                        max_entry_spread_pips=max_entry_spread_pips,
                        pip_size=pip_size,
                        n_tick_confirmation=n_tick_confirmation,
                        candle_close_price=float(m1_candle.get("close") or 0.0),
                    )
                elif do_measure_entry_excursion:
                    sim = measure_entry_excursion(
                        symbol=symbol,
                        side=final_signal,
                        entry_time=closed_by,
                        lot=lot,
                        contract_size=contract_size,
                        max_ticks=entry_excursion_max_ticks,
                        needs_conversion=needs_conversion,
                    )
                else:
                    sim = simulate_pre_be_phase(
                        exit_trade=exit_trade,
                        symbol=symbol,
                        side=final_signal,
                        entry_time=closed_by,
                        lot=lot,
                        contract_size=contract_size,
                        max_ticks=max_ticks,
                        fetch_ticks=fetch_ticks,
                        sl_price_distance=sl_price_distance,
                        run_counterfactual=run_counterfactual,
                        needs_conversion=needs_conversion,
                        measure_post_be=measure_post_be,
                        post_be_max_ticks=post_be_max_ticks,
                        simulate_trail=simulate_trail,
                    )
                indicator_votes = signal.get("indicators") or {}
                num_agree = sum(1 for v in indicator_votes.values() if v == final_signal)
                window = m1_candles[: i + 1]
                rsi_value = _compute_latest_rsi(
                    window, period=int(getattr(config, "ENTRY_RSI_PERIOD", 7))
                )
                hist = _macd_histogram(
                    window, fast_period=7, slow_period=16, signal_period=5,
                    log=_metadata_logger,
                )
                macd_hist_value = hist[0] if hist else None
                if sim.get("outcome") in ("skipped_wide_spread", "unconfirmed"):
                    continue
                results.append(
                    {
                        "time": m1_candle.get("time"),
                        "direction": final_signal,
                        "confidence": signal.get("confidence"),
                        "adx": signal.get("adx"),
                        "m15_bias": signal.get("m15_bias"),
                        "m5_confirm": signal.get("m5_confirm"),
                        "m1_entry": signal.get("m1_entry"),
                        "pullback_completed": signal.get("pullback_completed"),
                        "num_indicators_agree": num_agree if indicator_votes else None,
                        "indicator_votes": "|".join(f"{k}={v}" for k, v in indicator_votes.items()) or None,
                        "rsi_value": rsi_value,
                        "macd_hist_value": macd_hist_value,
                        "atr_value": atr_value,
                        "atr_scale": atr_scale,
                        **sim,
                    }
                )
    finally:
        logging.disable(logging.NOTSET)

    if full_lifecycle and sweep_staircase_cap:
        write_staircase_cap_sweep_csv(results, symbol)
        summarize_staircase_cap_sweep(results, symbol)
    elif full_lifecycle and sweep_post_be_cap:
        write_full_lifecycle_cap_sweep_csv(results, symbol)
        summarize_full_lifecycle_cap_sweep(results, symbol)
    elif sweep_pre_be_threshold:
        write_pre_be_threshold_sweep_csv(results, symbol)
        summarize_pre_be_threshold_sweep(results, symbol)
    elif unified_post_be_stop:
        write_full_lifecycle_csv(results, symbol)
        summarize_full_lifecycle(results, symbol)
    elif full_lifecycle and sweep_staircase_tiers:
        write_staircase_sweep_csv(results, symbol)
        summarize_staircase_sweep(results, symbol)
    elif full_lifecycle and staircase_trail:
        write_full_lifecycle_csv(results, symbol)
        summarize_full_lifecycle(results, symbol)
    elif full_lifecycle and chain_on_cap:
        write_chained_legs_csv(results, symbol)
        summarize_chained_legs(results, symbol)
    elif full_lifecycle and hedge_on_cap:
        write_hedge_csv(results, symbol)
        summarize_hedge(results, symbol)
    elif full_lifecycle:
        write_full_lifecycle_csv(results, symbol)
        summarize_full_lifecycle(results, symbol)
    elif do_measure_entry_excursion:
        write_entry_excursion_csv(results, symbol)
        summarize_entry_excursion(results, symbol)
    else:
        write_results_csv(results, symbol)
        summarize(results, symbol)


def write_results_csv(results: list[dict], symbol: str) -> None:
    if not results:
        return
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{symbol}_exit_strategy_{run_stamp}.csv"
    fieldnames = [
        "time", "direction", "confidence", "adx", "m15_bias", "m5_confirm", "m1_entry", "pullback_completed",
        "num_indicators_agree", "indicator_votes", "rsi_value", "macd_hist_value", "atr_value", "atr_scale",
        "outcome", "profit", "ticks_used", "entry_price",
        "cf_outcome", "cf_profit", "cf_extra_ticks",
        "post_be_peak_profit", "post_be_ticks_to_peak", "post_be_max_drawdown_from_peak",
        "post_be_final_profit", "post_be_ticks_observed",
    ] + [
        f"trail_{name}_{suffix}" for name, _ in TRAIL_RULES
        for suffix in ("profit", "ticks", "triggered", "lm_capped", "lm_capped_in_gap", "activation_ticks")
    ] + [
        "trail_lm_profit", "trail_lm_ticks", "trail_lm_triggered", "trail_lm_reason",
        "gap_recovered", "gap_ticks_to_recover", "gap_min_profit_after_cap", "gap_final_profit",
    ] + [
        f"cap{int(abs(c))}_{suffix}" for c in CAP_THRESHOLDS for suffix in ("profit", "ticks", "triggered")
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"Per-trade log: {path}")


def summarize(results: list[dict], symbol: str) -> None:
    total = len(results)
    if total == 0:
        print(f"No buy/sell signals found for {symbol} in this window.")
        return

    by_outcome: dict[str, list[dict]] = {}
    for r in results:
        by_outcome.setdefault(r["outcome"], []).append(r)

    print(f"\n=== Exit-strategy (pre-breakeven phase) backtest: {symbol} ===")
    print(f"Total simulated trades: {total}")
    for outcome in ("soft_sl", "timed_out", "reached_be", "exhausted", "no_ticks"):
        rows = by_outcome.get(outcome, [])
        if not rows:
            continue
        pct = len(rows) / total * 100.0
        profits = [r["profit"] for r in rows if r["profit"] is not None]
        avg_profit = sum(profits) / len(profits) if profits else 0.0
        avg_ticks = sum(r["ticks_used"] for r in rows) / len(rows)
        print(
            f"  {outcome:12s}: {len(rows):4d} ({pct:5.1f}%)  "
            f"avg_profit=${avg_profit:+.2f}  avg_ticks_to_resolve={avg_ticks:.1f}"
        )

    cut_short = [r for r in results if r.get("cf_outcome")]
    if cut_short:
        print(f"\n--- Counterfactual (no soft SL / no timeout, broker-side SL only) for the {len(cut_short)} cut-short trades ---")
        by_cf: dict[str, list[dict]] = {}
        for r in cut_short:
            by_cf.setdefault(r["cf_outcome"], []).append(r)

        real_total = sum(r["profit"] for r in cut_short if r["profit"] is not None)
        cf_total = sum(r["cf_profit"] for r in cut_short if r.get("cf_profit") is not None)

        for cf_outcome in ("would_have_recovered", "would_have_hit_broker_sl", "still_unresolved"):
            rows = by_cf.get(cf_outcome, [])
            if not rows:
                continue
            pct = len(rows) / len(cut_short) * 100.0
            avg_cf_profit = sum(r["cf_profit"] for r in rows) / len(rows)
            avg_extra_ticks = sum(r["cf_extra_ticks"] for r in rows) / len(rows)
            print(
                f"  {cf_outcome:24s}: {len(rows):3d} ({pct:5.1f}%)  "
                f"avg_cf_profit=${avg_cf_profit:+.2f}  avg_extra_ticks={avg_extra_ticks:.1f}"
            )

        print(f"  Real total P&L on these {len(cut_short)} trades:          ${real_total:+.2f}")
        print(f"  Counterfactual total P&L (no early cut):    ${cf_total:+.2f}")
        print(f"  Difference (real minus counterfactual):     ${real_total - cf_total:+.2f}  "
              f"({'early cuts helped' if real_total > cf_total else 'early cuts hurt' if real_total < cf_total else 'no difference'})")

    def _pctiles(values: list[float], label: str) -> None:
        values = sorted(values)
        n = len(values)
        med = statistics.median(values)
        p10 = values[int(n * 0.10)]
        p90 = values[min(int(n * 0.90), n - 1)]
        avg = statistics.mean(values)
        print(f"  {label:28s}: avg={avg:+.3f}  median={med:+.3f}  p10={p10:+.3f}  p90={p90:+.3f}")

    measured = [r for r in results if r.get("post_be_peak_profit") is not None]
    if measured:
        print(f"\n--- Post-breakeven excursion (observed only, no exit rule applied) for {len(measured)} reached_be trades ---")
        _pctiles([r["post_be_peak_profit"] for r in measured], "peak profit reached ($)")
        _pctiles([r["post_be_ticks_to_peak"] for r in measured], "ticks to reach that peak")
        _pctiles([r["post_be_max_drawdown_from_peak"] for r in measured], "max drawdown from peak ($)")
        _pctiles([r["post_be_final_profit"] for r in measured], "final profit at window end ($)")

    trailed = [r for r in results if r.get(f"trail_{TRAIL_RULES[0][0]}_profit") is not None]
    if trailed:
        print(f"\n--- Trail-rule simulation for {len(trailed)} reached_be trades ---")
        no_exit_total = sum(r["post_be_final_profit"] for r in trailed if r.get("post_be_final_profit") is not None)
        peak_total = sum(r["post_be_peak_profit"] for r in trailed if r.get("post_be_peak_profit") is not None)
        if measured:
            print(f"  {'(reference) hold forever, no exit':32s}: total=${no_exit_total:+.2f}")
            print(f"  {'(reference) exit exactly at peak':32s}: total=${peak_total:+.2f}")
        lm_only = [r["trail_lm_profit"] for r in trailed if r.get("trail_lm_triggered") is True]
        if lm_only:
            cap_hits = sum(1 for r in trailed if r.get("trail_lm_reason") == "profit_drop_after_be")
            lockin_hits = sum(1 for r in trailed if r.get("trail_lm_reason") == "be_recovered_after_unprofit")
            live_cap = float(getattr(Config, "EXIT_POST_BE_LOSS_CAP_MONEY", 5.0) or 5.0)
            print(
                f"  {'(reference) real -$' + f'{live_cap:g}' + ' post-BE cap alone':32s}: "
                f"triggered={len(lm_only):4d}/{len(trailed)}  total_on_those=${sum(lm_only):+.2f}  "
                f"(-${live_cap:g} cap: {cap_hits}, marginal-recovery lock-in: {lockin_hits})"
            )

            capped = [r for r in trailed if r.get("trail_lm_reason") == "profit_drop_after_be"]
            if capped:
                recovered = sum(1 for r in capped if r.get("gap_recovered") is True)
                no_exit_final_total = sum(r["gap_final_profit"] for r in capped if r.get("gap_final_profit") is not None)
                real_capped_total = sum(r["trail_lm_profit"] for r in capped)
                worst = [r["gap_min_profit_after_cap"] for r in capped if r.get("gap_min_profit_after_cap") is not None]
                print(
                    f"  {'  --> if the -$' + f'{live_cap:g}' + ' cap did NOT exist':32s}: "
                    f"{recovered}/{len(capped)} ({recovered/len(capped)*100:.1f}%) later recovered to positive profit; "
                    f"if left alone the whole window, total=${no_exit_final_total:+.2f} vs. real capped total=${real_capped_total:+.2f}"
                )
                if worst:
                    print(f"  {'  --> worst further drawdown after the cap-point':32s}: avg=${sum(worst)/len(worst):+.2f}  min=${min(worst):+.2f}")
        for name, _ in TRAIL_RULES:
            profits = [r[f"trail_{name}_profit"] for r in trailed if r.get(f"trail_{name}_profit") is not None]
            ticks_vals = [r[f"trail_{name}_ticks"] for r in trailed if r.get(f"trail_{name}_ticks") is not None]
            triggered_count = sum(1 for r in trailed if r.get(f"trail_{name}_triggered") is True)
            lm_capped_count = sum(1 for r in trailed if r.get(f"trail_{name}_lm_capped") is True)
            in_gap_count = sum(1 for r in trailed if r.get(f"trail_{name}_lm_capped_in_gap") is True)
            activations = [r[f"trail_{name}_activation_ticks"] for r in trailed if r.get(f"trail_{name}_activation_ticks") not in (None, "")]
            activation_rate = len(activations) / len(trailed) * 100.0 if trailed else 0.0
            total = sum(profits)
            win_rate = sum(1 for p in profits if p > 0) / len(profits) * 100.0 if profits else 0.0
            avg_ticks = sum(ticks_vals) / len(ticks_vals) if ticks_vals else 0.0
            marker = "  <-- ACTIVE (provisional)" if name == ACTIVE_TRAIL_RULE else ""
            print(
                f"  {name:16s}: total=${total:+8.2f}  win_rate={win_rate:5.1f}%  "
                f"triggered={triggered_count:4d}/{len(trailed)}  avg_ticks_held={avg_ticks:.1f}  "
                f"lm_capped={lm_capped_count:4d} (in_gap={in_gap_count})  ever_activated={activation_rate:.1f}%{marker}"
            )

    capped_thresholds = [r for r in results if r.get("cap5_profit") is not None]
    if capped_thresholds:
        print(f"\n--- Thread 4: candidate post-BE loss-cap thresholds for {len(capped_thresholds)} reached_be trades ---")
        for c in CAP_THRESHOLDS:
            tag = f"cap{int(abs(c))}"
            profits = [r[f"{tag}_profit"] for r in capped_thresholds if r.get(f"{tag}_profit") is not None]
            ticks_vals = [r[f"{tag}_ticks"] for r in capped_thresholds if r.get(f"{tag}_ticks") is not None]
            triggered_count = sum(1 for r in capped_thresholds if r.get(f"{tag}_triggered") is True)
            total = sum(profits)
            win_rate = sum(1 for p in profits if p > 0) / len(profits) * 100.0 if profits else 0.0
            avg_ticks = sum(ticks_vals) / len(ticks_vals) if ticks_vals else 0.0
            marker = "  <-- current production value" if c == -float(getattr(Config, "EXIT_POST_BE_LOSS_CAP_MONEY", 5.0) or 5.0) else ""
            print(
                f"  -${abs(c):<5.1f}: total=${total:+8.2f}  win_rate={win_rate:5.1f}%  "
                f"triggered={triggered_count:4d}/{len(capped_thresholds)}  avg_ticks_to_cap={avg_ticks:.1f}{marker}"
            )


def write_full_lifecycle_csv(results: list[dict], symbol: str) -> None:
    if not results:
        return
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{symbol}_full_lifecycle_{run_stamp}.csv"
    fieldnames = [
        "time", "direction", "confidence", "adx", "m15_bias", "m5_confirm", "m1_entry", "pullback_completed",
        "num_indicators_agree", "indicator_votes", "rsi_value", "macd_hist_value", "atr_value", "atr_scale",
        "outcome", "profit", "ticks_used", "entry_price", "post_be_peak_profit", "be_arm_ticks",
        "cf_recovered_to_be", "cf_ticks_to_recover", "cf_min_profit_after_exit",
        "cf_recovered_to_neg1", "cf_ticks_to_recover_neg1",
        "cf_post_recovery_peak", "cf_reversed_after_recovery", "cf_ticks_to_reversal_after_recovery",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"Per-trade log: {path}")


def summarize_full_lifecycle(results: list[dict], symbol: str) -> None:
    total = len(results)
    if total == 0:
        print(f"No buy/sell signals found for {symbol} in this window.")
        return

    by_outcome: dict[str, list[dict]] = {}
    for r in results:
        by_outcome.setdefault(r["outcome"], []).append(r)

    all_profits = [r["profit"] for r in results if r["profit"] is not None]
    total_pnl = sum(all_profits)
    win_rate = sum(1 for p in all_profits if p > 0) / len(all_profits) * 100.0 if all_profits else 0.0

    print(f"\n=== Full-lifecycle backtest (real ExitTrade, entry to final exit): {symbol} ===")
    print(f"Total simulated trades: {total}")
    print(f"Total realized P&L: ${total_pnl:+.2f}  win_rate={win_rate:.1f}%")
    print(f"\nBy real exit reason:")
    for outcome, rows in sorted(by_outcome.items(), key=lambda kv: -len(kv[1])):
        pct = len(rows) / total * 100.0
        profits = [r["profit"] for r in rows if r["profit"] is not None]
        avg_profit = sum(profits) / len(profits) if profits else 0.0
        sub_total = sum(profits)
        avg_ticks = sum(r["ticks_used"] for r in rows) / len(rows)
        print(
            f"  {outcome:28s}: {len(rows):4d} ({pct:5.1f}%)  "
            f"avg_profit=${avg_profit:+.2f}  total=${sub_total:+.2f}  avg_ticks={avg_ticks:.1f}"
        )

    cf_rows = [r for r in results if r.get("cf_recovered_to_be") is not None]
    if cf_rows:
        recovered_rows = [r for r in cf_rows if r["cf_recovered_to_be"]]
        never_rows = [r for r in cf_rows if not r["cf_recovered_to_be"]]
        recover_ticks = [r["cf_ticks_to_recover"] for r in recovered_rows if r.get("cf_ticks_to_recover") is not None]
        avg_recover_ticks = sum(recover_ticks) / len(recover_ticks) if recover_ticks else 0.0
        avg_min_after = sum(r["cf_min_profit_after_exit"] for r in cf_rows) / len(cf_rows)
        avg_min_after_never = sum(r["cf_min_profit_after_exit"] for r in never_rows) / len(never_rows) if never_rows else 0.0
        print(
            f"\nOf {len(cf_rows)} profit_drop_after_be trades, if the -$1 cap hadn't fired and price were just watched afterward: "
            f"{len(recovered_rows)} ({len(recovered_rows) / len(cf_rows) * 100.0:.1f}%) would have recovered to breakeven or better "
            f"(avg {avg_recover_ticks:.1f} ticks to do so); {len(never_rows)} ({len(never_rows) / len(cf_rows) * 100.0:.1f}%) never did "
            f"within the observed window. Avg worst point reached after the cap point: ${avg_min_after:.2f} overall, ${avg_min_after_never:.2f} among those that never recovered."
        )

        reversal_rows = [r for r in recovered_rows if r.get("cf_reversed_after_recovery") is not None]
        if reversal_rows:
            reversed_rows = [r for r in reversal_rows if r["cf_reversed_after_recovery"]]
            held_rows = [r for r in reversal_rows if not r["cf_reversed_after_recovery"]]
            avg_peak_reversed = sum(r["cf_post_recovery_peak"] for r in reversed_rows) / len(reversed_rows) if reversed_rows else 0.0
            avg_peak_held = sum(r["cf_post_recovery_peak"] for r in held_rows) / len(held_rows) if held_rows else 0.0
            print(
                f"Of {len(reversal_rows)} trades that recovered to breakeven, {len(reversed_rows)} ({len(reversed_rows) / len(reversal_rows) * 100.0:.1f}%) "
                f"reversed back below $0 again at some point (avg peak reached before that second reversal: ${avg_peak_reversed:.2f}); "
                f"{len(held_rows)} ({len(held_rows) / len(reversal_rows) * 100.0:.1f}%) never went negative again after recovering "
                f"(avg peak reached: ${avg_peak_held:.2f})."
            )

    arm_ticks = [r["be_arm_ticks"] for r in results if r.get("be_arm_ticks") is not None]
    if arm_ticks:
        avg_arm = sum(arm_ticks) / len(arm_ticks)
        sorted_arm = sorted(arm_ticks)
        median_arm = sorted_arm[len(sorted_arm) // 2]
        print(f"\nOf {len(arm_ticks)} trades that reached breakeven, avg ticks to arm: {avg_arm:.1f}  median: {median_arm}")

    reached_be_rows = [r for r in results if r.get("post_be_peak_profit") is not None]
    if reached_be_rows:
        # Trail trigger = peak - max(floor, pct*peak); since that max() is
        # always >= floor, trigger > 0 (trail structurally able to fire)
        # iff peak > floor -- NOT wherever the floor/pct branches cross
        # (that crossover, floor/pct, is a different, unrelated number).
        floor = float(getattr(Config, "EXIT_TRAIL_GAP_FLOOR_MONEY", 2.0) or 2.0)
        print(f"\nOf {len(reached_be_rows)} trades that reached breakeven, split by real post-BE peak profit reached before final outcome (trail floor=${floor:.2f}):")
        buckets = [
            (f"peak <= $1 (barely above BE)", lambda p: p <= 1.0),
            (f"$1 < peak <= ${floor:.2f} (trail structurally inactive: trigger <= 0)", lambda p: 1.0 < p <= floor),
            (f"peak > ${floor:.2f} (trail structurally active: trigger > 0)", lambda p: p > floor),
        ]
        for label, pred in buckets:
            bucket_rows = [r for r in reached_be_rows if pred(r["post_be_peak_profit"])]
            if not bucket_rows:
                continue
            by_outcome_in_bucket: dict[str, int] = {}
            for r in bucket_rows:
                by_outcome_in_bucket[r["outcome"]] = by_outcome_in_bucket.get(r["outcome"], 0) + 1
            outcome_str = ", ".join(f"{o}={c}" for o, c in sorted(by_outcome_in_bucket.items(), key=lambda kv: -kv[1]))
            print(f"  {label}: {len(bucket_rows)} ({outcome_str})")


def write_chained_legs_csv(results: list[dict], symbol: str) -> None:
    if not results:
        return
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{symbol}_chained_legs_{run_stamp}.csv"
    fieldnames = [
        "time", "direction", "confidence", "adx", "m15_bias", "m5_confirm", "m1_entry", "pullback_completed",
        "num_indicators_agree", "indicator_votes", "rsi_value", "macd_hist_value", "atr_value", "atr_scale",
        "outcome", "profit", "ticks_used", "entry_price", "num_legs", "leg_outcomes", "leg_profits", "leg_sides",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"Per-trade log: {path}")


def summarize_chained_legs(results: list[dict], symbol: str) -> None:
    total = len(results)
    if total == 0:
        print(f"No buy/sell signals found for {symbol} in this window.")
        return

    all_profits = [r["profit"] for r in results if r["profit"] is not None]
    total_pnl = sum(all_profits)
    win_rate = sum(1 for p in all_profits if p > 0) / len(all_profits) * 100.0 if all_profits else 0.0
    all_legs = [r["num_legs"] for r in results if r.get("num_legs") is not None]
    chained = [r for r in results if r.get("num_legs", 1) > 1]

    print(f"\n=== Chained-legs backtest (close at -$1, re-enter same direction, same rules apply): {symbol} ===")
    print(f"Total original signals: {total}")
    print(f"Total realized P&L (summed across all legs): ${total_pnl:+.2f}  win_rate={win_rate:.1f}%")
    print(f"Avg legs per signal: {sum(all_legs) / len(all_legs):.2f}  (max: {max(all_legs) if all_legs else 0})")
    print(f"Signals that re-entered at least once: {len(chained)} ({len(chained) / total * 100.0:.1f}%)")

    by_outcome: dict[str, list[dict]] = {}
    for r in results:
        by_outcome.setdefault(r["outcome"], []).append(r)
    print(f"\nBy FINAL leg's exit reason:")
    for outcome, rows in sorted(by_outcome.items(), key=lambda kv: -len(kv[1])):
        pct = len(rows) / total * 100.0
        profits = [r["profit"] for r in rows if r["profit"] is not None]
        avg_profit = sum(profits) / len(profits) if profits else 0.0
        sub_total = sum(profits)
        print(f"  {outcome:28s}: {len(rows):4d} ({pct:5.1f}%)  avg_total_profit=${avg_profit:+.2f}  total=${sub_total:+.2f}")

    by_legs: dict[int, list[dict]] = {}
    for r in results:
        by_legs.setdefault(r.get("num_legs", 1), []).append(r)
    print(f"\nBy number of legs (1 = never re-entered):")
    for n, rows in sorted(by_legs.items()):
        profits = [r["profit"] for r in rows if r["profit"] is not None]
        avg_profit = sum(profits) / len(profits) if profits else 0.0
        sub_total = sum(profits)
        win_r = sum(1 for p in profits if p > 0) / len(profits) * 100.0 if profits else 0.0
        print(f"  {n:2d} leg(s): {len(rows):4d} trades  avg_total_profit=${avg_profit:+.2f}  total=${sub_total:+.2f}  win_rate={win_r:.1f}%")


def write_hedge_csv(results: list[dict], symbol: str) -> None:
    if not results:
        return
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{symbol}_hedge_on_cap_{run_stamp}.csv"
    fieldnames = [
        "time", "direction", "confidence", "adx", "m15_bias", "m5_confirm", "m1_entry", "pullback_completed",
        "num_indicators_agree", "indicator_votes", "rsi_value", "macd_hist_value", "atr_value", "atr_scale",
        "outcome", "profit", "ticks_used", "entry_price", "hedge_opened", "leg_outcomes", "leg_profits", "leg_sides",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"Per-trade log: {path}")


def summarize_hedge(results: list[dict], symbol: str) -> None:
    total = len(results)
    if total == 0:
        print(f"No buy/sell signals found for {symbol} in this window.")
        return

    all_profits = [r["profit"] for r in results if r["profit"] is not None]
    total_pnl = sum(all_profits)
    win_rate = sum(1 for p in all_profits if p > 0) / len(all_profits) * 100.0 if all_profits else 0.0
    hedged = [r for r in results if r.get("hedge_opened")]
    never_hedged = [r for r in results if not r.get("hedge_opened")]

    print(f"\n=== Hedge-on-cap backtest (hold original + open reverse leg at -$1, no re-entry chaining): {symbol} ===")
    print(f"Total original signals: {total}")
    print(f"Total realized P&L: ${total_pnl:+.2f}  win_rate={win_rate:.1f}%")
    print(f"Signals that opened a hedge (hit -$1): {len(hedged)} ({len(hedged) / total * 100.0:.1f}%)")
    if never_hedged:
        never_profits = [r["profit"] for r in never_hedged if r["profit"] is not None]
        print(f"  Never hedged (never hit -$1): {len(never_hedged)}  total=${sum(never_profits):+.2f}")
    if hedged:
        hedged_profits = [r["profit"] for r in hedged if r["profit"] is not None]
        hedged_win_rate = sum(1 for p in hedged_profits if p > 0) / len(hedged_profits) * 100.0 if hedged_profits else 0.0
        print(f"  Hedged: {len(hedged)}  total=${sum(hedged_profits):+.2f}  avg=${sum(hedged_profits) / len(hedged_profits):+.2f}  win_rate={hedged_win_rate:.1f}%")

        by_outcome: dict[str, list[dict]] = {}
        for r in hedged:
            by_outcome.setdefault(r["outcome"], []).append(r)
        print(f"\n  By (original_outcome + hedge_outcome) combination:")
        for outcome, rows in sorted(by_outcome.items(), key=lambda kv: -len(kv[1])):
            profits = [r["profit"] for r in rows if r["profit"] is not None]
            avg_profit = sum(profits) / len(profits) if profits else 0.0
            print(f"    {outcome:45s}: {len(rows):3d}  avg=${avg_profit:+.2f}  total=${sum(profits):+.2f}")


def write_entry_excursion_csv(results: list[dict], symbol: str) -> None:
    if not results:
        return
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{symbol}_entry_excursion_{run_stamp}.csv"
    fieldnames = [
        "time", "direction", "confidence", "adx", "m15_bias", "m5_confirm", "m1_entry", "pullback_completed",
        "num_indicators_agree", "indicator_votes", "rsi_value", "macd_hist_value", "atr_value", "atr_scale",
        "entry_price", "entry_peak_profit", "entry_ticks_to_peak",
        "entry_max_drawdown_from_peak", "entry_final_profit", "entry_ticks_observed",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"Per-trade log: {path}")


def summarize_entry_excursion(results: list[dict], symbol: str) -> None:
    """Print aggregate stats for `--measure-entry-excursion`: does the
    trade actually progress in its own favor after entry, with no exit
    rule of any kind applied, over the real continuous tick stream --
    the direct answer to "does price move in the signaled direction, and
    does that hold up" that `--quick-check`'s fixed-horizon bar snapshots
    can only approximate.
    """
    rows = [r for r in results if r.get("entry_peak_profit") is not None]
    total = len(results)
    if total == 0:
        print(f"No buy/sell signals found for {symbol} in this window.")
        return
    if not rows:
        print(f"No ticks available for any of {total} signals for {symbol} in this window.")
        return

    peaks = [r["entry_peak_profit"] for r in rows]
    ticks_to_peak = [r["entry_ticks_to_peak"] for r in rows]
    drawdowns = [r["entry_max_drawdown_from_peak"] for r in rows]
    finals = [r["entry_final_profit"] for r in rows]

    n = len(rows)
    ever_favorable = sum(1 for p in peaks if p > 0)
    still_favorable_at_end = sum(1 for f in finals if f > 0)
    avg_peak = sum(peaks) / n
    avg_ticks_to_peak = sum(ticks_to_peak) / n
    avg_drawdown = sum(drawdowns) / n
    avg_final = sum(finals) / n
    # Of trades that did reach a positive peak, how much of that peak
    # survived to the final observed tick, on average -- 100% would mean
    # every trade held its own high-water mark; 0% would mean every trade
    # gave the whole thing back.
    favorable_rows = [(p, f) for p, f in zip(peaks, finals) if p > 0]
    avg_retained_pct = (
        sum(max(f, 0.0) / p for p, f in favorable_rows) / len(favorable_rows) * 100.0
        if favorable_rows else 0.0
    )

    print(f"\n=== Entry excursion (no exit rule, real ticks from entry): {symbol} ===")
    print(f"Total signals with tick data: {n} / {total}")
    print(f"Ever reached a favorable peak (peak > 0): {ever_favorable} ({ever_favorable / n * 100.0:.1f}%)")
    print(f"Still favorable at final observed tick: {still_favorable_at_end} ({still_favorable_at_end / n * 100.0:.1f}%)")
    print(f"Avg peak profit: ${avg_peak:+.2f}   avg ticks to peak: {avg_ticks_to_peak:.1f}")
    print(f"Avg max drawdown from running peak: ${avg_drawdown:.2f}")
    print(f"Avg final (still-open, mark-to-market) profit: ${avg_final:+.2f}")
    print(f"Of trades that ever went favorable, avg % of peak still held at final tick: {avg_retained_pct:.1f}%")


def _cap_sweep_labels() -> list[str]:
    return [f"cap{str(c).replace('.', '_')}" for c in FULL_LIFECYCLE_CAP_CANDIDATES]


def write_full_lifecycle_cap_sweep_csv(results: list[dict], symbol: str) -> None:
    if not results:
        return
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{symbol}_full_lifecycle_cap_sweep_{run_stamp}.csv"
    fieldnames = [
        "time", "direction", "confidence", "adx", "m15_bias", "m5_confirm", "m1_entry", "pullback_completed",
        "num_indicators_agree", "indicator_votes", "rsi_value", "macd_hist_value", "atr_value", "atr_scale",
    ] + [
        f"{label}_{suffix}" for label in _cap_sweep_labels() for suffix in ("outcome", "profit", "ticks")
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"Per-trade log: {path}")


def summarize_full_lifecycle_cap_sweep(results: list[dict], symbol: str) -> None:
    total = len(results)
    if total == 0:
        print(f"No buy/sell signals found for {symbol} in this window.")
        return

    print(f"\n=== Full-lifecycle post-BE cap sweep (real ExitTrade, cap value varies) for {symbol}, {total} trades ===")
    for c, label in zip(FULL_LIFECYCLE_CAP_CANDIDATES, _cap_sweep_labels()):
        profits = [r[f"{label}_profit"] for r in results if r.get(f"{label}_profit") is not None]
        outcomes = [r[f"{label}_outcome"] for r in results if r.get(f"{label}_outcome") is not None]
        total_pnl = sum(profits)
        win_rate = sum(1 for p in profits if p > 0) / len(profits) * 100.0 if profits else 0.0
        cap_hits = sum(1 for o in outcomes if o == "profit_drop_after_be")
        trail_hits = sum(1 for o in outcomes if o == "trailing_breach_pct_of_peak")
        marker = "  <-- current production value" if c == float(getattr(Config, "EXIT_POST_BE_LOSS_CAP_MONEY", 5.0) or 5.0) else ""
        print(
            f"  -${c:<4.1f}: total=${total_pnl:+9.2f}  win_rate={win_rate:5.1f}%  "
            f"cap_hits={cap_hits:4d}  trail_hits={trail_hits:4d}{marker}"
        )


def write_staircase_cap_sweep_csv(results: list[dict], symbol: str) -> None:
    if not results:
        return
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{symbol}_staircase_cap_sweep_{run_stamp}.csv"
    fieldnames = [
        "time", "direction", "confidence", "adx", "m15_bias", "m5_confirm", "m1_entry", "pullback_completed",
        "num_indicators_agree", "indicator_votes", "rsi_value", "macd_hist_value", "atr_value", "atr_scale",
    ] + [
        f"{label}_{suffix}" for label in _cap_sweep_labels() for suffix in ("outcome", "profit", "ticks")
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"Per-trade log: {path}")


def summarize_staircase_cap_sweep(results: list[dict], symbol: str) -> None:
    total = len(results)
    if total == 0:
        print(f"No buy/sell signals found for {symbol} in this window.")
        return

    print(f"\n=== Staircase-trail post-BE cap sweep (cap value varies, staircase trail fixed) for {symbol}, {total} trades ===")
    for c, label in zip(FULL_LIFECYCLE_CAP_CANDIDATES, _cap_sweep_labels()):
        profits = [r[f"{label}_profit"] for r in results if r.get(f"{label}_profit") is not None]
        outcomes = [r[f"{label}_outcome"] for r in results if r.get(f"{label}_outcome") is not None]
        total_pnl = sum(profits)
        win_rate = sum(1 for p in profits if p > 0) / len(profits) * 100.0 if profits else 0.0
        cap_hits = sum(1 for o in outcomes if o == "profit_drop_after_be")
        trail_hits = sum(1 for o in outcomes if o == "staircase_trail_breach")
        marker = "  <-- current production value" if c == float(getattr(Config, "EXIT_POST_BE_LOSS_CAP_MONEY", 5.0) or 5.0) else ""
        print(
            f"  -${c:<4.1f}: total=${total_pnl:+9.2f}  win_rate={win_rate:5.1f}%  "
            f"cap_hits={cap_hits:4d}  trail_hits={trail_hits:4d}{marker}"
        )


def _staircase_sweep_labels() -> list[str]:
    return [f"tier{str(w).replace('.', '_')}" for w in STAIRCASE_TIER_CANDIDATES]


def write_staircase_sweep_csv(results: list[dict], symbol: str) -> None:
    if not results:
        return
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{symbol}_staircase_tier_sweep_{run_stamp}.csv"
    fieldnames = [
        "time", "direction", "confidence", "adx", "m15_bias", "m5_confirm", "m1_entry", "pullback_completed",
        "num_indicators_agree", "indicator_votes", "rsi_value", "macd_hist_value", "atr_value", "atr_scale",
    ] + [
        f"{label}_{suffix}" for label in _staircase_sweep_labels() for suffix in ("outcome", "profit", "ticks")
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"Per-trade log: {path}")


def summarize_staircase_sweep(results: list[dict], symbol: str) -> None:
    total = len(results)
    if total == 0:
        print(f"No buy/sell signals found for {symbol} in this window.")
        return

    print(f"\n=== Staircase tier-width sweep (real LossExitManager, tier width varies) for {symbol}, {total} trades ===")
    for w, label in zip(STAIRCASE_TIER_CANDIDATES, _staircase_sweep_labels()):
        profits = [r[f"{label}_profit"] for r in results if r.get(f"{label}_profit") is not None]
        outcomes = [r[f"{label}_outcome"] for r in results if r.get(f"{label}_outcome") is not None]
        total_pnl = sum(profits)
        win_rate = sum(1 for p in profits if p > 0) / len(profits) * 100.0 if profits else 0.0
        cap_hits = sum(1 for o in outcomes if o == "profit_drop_after_be")
        trail_hits = sum(1 for o in outcomes if o == "staircase_trail_breach")
        print(
            f"  ${w:<4.2f} tiers: total=${total_pnl:+9.2f}  win_rate={win_rate:5.1f}%  "
            f"cap_hits={cap_hits:4d}  trail_hits={trail_hits:4d}"
        )


def _pre_be_sweep_labels() -> list[str]:
    return [_pre_be_threshold_label(c) for c in PRE_BE_THRESHOLD_CANDIDATES]


def write_pre_be_threshold_sweep_csv(results: list[dict], symbol: str) -> None:
    if not results:
        return
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{symbol}_pre_be_threshold_sweep_{run_stamp}.csv"
    fieldnames = [
        "time", "direction", "confidence", "adx", "m15_bias", "m5_confirm", "m1_entry", "pullback_completed",
        "num_indicators_agree", "indicator_votes", "rsi_value", "macd_hist_value", "atr_value", "atr_scale",
    ] + [
        f"{label}_{suffix}" for label in _pre_be_sweep_labels() for suffix in ("outcome", "profit", "ticks")
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"Per-trade log: {path}")


def summarize_pre_be_threshold_sweep(results: list[dict], symbol: str) -> None:
    total = len(results)
    if total == 0:
        print(f"No buy/sell signals found for {symbol} in this window.")
        return

    print(f"\n=== Pre-BE soft-SL threshold sweep (real LossExitManager, threshold varies) for {symbol}, {total} trades ===")
    for c, label in zip(PRE_BE_THRESHOLD_CANDIDATES, _pre_be_sweep_labels()):
        profits = [r[f"{label}_profit"] for r in results if r.get(f"{label}_profit") is not None]
        outcomes = [r[f"{label}_outcome"] for r in results if r.get(f"{label}_outcome") is not None]
        total_pnl = sum(profits)
        win_rate = sum(1 for p in profits if p > 0) / len(profits) * 100.0 if profits else 0.0
        reached_be = sum(1 for o in outcomes if o == "reached_be")
        marker = "  <-- current production value" if c == float(getattr(Config, "EXIT_MAX_LOSS_MONEY", 5.0) or 5.0) else ""
        print(
            f"  -${c:<4.1f}: total=${total_pnl:+9.2f}  win_rate={win_rate:5.1f}%  "
            f"reached_be={reached_be:4d}/{total}{marker}"
        )


def main() -> None:
    args = parse_args()
    symbol = args.symbol or getattr(Config, "SYMBOLS", ["EURUSD"])[0]
    if args.single_timeframe and args.mtf_entry_indicator is not None:
        print("--mtf-entry-indicator only affects the MTF entry layer -- it's a no-op with --single-timeframe. Aborting.")
        sys.exit(1)
    if args.indicators and not args.single_timeframe:
        print("--indicators is a single-timeframe ablation flag -- requires --single-timeframe. Aborting.")
        sys.exit(1)
    config = Config
    if args.mtf_entry_indicator is not None:
        config = type("ConfigOverride", (Config,), {"MTF_ENTRY_INDICATOR": args.mtf_entry_indicator})
    run(
        symbol,
        args.weeks,
        args.start_pos,
        args.lot,
        args.max_ticks_per_trade,
        args.counterfactual,
        args.counterfactual_max_ticks,
        args.disable_timeout,
        args.measure_post_be,
        args.post_be_max_ticks,
        args.simulate_trail,
        args.full_lifecycle,
        args.full_lifecycle_max_ticks,
        args.sweep_post_be_cap,
        args.sweep_pre_be_threshold,
        config=config,
        invert_signal=args.invert_signal,
        do_measure_entry_excursion=args.measure_entry_excursion,
        entry_excursion_max_ticks=args.entry_excursion_max_ticks,
        trail_gap_pct=args.trail_gap_pct,
        trail_gap_floor_money=args.trail_gap_floor_money,
        post_be_loss_cap=args.post_be_loss_cap,
        pre_be_loss_threshold=args.pre_be_loss_threshold,
        be_arming_ticks=args.be_arming_ticks,
        max_entry_spread_pips=args.max_entry_spread_pips,
        n_tick_confirmation=args.n_tick_confirmation,
        atr_normalize=args.atr_normalize,
        atr_baseline_pips=args.atr_baseline_pips,
        unified_post_be_stop=args.unified_post_be_stop,
        chain_on_cap=args.chain_on_cap,
        chain_max_legs=args.chain_max_legs,
        chain_flip_direction=args.chain_flip_direction,
        chain_loss_cap=args.chain_loss_cap,
        hedge_on_cap=args.hedge_on_cap,
        original_loss_cap=args.original_loss_cap,
        single_timeframe=args.single_timeframe,
        indicator_names=(
            [n.strip() for n in args.indicators.split(",") if n.strip()]
            if args.indicators
            else None
        ),
        staircase_trail=args.staircase_trail,
        staircase_tier_width=args.staircase_tier_width,
        staircase_first_tier=args.staircase_first_tier,
        sweep_staircase_tiers=args.sweep_staircase_tiers,
        sweep_staircase_cap=args.sweep_staircase_cap,
        start_date=_parse_dt(args.start_date),
        end_date=_parse_dt(args.end_date),
    )


if __name__ == "__main__":
    main()
