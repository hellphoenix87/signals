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

Usage:
    pipenv run python scripts/backtest_exit_strategy.py [--symbol EURUSD]
        [--weeks 4] [--start-pos 1] [--lot 0.2] [--max-ticks-per-trade 500]
        [--counterfactual] [--counterfactual-max-ticks 3000]
        [--disable-timeout] [--measure-post-be] [--simulate-trail]
        [--post-be-max-ticks 3000]

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

        action = loss_manager.check_exit_on_tick(position, t, state)
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
    (Thread 4's existing post-BE loss cap -- the hardcoded `-$5`
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

    Returns `{"trail_<name>_profit", "trail_<name>_ticks",
    "trail_<name>_triggered", "trail_<name>_lm_capped"}` for every name
    in `TRAIL_RULES`, plus `"trail_lm_profit"`/`"trail_lm_ticks"`/
    `"trail_lm_triggered"` for the real loss manager's own outcome.
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
            action = loss_manager.check_exit_on_tick(position, t, state)
            if action:
                lm_triggered = True
                lm_profit = profit
                lm_ticks = idx - arm_idx
                lm_reason = action.reason
                lm_trigger_abs_idx = idx
        elif lm_trigger_abs_idx is not None and idx > lm_trigger_abs_idx:
            # Gap-reversal counterfactual: what real price did AFTER the real
            # -$5 cap would have force-closed the trade -- continuing to
            # watch with no exit rule applied, same as --measure-post-be but
            # anchored to the cap point instead of the breakeven-arming point.
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
            print(
                f"  {'(reference) real -$5 post-BE cap alone':32s}: "
                f"triggered={len(lm_only):4d}/{len(trailed)}  total_on_those=${sum(lm_only):+.2f}  "
                f"(-$5 cap: {cap_hits}, marginal-recovery lock-in: {lockin_hits})"
            )

            capped = [r for r in trailed if r.get("trail_lm_reason") == "profit_drop_after_be"]
            if capped:
                recovered = sum(1 for r in capped if r.get("gap_recovered") is True)
                no_exit_final_total = sum(r["gap_final_profit"] for r in capped if r.get("gap_final_profit") is not None)
                real_capped_total = sum(r["trail_lm_profit"] for r in capped)
                worst = [r["gap_min_profit_after_cap"] for r in capped if r.get("gap_min_profit_after_cap") is not None]
                print(
                    f"  {'  --> if the -$5 cap did NOT exist':32s}: "
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
    )


if __name__ == "__main__":
    main()
