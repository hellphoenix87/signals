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

`--quick-check` replaces the fixed target/stop win-loss simulation with a
much shorter, assumption-free read on entry timing: for each signal, look
only at the next `--horizon-bars` M1 candles (default 1 = one minute) and
report the best price seen in the signal's favor, the worst price seen
against it, and where price ended up at the close of the window -- a
proxy for "could a fast, tick-driven exit have captured a win in this
short a hold", independent of any target/stop distance. Useful now,
before the real exit strategy (tick-driven, can close a position within
seconds) is finalized -- the longer target/stop simulation below still
matters once that's settled, but assumes a hold time this app doesn't
actually commit to.

`--indicators macd,sma,rsi,macd_crossover` (comma-separated, any subset)
overrides the single-timeframe default vote with only the named
indicator(s) -- for single-indicator ablation, e.g. `--indicators macd`
to test MACD alone. `macd_crossover` is an experimental, backtest-only
MACD variant (true crossover trigger instead of the live "still
accelerating" one) for direct comparison. Not compatible with `--mtf`.

`--mtf-score-threshold`/`--mtf-adx-min-strength` override
`Config.MTF_SCORE_THRESHOLD`/`MTF_ADX_MIN_STRENGTH` for `--mtf` runs
(gate-sensitivity sweeps), without touching live settings.

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
from typing import Any

import MetaTrader5 as mt5

from app.config.settings import Config
from app.data.market_data import MarketData
from app.signals.indicators.macd import calculate_macd_crossover
from app.signals.signal_generation import build_indicator, strategy_factory
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
    parser.add_argument(
        "--quick-check",
        action="store_true",
        help="Report best/worst/end-of-window price excursion over --horizon-bars instead of a fixed target/stop win-loss simulation",
    )
    parser.add_argument("--horizon-bars", type=int, default=1, help="Bars to look ahead for --quick-check (default: 1, i.e. one M1 minute)")
    parser.add_argument(
        "--indicators",
        default=None,
        help="Comma-separated single-timeframe indicator ablation (e.g. 'macd', 'sma', 'macd_crossover'); overrides the default macd+sma+rsi vote. Not compatible with --mtf.",
    )
    parser.add_argument("--mtf-score-threshold", type=float, default=None, help="Override Config.MTF_SCORE_THRESHOLD for --mtf runs")
    parser.add_argument("--mtf-adx-min-strength", type=float, default=None, help="Override Config.MTF_ADX_MIN_STRENGTH for --mtf runs")
    return parser.parse_args()


def build_indicator_for_backtest(name: str, config: Any):
    """Like `signal_generation.build_indicator`, plus experimental,
    backtest-only variants (e.g. `macd_crossover`) not used by production
    `strategy_factory`."""
    if name == "macd_crossover":
        return calculate_macd_crossover
    return build_indicator(name, config)


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


def evaluate_immediate_move(
    candles: list[dict],
    entry_index: int,
    direction: str,
    pip_size: float,
    horizon_bars: int,
) -> dict:
    """Look at the next `horizon_bars` candles after entry and report the
    best price seen in the signal's favor, the worst seen against it, and
    the net move at the close of the window -- all in pips, all
    independent of any target/stop assumption.
    """
    entry_price = float(candles[entry_index]["close"])
    end = min(entry_index + 1 + horizon_bars, len(candles))
    window = candles[entry_index + 1 : end]
    if not window:
        return {"favorable_pips": 0.0, "adverse_pips": 0.0, "end_pips": 0.0, "bars": 0}

    highs = [float(c["high"]) for c in window]
    lows = [float(c["low"]) for c in window]
    end_close = float(window[-1]["close"])

    if direction == "buy":
        favorable_pips = (max(highs) - entry_price) / pip_size
        adverse_pips = (entry_price - min(lows)) / pip_size
        end_pips = (end_close - entry_price) / pip_size
    else:
        favorable_pips = (entry_price - min(lows)) / pip_size
        adverse_pips = (max(highs) - entry_price) / pip_size
        end_pips = (entry_price - end_close) / pip_size

    return {
        "favorable_pips": favorable_pips,
        "adverse_pips": adverse_pips,
        "end_pips": end_pips,
        "bars": len(window),
    }


def fetch_history(market_data: MarketData, symbol: str, timeframe: int, count: int) -> list[dict]:
    """Fetch `count` closed candles for `timeframe` and tag each with `symbol`."""
    candles = market_data.get_historical_candles(
        symbol, timeframe=timeframe, start_pos=1, count=count
    )
    for c in candles:
        c["symbol"] = symbol
    return candles


def run_backtest(
    symbol: str,
    count: int,
    forward_bars: int,
    target_pips: float,
    stop_pips: float,
    quick_check: bool = False,
    horizon_bars: int = 1,
    indicator_names: list[str] | None = None,
) -> None:
    """Fetch history, replay it through the real signal generator, and print a summary.

    `indicator_names`, when given, overrides the default macd+sma+rsi vote
    with only the named indicator(s) -- for single-indicator ablation runs
    (see `build_indicator_for_backtest`).
    """
    if not mt5.initialize():
        print("MT5 initialization failed.")
        sys.exit(1)

    market_data = MarketData()
    candles = fetch_history(market_data, symbol, Config.TIMEFRAME, count)
    if not candles:
        print(f"No historical candles returned for {symbol}.")
        return

    indicators = (
        {name: build_indicator_for_backtest(name, Config) for name in indicator_names}
        if indicator_names
        else None
    )
    strategy = strategy_factory(config=Config, indicators=indicators)
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
                if quick_check:
                    move = evaluate_immediate_move(candles, i, final_signal, pip_size, horizon_bars)
                    results.append({"time": candles[i].get("time"), "direction": final_signal, **move})
                else:
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

    label = f"{symbol}_{'+'.join(indicator_names)}" if indicator_names else symbol
    display_name = f"{symbol} ({'+'.join(indicator_names)})" if indicator_names else symbol
    if quick_check:
        log_path = write_quick_results_csv(results, label)
        summarize_quick(results, display_name, horizon_bars, log_path)
    else:
        log_path = write_results_csv(results, label)
        summarize(results, display_name, log_path)


TF_SECONDS = {
    mt5.TIMEFRAME_M1: 60,
    mt5.TIMEFRAME_M5: 5 * 60,
    mt5.TIMEFRAME_M15: 15 * 60,
}


def run_mtf_backtest(
    symbol: str,
    m1_count: int,
    forward_bars: int,
    target_pips: float,
    stop_pips: float,
    quick_check: bool = False,
    horizon_bars: int = 1,
    config: Any = Config,
    label: str | None = None,
) -> None:
    """Replay the multi-timeframe strategy (SMA/M15 bias, RSI/M5 confirm,
    MACD/M1 entry) and print a summary.

    Fetches M1/M5/M15 history up front, then for each M1 candle passes each
    higher-timeframe layer only the candles already closed by that M1
    candle's own close time -- found via `bisect` over each timeframe's
    precomputed close-time array, so no layer ever sees a bar before it
    actually finished forming.

    `config` defaults to the real `Config` but can be a subclass override
    (e.g. a different `MTF_SCORE_THRESHOLD`/`MTF_ADX_MIN_STRENGTH`) for
    gate-sensitivity sweeps, without touching live settings.
    """
    if not mt5.initialize():
        print("MT5 initialization failed.")
        sys.exit(1)

    market_data = MarketData()
    tf_entry = getattr(config, "TF_ENTRY", mt5.TIMEFRAME_M1)
    tf_confirm = getattr(config, "TF_CONFIRM", mt5.TIMEFRAME_M5)
    tf_bias = getattr(config, "TF_BIAS", mt5.TIMEFRAME_M15)

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

    strategy = strategy_factory(config=config, use_multi=True)
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
                if quick_check:
                    move = evaluate_immediate_move(m1_candles, i, final_signal, pip_size, horizon_bars)
                    results.append({"time": m1_candle.get("time"), "direction": final_signal, **move})
                else:
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

    file_tag = f"{symbol}_mtf" + (f"_{label}" if label else "")
    display_name = f"{symbol} (multi-timeframe" + (f", {label})" if label else ")")
    if quick_check:
        log_path = write_quick_results_csv(results, file_tag)
        summarize_quick(results, display_name, horizon_bars, log_path)
    else:
        log_path = write_results_csv(results, file_tag)
        summarize(results, display_name, log_path)


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


def write_quick_results_csv(results: list[dict], symbol: str) -> Path | None:
    """Write one row per signal to a timestamped CSV for `--quick-check` runs."""
    if not results:
        return None

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{symbol}_quick_{run_stamp}.csv"

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["time", "direction", "favorable_pips", "adverse_pips", "end_pips", "bars"]
        )
        writer.writeheader()
        writer.writerows(results)

    return path


def summarize_quick(results: list[dict], symbol: str, horizon_bars: int, log_path: Path | None) -> None:
    """Print aggregate stats for a `--quick-check` run: what fraction of
    signals ever saw a favorable price move within the window, vs. never
    going positive at all (a loss no matter when you'd exited)."""
    total = len(results)
    if total == 0:
        print(f"No buy/sell signals found for {symbol} in this window.")
        return

    buys = [r for r in results if r["direction"] == "buy"]
    sells = [r for r in results if r["direction"] == "sell"]
    had_win_opportunity = [r for r in results if r["favorable_pips"] > 0]
    ended_positive = [r for r in results if r["end_pips"] > 0]
    never_favorable = [r for r in results if r["favorable_pips"] <= 0]

    avg_favorable = sum(r["favorable_pips"] for r in results) / total
    avg_adverse = sum(r["adverse_pips"] for r in results) / total
    avg_end = sum(r["end_pips"] for r in results) / total

    print(f"\n=== Quick-check summary: {symbol} (horizon={horizon_bars} bar(s)) ===")
    print(f"Total signals: {total} (buy={len(buys)}, sell={len(sells)})")
    print(
        f"Had a winning exit at some point in the window: {len(had_win_opportunity)} "
        f"({len(had_win_opportunity) / total * 100.0:.1f}%)"
    )
    print(
        f"Never went positive (a loss no matter when exited): {len(never_favorable)} "
        f"({len(never_favorable) / total * 100.0:.1f}%)"
    )
    print(
        f"Still positive at end of window: {len(ended_positive)} "
        f"({len(ended_positive) / total * 100.0:.1f}%)"
    )
    print(
        f"Avg favorable/adverse/end move: {avg_favorable:+.2f} / {avg_adverse:+.2f} / {avg_end:+.2f} pips"
    )
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

    if args.indicators and args.mtf:
        print("--indicators is a single-timeframe ablation flag, not compatible with --mtf. Aborting.")
        sys.exit(1)

    if args.mtf:
        config = Config
        label_parts = []
        if args.mtf_score_threshold is not None or args.mtf_adx_min_strength is not None:
            overrides = {}
            if args.mtf_score_threshold is not None:
                overrides["MTF_SCORE_THRESHOLD"] = args.mtf_score_threshold
                label_parts.append(f"score{args.mtf_score_threshold}")
            if args.mtf_adx_min_strength is not None:
                overrides["MTF_ADX_MIN_STRENGTH"] = args.mtf_adx_min_strength
                label_parts.append(f"adx{args.mtf_adx_min_strength}")
            config = type("ConfigOverride", (Config,), overrides)
        run_mtf_backtest(
            symbol, count, args.forward_bars, target_pips, stop_pips,
            quick_check=args.quick_check, horizon_bars=args.horizon_bars,
            config=config, label="_".join(label_parts) or None,
        )
    else:
        indicator_names = (
            [n.strip() for n in args.indicators.split(",") if n.strip()]
            if args.indicators
            else None
        )
        run_backtest(
            symbol, count, args.forward_bars, target_pips, stop_pips,
            quick_check=args.quick_check, horizon_bars=args.horizon_bars,
            indicator_names=indicator_names,
        )


if __name__ == "__main__":
    main()
