"""Backtest signal-generation quality against real historical MT5 data.

Replays historical candles through the real, currently-configured
single-timeframe signal generator (`strategy_factory(config=Config)`),
and reports whether each non-hold signal would have moved
`Config.DEFAULT_TP_PIPS` in its favor before `Config.DEFAULT_SL_PIPS`
against it within a lookahead window -- a simple, exit-strategy-agnostic
proxy for signal quality, not a full trade simulation.

Usage:
    pipenv run python scripts/backtest_signals.py [--symbol EURUSD]
        [--weeks 4 | --count 5000] [--forward-bars 300] [--target-pips 50] [--stop-pips 2]

Per-signal results are written to a CSV under `backtest_results/` (one row
per signal: time, direction, outcome, bars-to-resolution) for offline
analysis in pandas/Excel; the aggregate summary is still printed to
stdout. The signal generator's own per-candle logging is suppressed
during the replay so it doesn't drown out the summary.

`--mtf` replays the multi-timeframe strategy (SMA/M15 bias, RSI/M5
confirm, MACD/M1 entry) instead of the single-timeframe path, fetching
synchronized M1/M5/M15 history and feeding each layer only the higher-
timeframe candles already closed as of each M1 decision point. This is
independent of `Config.USE_MULTI_TIMEFRAME_SIGNALS`, which stays off for
live trading regardless of this flag.

Not supported here: n-tick confirmation (needs live tick data to ever
confirm a signal, which a bar-only replay can't provide). Full
exit-strategy-aware backtesting (real trade lifecycle simulation via the
actual exit managers, not this fixed target/stop proxy) is deliberately
deferred until the exit strategy itself is finalized.
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

import MetaTrader5 as mt5

from app.config.settings import Config
from app.data.market_data import MarketData
from app.signals.signal_generation import strategy_factory
from app.trade_execution.broker import Broker
from app.trade_execution.mode import TradingMode

M1_BARS_PER_TRADING_WEEK = 5 * 24 * 60
RESULTS_DIR = Path(__file__).resolve().parent.parent / "backtest_results"


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments, defaulting symbol/target/stop pips from `Config`."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=None, help="Symbol to backtest (default: Config.SYMBOLS[0])")
    parser.add_argument("--weeks", type=float, default=None, help="Weeks of M1 history to fetch (approximate; overrides --count)")
    parser.add_argument("--count", type=int, default=5000, help="Number of historical M1 candles to fetch (ignored if --weeks is given)")
    parser.add_argument("--forward-bars", type=int, default=300, help="Lookahead window (bars) to resolve each signal")
    parser.add_argument("--target-pips", type=float, default=None, help="Favorable-move threshold (default: Config.DEFAULT_TP_PIPS)")
    parser.add_argument("--stop-pips", type=float, default=None, help="Adverse-move threshold (default: Config.DEFAULT_SL_PIPS)")
    parser.add_argument(
        "--mtf",
        action="store_true",
        help="Replay the multi-timeframe strategy (SMA/M15 bias, RSI/M5 confirm, MACD/M1 entry) instead of the single-timeframe path",
    )
    return parser.parse_args()


def evaluate_signal(
    candles: list[dict],
    entry_index: int,
    direction: str,
    pip_size: float,
    target_pips: float,
    stop_pips: float,
    forward_bars: int,
) -> tuple[str, int]:
    """Look forward from `entry_index` and return `("win"|"loss"|"undecided", bars_to_resolution)`.

    A bar that would satisfy both the target and the stop (its high/low
    range spans both) counts as a loss -- a conservative assumption, since
    the actual intrabar order of price movement isn't known from OHLC alone.
    """
    entry_price = float(candles[entry_index]["close"])
    target_distance = target_pips * pip_size
    stop_distance = stop_pips * pip_size
    end = min(entry_index + 1 + forward_bars, len(candles))

    for i in range(entry_index + 1, end):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        if direction == "buy":
            hit_stop = low <= entry_price - stop_distance
            hit_target = high >= entry_price + target_distance
        else:
            hit_stop = high >= entry_price + stop_distance
            hit_target = low <= entry_price - target_distance

        if hit_stop:
            return "loss", i - entry_index
        if hit_target:
            return "win", i - entry_index

    return "undecided", end - 1 - entry_index


def fetch_history(market_data: MarketData, symbol: str, timeframe: int, count: int) -> list[dict]:
    """Fetch `count` closed candles for `timeframe` and tag each with `symbol`."""
    candles = market_data.get_historical_candles(
        symbol, timeframe=timeframe, start_pos=1, count=count
    )
    for c in candles:
        c["symbol"] = symbol
    return candles


def run_backtest(
    symbol: str, count: int, forward_bars: int, target_pips: float, stop_pips: float
) -> None:
    """Fetch history, replay it through the real signal generator, and print a summary."""
    if not mt5.initialize():
        print("MT5 initialization failed.")
        sys.exit(1)

    market_data = MarketData()
    candles = fetch_history(market_data, symbol, Config.TIMEFRAME, count)
    if not candles:
        print(f"No historical candles returned for {symbol}.")
        return

    strategy = strategy_factory(config=Config)
    broker = Broker(TradingMode.BACKTEST)
    pip_size = broker.get_pip_size(symbol)

    min_candles = int(getattr(Config, "MIN_CANDLES_FOR_INDICATORS", 1) or 1)
    results: list[dict] = []

    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            for i in range(min_candles, len(candles)):
                window = candles[: i + 1]
                signal = strategy.generate_signal(window)
                final_signal = (signal.get("final_signal") or "hold").lower()
                if final_signal not in ("buy", "sell"):
                    continue
                outcome, bars = evaluate_signal(
                    candles, i, final_signal, pip_size, target_pips, stop_pips, forward_bars
                )
                results.append(
                    {
                        "time": candles[i].get("time"),
                        "direction": final_signal,
                        "outcome": outcome,
                        "bars": bars,
                    }
                )
    finally:
        logging.disable(logging.NOTSET)

    log_path = write_results_csv(results, symbol)
    summarize(results, symbol, log_path)


TF_SECONDS = {
    mt5.TIMEFRAME_M1: 60,
    mt5.TIMEFRAME_M5: 5 * 60,
    mt5.TIMEFRAME_M15: 15 * 60,
}


def run_mtf_backtest(
    symbol: str, m1_count: int, forward_bars: int, target_pips: float, stop_pips: float
) -> None:
    """Replay the multi-timeframe strategy (SMA/M15 bias, RSI/M5 confirm,
    MACD/M1 entry) and print a summary.

    Fetches M1/M5/M15 history up front, then for each M1 candle passes each
    higher-timeframe layer only the candles already closed by that M1
    candle's own close time -- found via `bisect` over each timeframe's
    precomputed close-time array, so no layer ever sees a bar before it
    actually finished forming.
    """
    if not mt5.initialize():
        print("MT5 initialization failed.")
        sys.exit(1)

    market_data = MarketData()
    tf_entry = getattr(Config, "TF_ENTRY", mt5.TIMEFRAME_M1)
    tf_confirm = getattr(Config, "TF_CONFIRM", mt5.TIMEFRAME_M5)
    tf_bias = getattr(Config, "TF_BIAS", mt5.TIMEFRAME_M15)

    m1_candles = fetch_history(market_data, symbol, tf_entry, m1_count)
    if not m1_candles:
        print(f"No historical M1 candles returned for {symbol}.")
        return

    m5_count = max(int(m1_count / 5), 100)
    m15_count = max(int(m1_count / 15), 100)
    m5_candles = fetch_history(market_data, symbol, tf_confirm, m5_count)
    m15_candles = fetch_history(market_data, symbol, tf_bias, m15_count)
    if not m5_candles or not m15_candles:
        print(f"No historical M5/M15 candles returned for {symbol}.")
        return

    m5_close_times = [c["time"] + datetime.timedelta(seconds=TF_SECONDS[tf_confirm]) for c in m5_candles]
    m15_close_times = [c["time"] + datetime.timedelta(seconds=TF_SECONDS[tf_bias]) for c in m15_candles]
    entry_seconds = TF_SECONDS[tf_entry]

    strategy = strategy_factory(config=Config, use_multi=True)
    broker = Broker(TradingMode.BACKTEST)
    pip_size = broker.get_pip_size(symbol)

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
                outcome, bars = evaluate_signal(
                    m1_candles, i, final_signal, pip_size, target_pips, stop_pips, forward_bars
                )
                results.append(
                    {
                        "time": m1_candle.get("time"),
                        "direction": final_signal,
                        "outcome": outcome,
                        "bars": bars,
                    }
                )
    finally:
        logging.disable(logging.NOTSET)

    log_path = write_results_csv(results, f"{symbol}_mtf")
    summarize(results, f"{symbol} (multi-timeframe)", log_path)


def write_results_csv(results: list[dict], symbol: str) -> Path | None:
    """Write one row per signal to a timestamped CSV under `backtest_results/`.

    Returns the path written, or `None` if there were no results to write.
    """
    if not results:
        return None

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{symbol}_{run_stamp}.csv"

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "direction", "outcome", "bars"])
        writer.writeheader()
        writer.writerows(results)

    return path


def summarize(results: list[dict], symbol: str, log_path: Path | None) -> None:
    """Print aggregate signal/outcome stats to stdout."""
    total = len(results)
    if total == 0:
        print(f"No buy/sell signals found for {symbol} in this window.")
        return

    buys = [r for r in results if r["direction"] == "buy"]
    sells = [r for r in results if r["direction"] == "sell"]
    wins = [r for r in results if r["outcome"] == "win"]
    losses = [r for r in results if r["outcome"] == "loss"]
    undecided = [r for r in results if r["outcome"] == "undecided"]
    decided = wins + losses
    win_rate = (len(wins) / len(decided) * 100.0) if decided else 0.0
    avg_bars = (sum(r["bars"] for r in decided) / len(decided)) if decided else 0.0

    print(f"\n=== Backtest summary: {symbol} ===")
    print(f"Total signals: {total} (buy={len(buys)}, sell={len(sells)})")
    print(f"Wins: {len(wins)}  Losses: {len(losses)}  Undecided: {len(undecided)}")
    print(f"Win rate (of decided): {win_rate:.1f}%")
    print(f"Average bars to resolution: {avg_bars:.1f}")
    if log_path:
        print(f"Per-signal log: {log_path}")


def main() -> None:
    args = parse_args()
    symbol = args.symbol or getattr(Config, "SYMBOLS", ["EURUSD"])[0]
    count = (
        int(args.weeks * M1_BARS_PER_TRADING_WEEK) if args.weeks is not None else args.count
    )
    target_pips = (
        args.target_pips
        if args.target_pips is not None
        else float(getattr(Config, "DEFAULT_TP_PIPS", 50.0))
    )
    stop_pips = (
        args.stop_pips
        if args.stop_pips is not None
        else float(getattr(Config, "DEFAULT_SL_PIPS", 2.0))
    )

    if not args.mtf and getattr(Config, "USE_MULTI_TIMEFRAME_SIGNALS", False):
        print(
            "USE_MULTI_TIMEFRAME_SIGNALS is on but --mtf wasn't passed -- this "
            "would silently replay the single-timeframe path instead of what's "
            "actually configured. Pass --mtf, or turn the flag off. Aborting."
        )
        sys.exit(1)

    n_tick_active = getattr(Config, "USE_N_TICK_CONFIRMATION", False) and int(
        getattr(Config, "N_TICK_CONFIRMATION", 0) or 0
    ) > 1
    if n_tick_active:
        print(
            "N-tick confirmation is on -- it needs live tick data to confirm "
            "signals, which this bar-only replay can't provide. Aborting."
        )
        sys.exit(1)

    if args.mtf:
        run_mtf_backtest(symbol, count, args.forward_bars, target_pips, stop_pips)
    else:
        run_backtest(symbol, count, args.forward_bars, target_pips, stop_pips)


if __name__ == "__main__":
    main()
