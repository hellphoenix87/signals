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

Usage:
    pipenv run python scripts/backtest_exit_strategy.py [--symbol EURUSD]
        [--weeks 4] [--start-pos 1] [--lot 0.2] [--max-ticks-per-trade 500]
        [--counterfactual] [--counterfactual-max-ticks 3000]

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
import sys
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from app.config.settings import Config
from app.data.market_data import MarketData
from app.signals.signal_generation import strategy_factory
from app.trade_execution.broker import Broker
from app.trade_execution.mode import TradingMode
from app.risk.risk_manager import create_risk_manager
from app.exit_strategies.exit_trade import create_exit_trade
from app.exit_strategies.exit_shared import PosState

from scripts.backtest_signals import fetch_history, TF_SECONDS

RESULTS_DIR = Path(__file__).resolve().parent.parent / "backtest_results"
M1_BARS_PER_TRADING_WEEK = 5 * 24 * 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=None, help="Symbol to backtest (default: Config.SYMBOLS[0])")
    parser.add_argument("--weeks", type=float, default=4.0, help="Weeks of M1 history to fetch (approximate)")
    parser.add_argument("--start-pos", type=int, default=1, help="MT5 bars back from now to start the M1 window")
    parser.add_argument("--lot", type=float, default=0.2, help="Fixed simulated lot size (default: 0.2, matching the current $1000 sizing basis)")
    parser.add_argument("--max-ticks-per-trade", type=int, default=500, help="Max real ticks fetched per simulated trade (be_arming_ticks=90 by default, so this is a generous ceiling)")
    parser.add_argument("--counterfactual", action="store_true", help="For every soft_sl/timed_out trade, continue the same real tick stream as if only the broker-side wide SL existed, to see whether the early cut was actually a good call")
    parser.add_argument("--counterfactual-max-ticks", type=int, default=3000, help="Extended tick budget for the counterfactual continuation")
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
) -> dict:
    """Open a simulated position at the first real tick at/after
    `entry_time` and replay real historical ticks through the real
    `LossExitManager.check_exit_on_tick`, returning
    `{"outcome", "profit", "ticks_used", "entry_price", ...counterfactual fields}`.

    Fetches `fetch_ticks` ticks up front (>= `max_ticks`) so that, when
    `run_counterfactual` and the real outcome is `soft_sl`/`timed_out`, the
    counterfactual continuation (`simulate_counterfactual`) can replay the
    exact same remaining price path rather than a separately-fetched one.
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
        if side == "buy":
            profit = (price - entry_price) * lot * contract_size
        else:
            profit = (entry_price - price) * lot * contract_size

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
        )
        result.update(cf)

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
        if side == "buy":
            cf_profit = (price - entry_price) * lot * contract_size
            adverse_distance = entry_price - price
        else:
            cf_profit = (entry_price - price) * lot * contract_size
            adverse_distance = price - entry_price

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


def run(
    symbol: str,
    weeks: float,
    start_pos: int,
    lot: float,
    max_ticks: int,
    run_counterfactual: bool,
    counterfactual_max_ticks: int,
) -> None:
    if not mt5.initialize():
        print("MT5 initialization failed.")
        sys.exit(1)

    market_data = MarketData()
    broker = Broker(TradingMode.LIVE)
    risk_manager = create_risk_manager(broker)
    contract_size = broker.get_lot_value(symbol)
    sl_pips = float(getattr(Config, "DEFAULT_SL_PIPS", 5.0) or 5.0)
    sl_price_distance = broker.get_pip_size(symbol) * sl_pips
    fetch_ticks = max(max_ticks, counterfactual_max_ticks) if run_counterfactual else max_ticks

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
    exit_trade = create_exit_trade(broker=broker, risk_manager=risk_manager)

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
                )
                results.append(
                    {
                        "time": m1_candle.get("time"),
                        "direction": final_signal,
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
        "time", "direction", "outcome", "profit", "ticks_used", "entry_price",
        "cf_outcome", "cf_profit", "cf_extra_ticks",
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
    if not cut_short:
        return

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
    )


if __name__ == "__main__":
    main()
