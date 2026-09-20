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
from app.signals.signal_generation import strategy_factory
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
FULL_LIFECYCLE_CAP_CANDIDATES: list[float] = [0.0, 1.0, 1.5, 2.0, 2.5, 3.0, 7.0, 15.0]

# Thread 2 (docs/exit-strategy-open-threads.md): candidate values for the
# PRE-breakeven soft-SL money threshold (`Config.EXIT_MAX_LOSS_MONEY`,
# LossExitManager._pre_be_soft_sl_hit), tested for --sweep-pre-be-threshold
# to see whether a threshold conditional on M5-confirm state (whether
# RSI/M5 actually agrees with the trade direction) beats today's flat $5
# rule. 5.0 is today's live value, kept for direct comparison.
PRE_BE_THRESHOLD_CANDIDATES: list[float] = [2.0, 3.0, 4.0, 5.0, 7.0, 10.0]


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
    parser.add_argument("--start-pos", type=int, default=1, help="MT5 bars back from now to start the M1 window")
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

    entry_tick = ticks[0]
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

    entry_tick = ticks[0]
    entry_price = float(entry_tick["ask"]) if side == "buy" else float(entry_tick["bid"])
    pos_type = 0 if side == "buy" else 1

    state = PosState(anchor=0.0, prev_price=0.0)
    loss_manager = exit_trade._loss_manager
    profit_manager = exit_trade._profit_manager
    profit_exits_on_tick = bool(getattr(exit_trade._config, "profit_exits_on_tick", True))

    profit = 0.0
    outcome = None
    idx = -1
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

    if outcome is None:
        outcome = "exhausted"

    return {
        "outcome": outcome,
        "profit": profit,
        "ticks_used": idx + 1,
        "entry_price": entry_price,
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

    entry_tick = ticks[0]
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

    entry_tick = ticks[0]
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
) -> None:
    if not mt5.initialize():
        print("MT5 initialization failed.")
        sys.exit(1)

    if disable_timeout:
        max_ticks = max(max_ticks, counterfactual_max_ticks)

    market_data = MarketData()
    broker = Broker(TradingMode.LIVE)
    risk_manager = create_risk_manager(broker)
    contract_size = broker.get_lot_value(symbol)
    sl_pips = float(getattr(Config, "DEFAULT_SL_PIPS", 5.0) or 5.0)
    sl_price_distance = broker.get_pip_size(symbol) * sl_pips
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
    m1_candles = fetch_history(market_data, symbol, tf_entry, m1_count, start_pos)
    if not m1_candles:
        print(f"No historical M1 candles returned for {symbol}.")
        return

    m5_count = max(int(m1_count / 5), 100)
    m15_count = max(int(m1_count / 15), 100)
    m5_start_pos = max(1, int(start_pos / 5))
    m15_start_pos = max(1, int(start_pos / 15))
    m5_candles = fetch_history(market_data, symbol, tf_confirm, m5_count, m5_start_pos)
    m15_candles = fetch_history(market_data, symbol, tf_bias, m15_count, m15_start_pos)
    if not m5_candles or not m15_candles:
        print(f"No historical M5/M15 candles returned for {symbol}.")
        return

    m5_close_times = [c["time"] + datetime.timedelta(seconds=TF_SECONDS[tf_confirm]) for c in m5_candles]
    m15_close_times = [c["time"] + datetime.timedelta(seconds=TF_SECONDS[tf_bias]) for c in m15_candles]

    strategy = strategy_factory(config=Config, use_multi=True)
    exit_config = ExitTradeConfig(be_arming_ticks=0) if disable_timeout else None
    exit_trade = create_exit_trade(broker=broker, risk_manager=risk_manager, config=exit_config)

    cap_sweep_trades: list[tuple[str, Any]] = []
    if full_lifecycle and sweep_post_be_cap:
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
                closed_by = m1_candle["time"] + datetime.timedelta(seconds=entry_seconds)
                m5_ptr = bisect.bisect_right(m5_close_times, closed_by)
                m15_ptr = bisect.bisect_right(m15_close_times, closed_by)
                if m5_ptr == 0 or m15_ptr == 0:
                    continue

                candles_by_tf = {
                    tf_entry: m1_candles[: i + 1],
                    tf_confirm: m5_candles[:m5_ptr],
                    tf_bias: m15_candles[:m15_ptr],
                }
                signal = strategy.generate_signal(candles_by_tf)
                final_signal = (signal.get("final_signal") or "hold").lower()
                if final_signal not in ("buy", "sell"):
                    continue

                if full_lifecycle and sweep_post_be_cap:
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
                elif full_lifecycle:
                    sim = simulate_full_lifecycle(
                        exit_trade=exit_trade,
                        symbol=symbol,
                        side=final_signal,
                        entry_time=closed_by,
                        lot=lot,
                        contract_size=contract_size,
                        max_ticks=full_lifecycle_max_ticks,
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
                        **sim,
                    }
                )
    finally:
        logging.disable(logging.NOTSET)

    if full_lifecycle and sweep_post_be_cap:
        write_full_lifecycle_cap_sweep_csv(results, symbol)
        summarize_full_lifecycle_cap_sweep(results, symbol)
    elif sweep_pre_be_threshold:
        write_pre_be_threshold_sweep_csv(results, symbol)
        summarize_pre_be_threshold_sweep(results, symbol)
    elif full_lifecycle:
        write_full_lifecycle_csv(results, symbol)
        summarize_full_lifecycle(results, symbol)
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
        "outcome", "profit", "ticks_used", "entry_price",
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
    )


if __name__ == "__main__":
    main()
