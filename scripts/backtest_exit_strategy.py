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

Usage:
    pipenv run python scripts/backtest_exit_strategy.py [--symbol EURUSD]
        [--weeks 4] [--start-pos 1] [--lot 0.2] [--max-ticks-per-trade 500]

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
) -> dict:
    """Open a simulated position at the first real tick at/after
    `entry_time` and replay real historical ticks through the real
    `LossExitManager.check_exit_on_tick`, returning
    `{"outcome", "profit", "ticks_used", "entry_price"}`.
    """
    ticks = mt5.copy_ticks_from(symbol, entry_time, max_ticks, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return {"outcome": "no_ticks", "profit": None, "ticks_used": 0, "entry_price": None}

    entry_tick = ticks[0]
    entry_price = float(entry_tick["ask"]) if side == "buy" else float(entry_tick["bid"])
    pos_type = 0 if side == "buy" else 1

    state = PosState(anchor=0.0, prev_price=0.0)
    loss_manager = exit_trade._loss_manager

    profit = 0.0
    for idx, t in enumerate(ticks):
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
            return {
                "outcome": outcome,
                "profit": profit,
                "ticks_used": idx + 1,
                "entry_price": entry_price,
            }
        if getattr(state, "be_armed", False):
            return {
                "outcome": "reached_be",
                "profit": profit,
                "ticks_used": idx + 1,
                "entry_price": entry_price,
            }

    return {"outcome": "exhausted", "profit": profit, "ticks_used": len(ticks), "entry_price": entry_price}


def run(symbol: str, weeks: float, start_pos: int, lot: float, max_ticks: int) -> None:
    if not mt5.initialize():
        print("MT5 initialization failed.")
        sys.exit(1)

    market_data = MarketData()
    broker = Broker(TradingMode.LIVE)
    risk_manager = create_risk_manager(broker)
    contract_size = broker.get_lot_value(symbol)

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
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["time", "direction", "outcome", "profit", "ticks_used", "entry_price"]
        )
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


def main() -> None:
    args = parse_args()
    symbol = args.symbol or getattr(Config, "SYMBOLS", ["EURUSD"])[0]
    run(symbol, args.weeks, args.start_pos, args.lot, args.max_ticks_per_trade)


if __name__ == "__main__":
    main()
