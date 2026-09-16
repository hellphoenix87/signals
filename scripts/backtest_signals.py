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

`--indicators macd,sma,rsi` (comma-separated, any subset) overrides the
single-timeframe default vote with only the named indicator(s) -- for
single-indicator ablation, e.g. `--indicators macd` to test MACD alone.
Not compatible with `--mtf`.

`--mtf-score-threshold`/`--mtf-adx-min-strength` override
`Config.MTF_SCORE_THRESHOLD`/`MTF_ADX_MIN_STRENGTH` for `--mtf` runs
(gate-sensitivity sweeps), without touching live settings.

`--spread-pips` models a round-trip spread cost against bid-based OHLC
bars in the target/stop simulation (default 0, unmodeled, matching every
report before this flag existed) -- see `evaluate_signal` for the exact
mechanics. `--start-pos` shifts the M1 window back by that many bars from
now (default 1, the most recent window); use it with `--weeks`/`--count`
to backtest an older, non-overlapping period for out-of-sample checks.

`--session-filter` forces `Config.USE_SESSION_FILTER` on for this run
(already the live default -- see `Config.SESSION_FILTER_BLOCKED_HOURS_UTC`),
holding entries during 08:00-18:59 UTC regardless of the underlying
signal. Useful to force it on/verify explicitly even if a future config
change flips the default back off.

`--ntick N` (N>1) tests raising `Config.N_TICK_CONFIRMATION` to N: for
each candle-close signal, wraps the strategy in the real
`NTickConfirmedSignalStrategy` and, when it marks a new pending
buy/sell, fetches *real* historical ticks (`mt5.copy_ticks_range`) for
the ~one-bar window between that candle's close and the next's --
exactly as long as the wrapper's own hard-reset-on-next-candle gives a
pending signal to confirm live -- and feeds them through `on_new_tick`.
If confirmed, the outcome is evaluated from the actual confirmed tick
price (not the candle close). Unconfirmed pending signals are dropped,
same as live. Not compatible with `--quick-check` (target/stop outcome
only). Uses `Config`'s own default `min_pip_move=0.0` (no CLI override
-- there's no matching `Config` field for it to test against anyway).

Full exit-strategy-aware backtesting (real trade lifecycle simulation
via the actual exit managers, not this fixed target/stop proxy) is
deliberately deferred until the exit strategy itself is finalized.
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
from typing import Any, Optional

import MetaTrader5 as mt5

from app.config.settings import Config
from app.data.market_data import MarketData
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
    parser.add_argument("--spread-pips", type=float, default=0.0, help="Round-trip spread cost to model in the target/stop simulation (default: 0, i.e. unmodeled as before)")
    parser.add_argument("--start-pos", type=int, default=1, help="MT5 bars back from now to start the M1 window (default: 1, i.e. the most recent window); use a larger value to backtest an older, non-overlapping period")
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
        help="Comma-separated single-timeframe indicator ablation (e.g. 'macd', 'sma'); overrides the default macd+sma+rsi vote. Not compatible with --mtf.",
    )
    parser.add_argument("--mtf-score-threshold", type=float, default=None, help="Override Config.MTF_SCORE_THRESHOLD for --mtf runs")
    parser.add_argument("--mtf-adx-min-strength", type=float, default=None, help="Override Config.MTF_ADX_MIN_STRENGTH for --mtf runs")
    parser.add_argument("--rsi-weight", type=float, default=None, help="Override Config.ENTRY_RSI_WEIGHT for the single-timeframe vote (default: Config's own value, 2.0)")
    parser.add_argument("--session-filter", action="store_true", help="Force Config.USE_SESSION_FILTER on for this run, regardless of Config's own value")
    parser.add_argument("--ml-entry", action="store_true", help="Use the trained ML entry model (Config.ML_MODEL_PATH) instead of the hand-coded indicator vote for the base/entry layer. Not compatible with --indicators.")
    parser.add_argument("--ntick", type=int, default=None, help="Test raising Config.N_TICK_CONFIRMATION to this value (>1), confirming each candle-close signal against real historical ticks. Not compatible with --quick-check.")
    return parser.parse_args()


def evaluate_signal(
    candles: list[dict],
    entry_index: int,
    direction: str,
    pip_size: float,
    target_pips: float,
    stop_pips: float,
    forward_bars: int,
    spread_pips: float = 0.0,
) -> tuple[str, int]:
    """Look forward from `entry_index` and return `("win"|"loss"|"undecided", bars_to_resolution)`.

    A bar that would satisfy both the target and the stop (its high/low
    range spans both) counts as a loss -- a conservative assumption, since
    the actual intrabar order of price movement isn't known from OHLC alone.

    `spread_pips`, when nonzero, models a round-trip cost against bid-based
    OHLC bars (the MT5 convention): you buy at ask (bid+spread) and sell at
    bid, so a target needs `spread_pips` more favorable movement to clear,
    while a stop needs that much less adverse movement to hit (you're
    already down the spread the instant you enter). If the stop is
    entirely consumed by the spread, the trade can't survive entry at all
    -- scored as an immediate loss.
    """
    entry_price = float(candles[entry_index]["close"])
    spread_distance = spread_pips * pip_size
    target_distance = target_pips * pip_size + spread_distance
    stop_distance = stop_pips * pip_size - spread_distance
    if stop_distance <= 0:
        return "loss", 0
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


def evaluate_signal_from_price(
    candles: list[dict],
    start_index: int,
    entry_price: float,
    direction: str,
    pip_size: float,
    target_pips: float,
    stop_pips: float,
    forward_bars: int,
    spread_pips: float = 0.0,
) -> tuple[str, int]:
    """Same target/stop win-loss-undecided simulation as `evaluate_signal`,
    but for a signal confirmed at a real (tick-level) `entry_price` rather
    than a candle's own close -- scans forward from `start_index` (the
    first full bar after confirmation) instead of `entry_index + 1`."""
    spread_distance = spread_pips * pip_size
    target_distance = target_pips * pip_size + spread_distance
    stop_distance = stop_pips * pip_size - spread_distance
    if stop_distance <= 0:
        return "loss", 0
    end = min(start_index + forward_bars, len(candles))

    for i in range(start_index, end):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        if direction == "buy":
            hit_stop = low <= entry_price - stop_distance
            hit_target = high >= entry_price + target_distance
        else:
            hit_stop = high >= entry_price + stop_distance
            hit_target = low <= entry_price - target_distance

        if hit_stop:
            return "loss", i - start_index
        if hit_target:
            return "win", i - start_index

    return "undecided", max(end - start_index, 0)


def simulate_ntick_confirmation(
    strategy: Any, symbol: str, candles: list[dict], i: int, entry_seconds: int
) -> Optional[dict]:
    """After `strategy.generate_signal` just marked a new pending buy/sell
    (`strategy._waiting`), fetch real historical ticks for the ~one-bar
    window between candle `i`'s close and the next candle's close, and
    feed them through `on_new_tick` -- mirroring the live orchestrator's
    tick loop and the wrapper's own hard-reset-on-next-candle behavior (a
    pending signal only gets this one candle's worth of real ticks to
    confirm). Returns `{"direction", "entry_price"}` if confirmed within
    that window, else `None` (dropped, same as live).
    """
    close_time = candles[i]["time"] + datetime.timedelta(seconds=entry_seconds)
    if i + 1 < len(candles):
        window_end = candles[i + 1]["time"] + datetime.timedelta(seconds=entry_seconds)
    else:
        window_end = close_time + datetime.timedelta(seconds=entry_seconds)

    ticks = mt5.copy_ticks_range(symbol, close_time, window_end, mt5.COPY_TICKS_ALL)
    if ticks is None:
        return None

    for t in ticks:
        strategy.on_new_tick(float(t["bid"]))
        confirmed = strategy.get_confirmed_signal()
        if confirmed:
            direction = (confirmed.get("final_signal") or "").lower()
            entry_price = confirmed.get("entry_price")
            if direction in ("buy", "sell") and entry_price is not None:
                return {"direction": direction, "entry_price": float(entry_price)}
            return None

    return None


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


def fetch_history(
    market_data: MarketData, symbol: str, timeframe: int, count: int, start_pos: int = 1
) -> list[dict]:
    """Fetch `count` closed candles for `timeframe` ending `start_pos` bars back from now, and tag each with `symbol`."""
    candles = market_data.get_historical_candles(
        symbol, timeframe=timeframe, start_pos=start_pos, count=count
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
    spread_pips: float = 0.0,
    start_pos: int = 1,
    config: Any = Config,
    ntick_n: Optional[int] = None,
) -> None:
    """Fetch history, replay it through the real signal generator, and print a summary.

    `indicator_names`, when given, overrides the default macd+sma+rsi vote
    with only the named indicator(s) -- for single-indicator ablation runs.

    `config` defaults to the real `Config` but can be a subclass override
    (e.g. a different `ENTRY_RSI_WEIGHT`) for weight-sensitivity sweeps,
    without touching live settings.

    `ntick_n`, when given, wraps the strategy in the real
    `NTickConfirmedSignalStrategy` (via `strategy_factory`'s own
    `use_n_tick`/`n_ticks` overrides, so the live wrapper order is
    reproduced exactly) and confirms each pending signal against real
    historical ticks instead of evaluating the candle-close signal
    directly. Not compatible with `quick_check`.
    """
    if not mt5.initialize():
        print("MT5 initialization failed.")
        sys.exit(1)

    market_data = MarketData()
    candles = fetch_history(market_data, symbol, config.TIMEFRAME, count, start_pos)
    if not candles:
        print(f"No historical candles returned for {symbol}.")
        return

    indicators = (
        {name: build_indicator(name, config) for name in indicator_names}
        if indicator_names
        else None
    )
    strategy = strategy_factory(
        config=config,
        indicators=indicators,
        use_multi=False,
        use_n_tick=bool(ntick_n),
        n_ticks=ntick_n or 0,
    )
    broker = Broker(TradingMode.BACKTEST)
    pip_size = broker.get_pip_size(symbol)
    entry_seconds = TF_SECONDS.get(int(getattr(config, "TIMEFRAME", mt5.TIMEFRAME_M1)), 60)

    min_candles = int(getattr(config, "MIN_CANDLES_FOR_INDICATORS", 1) or 1)
    results: list[dict] = []

    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            for i in range(min_candles, len(candles)):
                window = candles[: i + 1]
                signal = strategy.generate_signal(window)

                if ntick_n:
                    if not getattr(strategy, "_waiting", False):
                        continue
                    confirmed = simulate_ntick_confirmation(
                        strategy, symbol, candles, i, entry_seconds
                    )
                    if confirmed is None:
                        continue
                    outcome, bars = evaluate_signal_from_price(
                        candles, i + 1, confirmed["entry_price"], confirmed["direction"],
                        pip_size, target_pips, stop_pips, forward_bars, spread_pips,
                    )
                    results.append(
                        {
                            "time": candles[i].get("time"),
                            "direction": confirmed["direction"],
                            "outcome": outcome,
                            "bars": bars,
                        }
                    )
                    continue

                final_signal = (signal.get("final_signal") or "hold").lower()
                if final_signal not in ("buy", "sell"):
                    continue
                if quick_check:
                    move = evaluate_immediate_move(candles, i, final_signal, pip_size, horizon_bars)
                    results.append({"time": candles[i].get("time"), "direction": final_signal, **move})
                else:
                    outcome, bars = evaluate_signal(
                        candles, i, final_signal, pip_size, target_pips, stop_pips, forward_bars, spread_pips
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

    print(f"Window: start_pos={start_pos}, spread_pips={spread_pips}, rsi_weight={getattr(config, 'ENTRY_RSI_WEIGHT', 2.0)}")
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
    spread_pips: float = 0.0,
    start_pos: int = 1,
    ntick_n: Optional[int] = None,
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

    `ntick_n`, when given, wraps the MTF strategy in the real
    `NTickConfirmedSignalStrategy` (reproducing the live wrapper order
    exactly) and confirms each pending signal against real historical
    ticks. Not compatible with `quick_check`.
    """
    if not mt5.initialize():
        print("MT5 initialization failed.")
        sys.exit(1)

    market_data = MarketData()
    tf_entry = getattr(config, "TF_ENTRY", mt5.TIMEFRAME_M1)
    tf_confirm = getattr(config, "TF_CONFIRM", mt5.TIMEFRAME_M5)
    tf_bias = getattr(config, "TF_BIAS", mt5.TIMEFRAME_M15)

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
    entry_seconds = TF_SECONDS[tf_entry]

    strategy = strategy_factory(
        config=config,
        use_multi=True,
        use_n_tick=bool(ntick_n),
        n_ticks=ntick_n or 0,
    )
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

                if ntick_n:
                    if not getattr(strategy, "_waiting", False):
                        continue
                    confirmed = simulate_ntick_confirmation(
                        strategy, symbol, m1_candles, i, entry_seconds
                    )
                    if confirmed is None:
                        continue
                    outcome, bars = evaluate_signal_from_price(
                        m1_candles, i + 1, confirmed["entry_price"], confirmed["direction"],
                        pip_size, target_pips, stop_pips, forward_bars, spread_pips,
                    )
                    results.append(
                        {
                            "time": m1_candle.get("time"),
                            "direction": confirmed["direction"],
                            "outcome": outcome,
                            "bars": bars,
                        }
                    )
                    continue

                final_signal = (signal.get("final_signal") or "hold").lower()
                if final_signal not in ("buy", "sell"):
                    continue
                if quick_check:
                    move = evaluate_immediate_move(m1_candles, i, final_signal, pip_size, horizon_bars)
                    results.append({"time": m1_candle.get("time"), "direction": final_signal, **move})
                else:
                    outcome, bars = evaluate_signal(
                        m1_candles, i, final_signal, pip_size, target_pips, stop_pips, forward_bars, spread_pips
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

    print(f"Window: start_pos={start_pos}, spread_pips={spread_pips}")
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

    single_tf_test_flag = bool(args.indicators or args.rsi_weight is not None or args.ml_entry)
    if not args.mtf and not single_tf_test_flag and getattr(Config, "USE_MULTI_TIMEFRAME_SIGNALS", False):
        print(
            "USE_MULTI_TIMEFRAME_SIGNALS is on but --mtf wasn't passed -- this "
            "would silently replay the single-timeframe path instead of what's "
            "actually configured. Pass --mtf, or turn the flag off. Aborting."
        )
        sys.exit(1)

    n_tick_active = getattr(Config, "USE_N_TICK_CONFIRMATION", False) and int(
        getattr(Config, "N_TICK_CONFIRMATION", 0) or 0
    ) > 1
    if n_tick_active and not args.ntick:
        print(
            "Config.N_TICK_CONFIRMATION is on but --ntick wasn't passed -- this "
            "would silently replay without n-tick confirmation instead of what's "
            "actually configured. Pass --ntick <N>, or turn the flag off. Aborting."
        )
        sys.exit(1)

    if args.ntick is not None and args.ntick <= 1:
        print("--ntick must be > 1 (that's the threshold at which the wrapper actually engages). Aborting.")
        sys.exit(1)

    if args.ntick and args.quick_check:
        print("--ntick evaluates confirmed entries against a target/stop; not compatible with --quick-check. Aborting.")
        sys.exit(1)

    if args.indicators and args.mtf:
        print("--indicators is a single-timeframe ablation flag, not compatible with --mtf. Aborting.")
        sys.exit(1)

    if args.indicators and args.ml_entry:
        print("--indicators and --ml-entry are mutually exclusive (both override the entry layer). Aborting.")
        sys.exit(1)

    if args.mtf:
        config = Config
        label_parts = []
        overrides = {}
        if args.mtf_score_threshold is not None:
            overrides["MTF_SCORE_THRESHOLD"] = args.mtf_score_threshold
            label_parts.append(f"score{args.mtf_score_threshold}")
        if args.mtf_adx_min_strength is not None:
            overrides["MTF_ADX_MIN_STRENGTH"] = args.mtf_adx_min_strength
            label_parts.append(f"adx{args.mtf_adx_min_strength}")
        if args.session_filter:
            overrides["USE_SESSION_FILTER"] = True
            label_parts.append("sessionfilter")
        if args.ml_entry:
            overrides["USE_ML_ENTRY_MODEL"] = True
            label_parts.append("mlentry")
        if overrides:
            config = type("ConfigOverride", (Config,), overrides)
        run_mtf_backtest(
            symbol, count, args.forward_bars, target_pips, stop_pips,
            quick_check=args.quick_check, horizon_bars=args.horizon_bars,
            config=config, label="_".join(label_parts) or None,
            spread_pips=args.spread_pips, start_pos=args.start_pos,
            ntick_n=args.ntick,
        )
    else:
        indicator_names = (
            [n.strip() for n in args.indicators.split(",") if n.strip()]
            if args.indicators
            else None
        )
        config = Config
        overrides = {}
        if args.rsi_weight is not None:
            overrides["ENTRY_RSI_WEIGHT"] = args.rsi_weight
        if args.session_filter:
            overrides["USE_SESSION_FILTER"] = True
        if args.ml_entry:
            overrides["USE_ML_ENTRY_MODEL"] = True
        if overrides:
            config = type("ConfigOverride", (Config,), overrides)
        run_backtest(
            symbol, count, args.forward_bars, target_pips, stop_pips,
            quick_check=args.quick_check, horizon_bars=args.horizon_bars,
            indicator_names=indicator_names,
            spread_pips=args.spread_pips, start_pos=args.start_pos,
            config=config,
            ntick_n=args.ntick,
        )


if __name__ == "__main__":
    main()
